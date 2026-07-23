"""Integration tests for Step Functions workflow state transitions.

These tests validate the state machine logic by calling the handler function
directly with the expected inputs at each state, verifying that outputs map
correctly to subsequent state inputs, and that decision routing works as
specified in the ASL definition.
"""

import functools
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add lambda directory to path for imports
_lambda_dir = str(Path(__file__).resolve().parent.parent.parent / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

# Mock external dependencies before importing handler
_mock_strands = MagicMock()
_mock_strands.tool = lambda fn: fn
sys.modules["strands"] = _mock_strands
sys.modules["strands.models"] = MagicMock()
sys.modules["strands.models.bedrock"] = MagicMock()


def _fake_metric_scope(fn=None):
    """Fake metric_scope that injects a mock metrics logger like the real one."""
    if fn is not None:

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            # The real metric_scope injects a MetricsLogger as the last positional arg
            kwargs["metrics"] = MagicMock()
            return await fn(*args, **kwargs)

        return wrapper
    return _fake_metric_scope


_mock_emf = MagicMock()
_mock_emf.metric_scope = _fake_metric_scope
sys.modules["aws_embedded_metrics"] = _mock_emf
sys.modules["aws_embedded_metrics.logger"] = MagicMock()
sys.modules["aws_embedded_metrics.logger.metrics_logger"] = MagicMock()
sys.modules["aws_embedded_metrics.unit"] = MagicMock()

from deployment_agent.handler import handler  # noqa: E402, I001


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

SAMPLE_FIRMWARE_URL = "s3://amzn-s3-demo-firmware-bucket/firmware/sensor-v2.0.0.bin"
SAMPLE_DEPLOYMENT_ID = "deploy-abc-123"
SAMPLE_DEVICE_TYPE = "temperature-sensor"
SAMPLE_TARGET_VERSION = "2.0.0"


def _make_plan_result():
    """Return a sample PLAN result matching Step Functions ResultSelector."""
    return {
        "deployment_id": SAMPLE_DEPLOYMENT_ID,
        "wave_plan": [
            {"wave_number": 1, "thing_names": ["device-001", "device-002"]},
            {"wave_number": 2, "thing_names": ["device-003", "device-004", "device-005"]},
        ],
        "total_devices": 5,
        "status": "PLANNING",
    }


def _make_create_wave_result(wave_number=1):
    """Return a sample CREATE_WAVE result matching ResultSelector."""
    return {
        "job_id": f"fw-deploy-{SAMPLE_DEPLOYMENT_ID}-wave-{wave_number}",
        "job_arn": f"arn:aws:iot:us-east-1:123456789012:job/fw-deploy-{SAMPLE_DEPLOYMENT_ID}-wave-{wave_number}",
        "target_count": 2,
    }


def _make_check_wave_status_result(is_complete=True):
    """Return a sample CHECK_WAVE_STATUS result matching ResultSelector."""
    return {
        "is_complete": is_complete,
        "summary": {
            "total_devices": 2,
            "succeeded_count": 2,
            "failed_count": 0,
            "timed_out_count": 0,
            "in_progress_count": 0,
            "success_rate": 100.0,
            "failure_types": {
                "boot_loop": 0,
                "connectivity_lost": 0,
                "version_mismatch": 0,
                "timeout": 0,
            },
        },
    }


def _make_assess_result(decision="PROCEED", success_rate=100.0, pause_count=0):
    """Return a sample ASSESS result matching ResultSelector."""
    return {
        "decision": decision,
        "reasoning": f"Decision: {decision} with success rate {success_rate}%",
        "success_rate": success_rate,
        "failure_types": {"boot_loop": 0, "connectivity_lost": 0, "version_mismatch": 0, "timeout": 0},
        "pause_count": pause_count,
    }


def _make_rollback_result():
    """Return a sample ROLLBACK result matching ResultSelector."""
    return {
        "rollback_job_id": f"rollback-fw-deploy-{SAMPLE_DEPLOYMENT_ID}-wave-1",
        "rollback_job_arn": "arn:aws:iot:us-east-1:123456789012:job/rollback-fw-deploy-deploy-abc-123-wave-1",
        "target_count": 1,
        "cancelled_job_id": f"fw-deploy-{SAMPLE_DEPLOYMENT_ID}-wave-1",
    }


# ---------------------------------------------------------------------------
# Test class: Happy Path
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestStepFunctionsHappyPath:
    """Tests for the complete successful deployment flow."""

    @patch("deployment_agent.handler.create_agent")
    def test_plan_produces_expected_output_structure(self, mock_create_agent):
        """PLAN action returns deployment_id, wave_plan, total_devices, status."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = (
            '{"action":"PLAN","reasoning":"planned","details":{'
            '"deployment_id":"deploy-abc-123",'
            '"total_eligible_devices":5,'
            '"waves":[{"wave_number":1,"thing_names":["device-001","device-002"]},'
            '{"wave_number":2,"thing_names":["device-003","device-004","device-005"]}]}}'
        )
        mock_create_agent.return_value = mock_agent_instance

        event = {
            "action": "PLAN",
            "firmware_s3_url": SAMPLE_FIRMWARE_URL,
            "target_version": SAMPLE_TARGET_VERSION,
            "device_type": SAMPLE_DEVICE_TYPE,
        }
        result = handler(event)

        assert "deployment_id" in result
        assert "wave_plan" in result
        assert "total_devices" in result
        assert "status" in result
        assert result["status"] == "PLANNING"

    @patch("deployment_agent.handler.create_agent")
    def test_plan_to_create_wave_input_transformation(self, mock_create_agent):
        """PLAN output correctly feeds into CREATE_WAVE input per ASL definition.

        The ASL maps plan_result.wave_plan items into the Map iterator
        with deployment_id, wave data (wave_number, thing_names), and firmware_s3_url.
        """
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = (
            '{"action":"PLAN","reasoning":"planned","details":{'
            '"deployment_id":"deploy-abc-123",'
            '"total_eligible_devices":5,'
            '"waves":[{"wave_number":1,"thing_names":["device-001","device-002"]}]}}'
        )
        mock_create_agent.return_value = mock_agent_instance

        plan_event = {
            "action": "PLAN",
            "firmware_s3_url": SAMPLE_FIRMWARE_URL,
            "target_version": SAMPLE_TARGET_VERSION,
            "device_type": SAMPLE_DEVICE_TYPE,
        }
        plan_result = handler(plan_event)

        # Simulate ASL ExecuteWaveIterator parameters mapping
        wave = plan_result["wave_plan"][0]
        create_wave_event = {
            "action": "CREATE_WAVE",
            "deployment_id": plan_result["deployment_id"],
            "wave_number": wave["wave_number"],
            "thing_names": wave["thing_names"],
            "firmware_s3_url": SAMPLE_FIRMWARE_URL,
            "timeout_minutes": 30,
        }

        # Verify the input structure matches what handler expects
        assert create_wave_event["action"] == "CREATE_WAVE"
        assert create_wave_event["deployment_id"].startswith("deploy-")
        assert create_wave_event["wave_number"] == 1
        assert create_wave_event["thing_names"] == ["device-001", "device-002"]

    @patch("deployment_agent.handler.get_wave_health")
    @patch("deployment_agent.handler.create_deployment_wave")
    @patch("deployment_agent.handler.create_agent")
    def test_complete_happy_path_flow(self, mock_create_agent, mock_create_wave, mock_get_health):
        """Simulates full deployment: PLAN -> (CREATE_WAVE -> CHECK -> ASSESS PROCEED) * N waves."""
        # Setup PLAN mock
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = (
            '{"action":"PLAN","reasoning":"planned","details":{'
            '"deployment_id":"deploy-abc-123",'
            '"total_eligible_devices":5,'
            '"waves":[{"wave_number":1,"thing_names":["device-001","device-002"]},'
            '{"wave_number":2,"thing_names":["device-003","device-004","device-005"]}]}}'
        )
        mock_create_agent.return_value = mock_agent_instance

        # Step 1: PLAN
        plan_result = handler(
            {
                "action": "PLAN",
                "firmware_s3_url": SAMPLE_FIRMWARE_URL,
                "target_version": SAMPLE_TARGET_VERSION,
                "device_type": SAMPLE_DEVICE_TYPE,
            }
        )
        assert plan_result["deployment_id"].startswith("deploy-")
        assert len(plan_result["wave_plan"]) == 2

        # Step 2: Iterate each wave
        for wave in plan_result["wave_plan"]:
            wave_number = wave["wave_number"]

            # CREATE_WAVE
            mock_create_wave.return_value = _make_create_wave_result(wave_number)
            create_result = handler(
                {
                    "action": "CREATE_WAVE",
                    "deployment_id": plan_result["deployment_id"],
                    "wave_number": wave_number,
                    "thing_names": wave["thing_names"],
                    "firmware_s3_url": SAMPLE_FIRMWARE_URL,
                    "timeout_minutes": 30,
                }
            )
            assert "job_id" in create_result
            assert "job_arn" in create_result
            assert "target_count" in create_result

            # CHECK_WAVE_STATUS (complete)
            mock_get_health.return_value = {
                "total_devices": len(wave["thing_names"]),
                "succeeded_count": len(wave["thing_names"]),
                "failed_count": 0,
                "timed_out_count": 0,
                "in_progress_count": 0,
                "success_rate": 100.0,
                "failure_types": {
                    "boot_loop": 0,
                    "connectivity_lost": 0,
                    "version_mismatch": 0,
                    "timeout": 0,
                },
            }
            check_result = handler(
                {
                    "action": "CHECK_WAVE_STATUS",
                    "job_id": create_result["job_id"],
                }
            )
            assert check_result["is_complete"] is True

            # ASSESS (PROCEED)
            mock_agent_instance.return_value = (
                '{"action":"ASSESS","reasoning":"all devices succeeded",'
                '"details":{"decision":"PROCEED","success_rate":100.0,'
                '"failure_types":{"boot_loop":0,"connectivity_lost":0,'
                '"version_mismatch":0,"timeout":0},"pause_count":0}}'
            )
            assess_result = handler(
                {
                    "action": "ASSESS",
                    "deployment_id": plan_result["deployment_id"],
                    "wave_number": wave_number,
                    "job_id": create_result["job_id"],
                }
            )
            assert assess_result["decision"] == "PROCEED"
            assert assess_result["success_rate"] == 100.0

        # All waves PROCEED means deployment complete
        assert assess_result["decision"] == "PROCEED"


# ---------------------------------------------------------------------------
# Test class: PAUSE Path
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestStepFunctionsPausePath:
    """Tests for PAUSE decision handling."""

    @patch("deployment_agent.handler.create_agent")
    def test_pause_decision_routes_to_pause_and_retry(self, mock_create_agent):
        """ASSESS returning PAUSE routes to PauseAndRetry state (Wait 10min -> re-ASSESS)."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = (
            '{"action":"ASSESS","reasoning":"connectivity issues, pausing",'
            '"details":{"decision":"PAUSE","success_rate":96.5,'
            '"failure_types":{"boot_loop":0,"connectivity_lost":2,'
            '"version_mismatch":0,"timeout":0},"pause_count":1}}'
        )
        mock_create_agent.return_value = mock_agent_instance

        assess_result = handler(
            {
                "action": "ASSESS",
                "deployment_id": SAMPLE_DEPLOYMENT_ID,
                "wave_number": 1,
                "job_id": "fw-deploy-deploy-abc-123-wave-1",
            }
        )

        assert assess_result["decision"] == "PAUSE"
        assert assess_result["pause_count"] == 1
        # In the ASL, this routes to CheckRetryCount -> PauseAndRetry (Wait 600s)
        # then back to AssessWaveHealth
        assert assess_result["pause_count"] < 3  # Not yet at escalation threshold

    @patch("deployment_agent.handler.create_agent")
    def test_pause_then_proceed_on_retry(self, mock_create_agent):
        """PAUSE -> Wait 10min -> re-ASSESS -> PROCEED (success on retry)."""
        mock_agent_instance = MagicMock()
        mock_create_agent.return_value = mock_agent_instance

        # First ASSESS: PAUSE
        mock_agent_instance.return_value = (
            '{"action":"ASSESS","reasoning":"connectivity issues",'
            '"details":{"decision":"PAUSE","success_rate":96.0,'
            '"failure_types":{"boot_loop":0,"connectivity_lost":2,'
            '"version_mismatch":0,"timeout":0},"pause_count":1}}'
        )
        first_assess = handler(
            {
                "action": "ASSESS",
                "deployment_id": SAMPLE_DEPLOYMENT_ID,
                "wave_number": 1,
                "job_id": "fw-deploy-deploy-abc-123-wave-1",
            }
        )
        assert first_assess["decision"] == "PAUSE"
        assert first_assess["pause_count"] == 1

        # After 10-minute wait, re-ASSESS: PROCEED (devices recovered)
        mock_agent_instance.return_value = (
            '{"action":"ASSESS","reasoning":"devices recovered",'
            '"details":{"decision":"PROCEED","success_rate":99.0,'
            '"failure_types":{"boot_loop":0,"connectivity_lost":0,'
            '"version_mismatch":0,"timeout":0},"pause_count":1}}'
        )
        second_assess = handler(
            {
                "action": "ASSESS",
                "deployment_id": SAMPLE_DEPLOYMENT_ID,
                "wave_number": 1,
                "job_id": "fw-deploy-deploy-abc-123-wave-1",
            }
        )
        assert second_assess["decision"] == "PROCEED"
        assert second_assess["success_rate"] == 99.0


# ---------------------------------------------------------------------------
# Test class: ROLLBACK Path
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestStepFunctionsRollbackPath:
    """Tests for ROLLBACK decision handling."""

    @patch("deployment_agent.handler.create_agent")
    def test_assess_rollback_routes_to_execute_rollback(self, mock_create_agent):
        """ASSESS returning ROLLBACK routes to ExecuteRollback state."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = (
            '{"action":"ASSESS","reasoning":"boot-loop detected",'
            '"details":{"decision":"ROLLBACK","success_rate":90.0,'
            '"failure_types":{"boot_loop":1,"connectivity_lost":0,'
            '"version_mismatch":0,"timeout":0},"pause_count":0}}'
        )
        mock_create_agent.return_value = mock_agent_instance

        assess_result = handler(
            {
                "action": "ASSESS",
                "deployment_id": SAMPLE_DEPLOYMENT_ID,
                "wave_number": 1,
                "job_id": "fw-deploy-deploy-abc-123-wave-1",
            }
        )
        assert assess_result["decision"] == "ROLLBACK"
        # In the ASL, DecisionBranch routes ROLLBACK to ExecuteRollback

    @patch("deployment_agent.handler.create_agent")
    def test_rollback_execution_produces_expected_output(self, mock_create_agent):
        """ExecuteRollback state invokes handler with action=ROLLBACK and returns rollback details."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = (
            '{"action":"ROLLBACK","reasoning":"boot-loop triggered rollback",'
            '"details":{"rollback_job_id":"rollback-fw-deploy-deploy-abc-123-wave-1",'
            '"rollback_job_arn":"arn:aws:iot:us-east-1:123456789012:job/rollback",'
            '"target_count":2,"cancelled_job_id":"fw-deploy-deploy-abc-123-wave-1"}}'
        )
        mock_create_agent.return_value = mock_agent_instance

        # The ASL ExecuteRollback state payload
        rollback_event = {
            "action": "ROLLBACK",
            "deployment_id": SAMPLE_DEPLOYMENT_ID,
            "job_id": "fw-deploy-deploy-abc-123-wave-1",
            "failed_thing_names": ["device-001", "device-002"],
            "previous_firmware_s3_url": "s3://amzn-s3-demo-firmware-bucket/firmware/sensor-v1.0.0.bin",
        }
        rollback_result = handler(rollback_event)

        assert "rollback_job_id" in rollback_result
        assert "rollback_job_arn" in rollback_result
        assert "target_count" in rollback_result
        assert "cancelled_job_id" in rollback_result

    @patch("deployment_agent.handler.create_agent")
    def test_rollback_to_wave_failed_terminal_state(self, mock_create_agent):
        """After ExecuteRollback, the ASL transitions to WaveFailed (Fail state).

        This test verifies the complete ASSESS ROLLBACK -> ExecuteRollback sequence
        and that the output of ROLLBACK contains the info needed for NotifyOperator.
        """
        mock_agent_instance = MagicMock()
        mock_create_agent.return_value = mock_agent_instance

        # ASSESS returns ROLLBACK
        mock_agent_instance.return_value = (
            '{"action":"ASSESS","reasoning":"boot-loop detected",'
            '"details":{"decision":"ROLLBACK","success_rate":85.0,'
            '"failure_types":{"boot_loop":2,"connectivity_lost":0,'
            '"version_mismatch":0,"timeout":0},"pause_count":0}}'
        )
        assess_result = handler(
            {
                "action": "ASSESS",
                "deployment_id": SAMPLE_DEPLOYMENT_ID,
                "wave_number": 1,
                "job_id": "fw-deploy-deploy-abc-123-wave-1",
            }
        )
        assert assess_result["decision"] == "ROLLBACK"

        # Execute rollback
        mock_agent_instance.return_value = (
            '{"action":"ROLLBACK","reasoning":"executing rollback for boot-loop",'
            '"details":{"rollback_job_id":"rollback-fw-deploy-deploy-abc-123-wave-1",'
            '"rollback_job_arn":"arn:aws:iot:us-east-1:123456789012:job/rollback",'
            '"target_count":2,"cancelled_job_id":"fw-deploy-deploy-abc-123-wave-1"}}'
        )
        rollback_result = handler(
            {
                "action": "ROLLBACK",
                "deployment_id": SAMPLE_DEPLOYMENT_ID,
                "job_id": "fw-deploy-deploy-abc-123-wave-1",
                "failed_thing_names": ["device-001", "device-002"],
                "previous_firmware_s3_url": "s3://amzn-s3-demo-firmware-bucket/firmware/sensor-v1.0.0.bin",
            }
        )
        assert rollback_result["cancelled_job_id"] == "fw-deploy-deploy-abc-123-wave-1"
        assert rollback_result["target_count"] == 2


# ---------------------------------------------------------------------------
# Test class: Max Retries Escalation
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestStepFunctionsMaxRetries:
    """Tests for max retries escalation (3 PAUSEs -> ROLLBACK)."""

    @patch("deployment_agent.handler.create_agent")
    def test_three_consecutive_pauses_escalates_to_rollback(self, mock_create_agent):
        """3 consecutive PAUSE decisions -> automatic ROLLBACK via CheckRetryCount.

        The ASL CheckRetryCount state checks if pause_count >= 3 and routes
        to ExecuteRollback.
        """
        mock_agent_instance = MagicMock()
        mock_create_agent.return_value = mock_agent_instance

        decisions = []

        # First PAUSE (pause_count=1)
        mock_agent_instance.return_value = (
            '{"action":"ASSESS","reasoning":"connectivity issue, attempt 1",'
            '"details":{"decision":"PAUSE","success_rate":96.0,'
            '"failure_types":{"boot_loop":0,"connectivity_lost":2,'
            '"version_mismatch":0,"timeout":0},"pause_count":1}}'
        )
        result = handler(
            {
                "action": "ASSESS",
                "deployment_id": SAMPLE_DEPLOYMENT_ID,
                "wave_number": 1,
                "job_id": "fw-deploy-deploy-abc-123-wave-1",
            }
        )
        decisions.append(result["decision"])
        assert result["decision"] == "PAUSE"
        assert result["pause_count"] < 3

        # Second PAUSE (pause_count=2)
        mock_agent_instance.return_value = (
            '{"action":"ASSESS","reasoning":"connectivity issue, attempt 2",'
            '"details":{"decision":"PAUSE","success_rate":96.0,'
            '"failure_types":{"boot_loop":0,"connectivity_lost":2,'
            '"version_mismatch":0,"timeout":0},"pause_count":2}}'
        )
        result = handler(
            {
                "action": "ASSESS",
                "deployment_id": SAMPLE_DEPLOYMENT_ID,
                "wave_number": 1,
                "job_id": "fw-deploy-deploy-abc-123-wave-1",
            }
        )
        decisions.append(result["decision"])
        assert result["decision"] == "PAUSE"
        assert result["pause_count"] < 3

        # Third PAUSE (pause_count=3) -> escalates to ROLLBACK in ASL
        mock_agent_instance.return_value = (
            '{"action":"ASSESS","reasoning":"3 pauses reached, escalating",'
            '"details":{"decision":"PAUSE","success_rate":96.0,'
            '"failure_types":{"boot_loop":0,"connectivity_lost":2,'
            '"version_mismatch":0,"timeout":0},"pause_count":3}}'
        )
        result = handler(
            {
                "action": "ASSESS",
                "deployment_id": SAMPLE_DEPLOYMENT_ID,
                "wave_number": 1,
                "job_id": "fw-deploy-deploy-abc-123-wave-1",
            }
        )
        decisions.append(result["decision"])

        # The handler returns PAUSE, but the ASL CheckRetryCount state
        # checks pause_count >= 3 and routes to ExecuteRollback
        assert result["pause_count"] >= 3
        # Verify the ASL routing logic: pause_count >= 3 -> ExecuteRollback
        should_escalate = result["pause_count"] >= 3
        assert should_escalate is True
        assert all(d == "PAUSE" for d in decisions)

    @patch("deployment_agent.handler.create_agent")
    def test_pause_count_below_three_does_not_escalate(self, mock_create_agent):
        """pause_count < 3 does not trigger ROLLBACK escalation."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = (
            '{"action":"ASSESS","reasoning":"connectivity issue",'
            '"details":{"decision":"PAUSE","success_rate":96.5,'
            '"failure_types":{"boot_loop":0,"connectivity_lost":1,'
            '"version_mismatch":0,"timeout":0},"pause_count":2}}'
        )
        mock_create_agent.return_value = mock_agent_instance

        result = handler(
            {
                "action": "ASSESS",
                "deployment_id": SAMPLE_DEPLOYMENT_ID,
                "wave_number": 1,
                "job_id": "fw-deploy-deploy-abc-123-wave-1",
            }
        )

        assert result["decision"] == "PAUSE"
        assert result["pause_count"] == 2
        # ASL CheckRetryCount: pause_count < 3 -> Default -> PauseAndRetry
        should_escalate = result["pause_count"] >= 3
        assert should_escalate is False


# ---------------------------------------------------------------------------
# Test class: Error Handling with Retries
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestStepFunctionsErrorHandling:
    """Tests for error handling and retry behavior."""

    def test_unknown_action_raises_value_error(self):
        """Unknown action routes to error (Catch -> NotifyOperator in ASL)."""
        with pytest.raises(ValueError, match="Unknown action: INVALID"):
            handler({"action": "INVALID"})

    def test_missing_action_raises_value_error(self):
        """Missing action raises ValueError caught by Step Functions Catch."""
        with pytest.raises(ValueError, match="Unknown action: None"):
            handler({})

    @patch("deployment_agent.handler.create_agent")
    def test_plan_with_unparseable_agent_response(self, mock_create_agent):
        """PLAN action handles non-JSON agent responses gracefully.

        Step Functions retry logic would retry on Lambda failure, but the
        handler should not crash on malformed agent output.
        """
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = "This is not valid JSON at all"
        mock_create_agent.return_value = mock_agent_instance

        result = handler(
            {
                "action": "PLAN",
                "firmware_s3_url": SAMPLE_FIRMWARE_URL,
                "target_version": SAMPLE_TARGET_VERSION,
                "device_type": SAMPLE_DEVICE_TYPE,
            }
        )
        # Handler falls back gracefully
        assert "deployment_id" in result
        assert "wave_plan" in result
        assert "status" in result

    @patch("deployment_agent.handler.create_agent")
    def test_assess_with_agent_exception_propagates(self, mock_create_agent):
        """Lambda invocation failure propagates for Step Functions retry.

        Step Functions retries with MaxAttempts=2 and BackoffRate=2.0
        on Lambda.ServiceException and States.TaskFailed.
        """
        mock_agent_instance = MagicMock()
        mock_agent_instance.side_effect = RuntimeError("Bedrock throttle")
        mock_create_agent.return_value = mock_agent_instance

        with pytest.raises(RuntimeError, match="Bedrock throttle"):
            handler(
                {
                    "action": "ASSESS",
                    "deployment_id": SAMPLE_DEPLOYMENT_ID,
                    "wave_number": 1,
                    "job_id": "fw-deploy-deploy-abc-123-wave-1",
                }
            )

    @patch("deployment_agent.handler.get_wave_health")
    def test_check_wave_status_with_service_error_propagates(self, mock_get_health):
        """CHECK_WAVE_STATUS service error propagates for Step Functions retry."""
        mock_get_health.side_effect = Exception("IoT service unavailable")

        with pytest.raises(Exception, match="IoT service unavailable"):
            handler(
                {
                    "action": "CHECK_WAVE_STATUS",
                    "job_id": "fw-deploy-deploy-abc-123-wave-1",
                }
            )

    @patch("deployment_agent.handler.create_agent")
    def test_retry_succeeds_on_second_attempt(self, mock_create_agent):
        """Simulates retry behavior: first call fails, second succeeds.

        Step Functions retries the Lambda invocation. This test verifies the
        handler works correctly when called again after a transient failure.
        """
        mock_agent_instance = MagicMock()
        mock_create_agent.return_value = mock_agent_instance

        # First attempt: failure
        mock_agent_instance.side_effect = RuntimeError("Transient error")
        with pytest.raises(RuntimeError):
            handler(
                {
                    "action": "ASSESS",
                    "deployment_id": SAMPLE_DEPLOYMENT_ID,
                    "wave_number": 1,
                    "job_id": "fw-deploy-deploy-abc-123-wave-1",
                }
            )

        # Second attempt: success (Step Functions retry)
        mock_agent_instance.side_effect = None
        mock_agent_instance.return_value = (
            '{"action":"ASSESS","reasoning":"recovered",'
            '"details":{"decision":"PROCEED","success_rate":99.0,'
            '"failure_types":{"boot_loop":0,"connectivity_lost":0,'
            '"version_mismatch":0,"timeout":0},"pause_count":0}}'
        )
        result = handler(
            {
                "action": "ASSESS",
                "deployment_id": SAMPLE_DEPLOYMENT_ID,
                "wave_number": 1,
                "job_id": "fw-deploy-deploy-abc-123-wave-1",
            }
        )
        assert result["decision"] == "PROCEED"


# ---------------------------------------------------------------------------
# Test class: Input/Output Transformations
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestStepFunctionsInputOutputTransformations:
    """Tests verifying data flows correctly between states."""

    @patch("deployment_agent.handler.create_agent")
    def test_plan_output_feeds_execute_wave_iterator(self, mock_create_agent):
        """PLAN output (wave_plan list) feeds into ExecuteWaveIterator Map ItemsPath.

        ASL: ExecuteWaveIterator.ItemsPath = $.plan_result.wave_plan
        Each item becomes the 'wave' parameter in the Map iteration.
        """
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = (
            '{"action":"PLAN","reasoning":"planned",'
            '"details":{"deployment_id":"deploy-abc-123",'
            '"total_eligible_devices":5,'
            '"waves":[{"wave_number":1,"wave_type":"CANARY","thing_names":["d-001","d-002"],"target_count":2},'
            '{"wave_number":2,"wave_type":"FULL_ROLLOUT","thing_names":["d-003","d-004","d-005"],"target_count":3}]}}'
        )
        mock_create_agent.return_value = mock_agent_instance

        plan_result = handler(
            {
                "action": "PLAN",
                "firmware_s3_url": SAMPLE_FIRMWARE_URL,
                "target_version": SAMPLE_TARGET_VERSION,
                "device_type": SAMPLE_DEVICE_TYPE,
            }
        )

        # The ASL ResultSelector extracts wave_plan from Payload
        wave_plan = plan_result["wave_plan"]
        assert isinstance(wave_plan, list)
        assert len(wave_plan) == 2

        # Each wave in the plan has the fields needed by CreateIoTJob
        for wave in wave_plan:
            assert "wave_number" in wave
            assert "thing_names" in wave
            assert isinstance(wave["thing_names"], list)
            assert len(wave["thing_names"]) > 0

    @patch("deployment_agent.handler.create_deployment_wave")
    def test_wave_data_feeds_into_create_wave(self, mock_create_wave):
        """Wave data from Map iterator feeds into CREATE_WAVE as expected.

        ASL CreateIoTJob.Parameters:
          action: CREATE_WAVE
          deployment_id.$: $.deployment_id
          wave_number.$: $.wave.wave_number
          thing_names.$: $.wave.thing_names
          firmware_s3_url.$: $.firmware_s3_url
          timeout_minutes: 30
        """
        mock_create_wave.return_value = {
            "job_id": "fw-deploy-deploy-abc-123-wave-1",
            "job_arn": "arn:aws:iot:us-east-1:123456789012:job/fw-deploy-deploy-abc-123-wave-1",
            "target_count": 2,
        }

        # Simulate the Map iterator passing wave data
        create_wave_event = {
            "action": "CREATE_WAVE",
            "deployment_id": SAMPLE_DEPLOYMENT_ID,
            "wave_number": 1,
            "thing_names": ["device-001", "device-002"],
            "firmware_s3_url": SAMPLE_FIRMWARE_URL,
            "timeout_minutes": 30,
        }
        result = handler(create_wave_event)

        assert result["job_id"] == "fw-deploy-deploy-abc-123-wave-1"
        assert result["target_count"] == 2
        # Verify create_deployment_wave was called with correct args
        mock_create_wave.assert_called_once_with(
            deployment_id=SAMPLE_DEPLOYMENT_ID,
            wave_number=1,
            thing_names=["device-001", "device-002"],
            firmware_s3_url=SAMPLE_FIRMWARE_URL,
            timeout_minutes=30,
        )

    @patch("deployment_agent.handler.get_wave_health")
    def test_create_wave_output_feeds_into_check_status(self, mock_get_health):
        """CREATE_WAVE output (job_id) feeds into CHECK_WAVE_STATUS.

        ASL CheckWaveStatus.Parameters:
          action: CHECK_WAVE_STATUS
          job_id.$: $.job_result.job_id
        """
        mock_get_health.return_value = {
            "total_devices": 2,
            "succeeded_count": 2,
            "failed_count": 0,
            "timed_out_count": 0,
            "in_progress_count": 0,
            "success_rate": 100.0,
            "failure_types": {
                "boot_loop": 0,
                "connectivity_lost": 0,
                "version_mismatch": 0,
                "timeout": 0,
            },
        }

        # Simulate: job_result from CREATE_WAVE feeds job_id to CHECK_WAVE_STATUS
        job_result = _make_create_wave_result(1)
        check_event = {
            "action": "CHECK_WAVE_STATUS",
            "job_id": job_result["job_id"],
        }
        result = handler(check_event)

        assert result["is_complete"] is True
        assert "summary" in result
        mock_get_health.assert_called_once_with(job_id="fw-deploy-deploy-abc-123-wave-1")

    @patch("deployment_agent.handler.get_wave_health")
    def test_check_status_incomplete_routes_back_to_wait(self, mock_get_health):
        """CHECK_WAVE_STATUS with is_complete=False routes to WaitForWaveCompletion.

        ASL IsWaveComplete Choice: if is_complete=false -> Default -> WaitForWaveCompletion
        """
        mock_get_health.return_value = {
            "total_devices": 2,
            "succeeded_count": 1,
            "failed_count": 0,
            "timed_out_count": 0,
            "in_progress_count": 1,
            "success_rate": 50.0,
            "failure_types": {
                "boot_loop": 0,
                "connectivity_lost": 0,
                "version_mismatch": 0,
                "timeout": 0,
            },
        }

        result = handler(
            {
                "action": "CHECK_WAVE_STATUS",
                "job_id": "fw-deploy-deploy-abc-123-wave-1",
            }
        )

        assert result["is_complete"] is False
        # ASL routes back to WaitForWaveCompletion (Wait 30s) then CheckWaveStatus again

    @patch("deployment_agent.handler.create_agent")
    def test_assess_output_feeds_into_decision_branch(self, mock_create_agent):
        """ASSESS output (decision, pause_count) feeds into DecisionBranch Choice.

        ASL DecisionBranch checks:
          $.assess_result.decision == "PROCEED" -> WaveSucceeded
          $.assess_result.decision == "PAUSE" -> CheckRetryCount
          $.assess_result.decision == "ROLLBACK" -> ExecuteRollback

        CheckRetryCount checks:
          $.assess_result.pause_count >= 3 -> ExecuteRollback
          Default -> PauseAndRetry
        """
        mock_agent_instance = MagicMock()
        mock_create_agent.return_value = mock_agent_instance

        # Test PROCEED routing
        mock_agent_instance.return_value = (
            '{"action":"ASSESS","reasoning":"healthy",'
            '"details":{"decision":"PROCEED","success_rate":99.5,'
            '"failure_types":{"boot_loop":0,"connectivity_lost":0,'
            '"version_mismatch":0,"timeout":0},"pause_count":0}}'
        )
        result = handler(
            {
                "action": "ASSESS",
                "deployment_id": SAMPLE_DEPLOYMENT_ID,
                "wave_number": 1,
                "job_id": "fw-deploy-deploy-abc-123-wave-1",
            }
        )
        assert result["decision"] == "PROCEED"
        # ASL routes to WaveSucceeded (Succeed state)

    @patch("deployment_agent.handler.create_agent")
    def test_rollback_input_constructed_from_wave_state(self, mock_create_agent):
        """ExecuteRollback state receives input from accumulated wave state.

        ASL ExecuteRollback.Parameters:
          action: ROLLBACK
          deployment_id.$: $.deployment_id
          job_id.$: $.job_result.job_id
          failed_thing_names.$: $.wave.thing_names
          previous_firmware_s3_url.$: $.firmware_s3_url
        """
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = (
            '{"action":"ROLLBACK","reasoning":"rolling back",'
            '"details":{"rollback_job_id":"rollback-fw-deploy-deploy-abc-123-wave-1",'
            '"rollback_job_arn":"arn:aws:iot:us-east-1:123456789012:job/rollback",'
            '"target_count":2,"cancelled_job_id":"fw-deploy-deploy-abc-123-wave-1"}}'
        )
        mock_create_agent.return_value = mock_agent_instance

        # Simulate ASL accumulated state feeding into ExecuteRollback
        wave_state = {
            "deployment_id": SAMPLE_DEPLOYMENT_ID,
            "wave": {"wave_number": 1, "thing_names": ["device-001", "device-002"]},
            "job_result": {"job_id": "fw-deploy-deploy-abc-123-wave-1"},
            "firmware_s3_url": SAMPLE_FIRMWARE_URL,
        }

        # Build the ROLLBACK event as the ASL would
        rollback_event = {
            "action": "ROLLBACK",
            "deployment_id": wave_state["deployment_id"],
            "job_id": wave_state["job_result"]["job_id"],
            "failed_thing_names": wave_state["wave"]["thing_names"],
            "previous_firmware_s3_url": wave_state["firmware_s3_url"],
        }

        result = handler(rollback_event)
        assert result["rollback_job_id"] == "rollback-fw-deploy-deploy-abc-123-wave-1"
        assert result["cancelled_job_id"] == "fw-deploy-deploy-abc-123-wave-1"
        assert result["target_count"] == 2

    @patch("deployment_agent.handler.create_agent")
    def test_assess_input_constructed_from_wave_state(self, mock_create_agent):
        """AssessWaveHealth state receives input from accumulated wave state.

        ASL AssessWaveHealth.Parameters:
          action: ASSESS
          deployment_id.$: $.deployment_id
          wave_number.$: $.wave.wave_number
          job_id.$: $.job_result.job_id
        """
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = (
            '{"action":"ASSESS","reasoning":"assessing",'
            '"details":{"decision":"PROCEED","success_rate":99.0,'
            '"failure_types":{"boot_loop":0,"connectivity_lost":0,'
            '"version_mismatch":0,"timeout":0},"pause_count":0}}'
        )
        mock_create_agent.return_value = mock_agent_instance

        # Simulate ASL accumulated state
        wave_state = {
            "deployment_id": SAMPLE_DEPLOYMENT_ID,
            "wave": {"wave_number": 2, "thing_names": ["device-003", "device-004"]},
            "job_result": {"job_id": "fw-deploy-deploy-abc-123-wave-2"},
        }

        # Build the ASSESS event as the ASL would
        assess_event = {
            "action": "ASSESS",
            "deployment_id": wave_state["deployment_id"],
            "wave_number": wave_state["wave"]["wave_number"],
            "job_id": wave_state["job_result"]["job_id"],
        }

        result = handler(assess_event)
        assert result["decision"] == "PROCEED"
        assert "success_rate" in result
        assert "failure_types" in result
        assert "pause_count" in result
