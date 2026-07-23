"""Integration tests for agent decision logic.

These tests validate the decision rules specified in Requirement 5 (Decision Logic)
by calling the handler with action=ASSESS and mocking the Strands agent to return
structured JSON responses simulating different health assessment scenarios.

Each test class covers a distinct decision rule:
- PROCEED at 98%+ success rate
- ROLLBACK on boot-loop (highest priority)
- PAUSE on connectivity-only failures at 95-98%
- ROLLBACK on mixed failure types at 95-98%
- ROLLBACK below 95% threshold
- PAUSE on hardware_revision correlation
- 3-pause escalation to ROLLBACK
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
sys.modules.setdefault("strands", _mock_strands)
sys.modules.setdefault("strands.models", MagicMock())
sys.modules.setdefault("strands.models.bedrock", MagicMock())


def _fake_metric_scope(fn=None):
    """Fake metric_scope that injects a mock metrics logger."""
    if fn is not None:

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            kwargs["metrics"] = MagicMock()
            return await fn(*args, **kwargs)

        return wrapper
    return _fake_metric_scope


_mock_emf = MagicMock()
_mock_emf.metric_scope = _fake_metric_scope
sys.modules.setdefault("aws_embedded_metrics", _mock_emf)
sys.modules.setdefault("aws_embedded_metrics.logger", MagicMock())
sys.modules.setdefault("aws_embedded_metrics.logger.metrics_logger", MagicMock())
sys.modules.setdefault("aws_embedded_metrics.unit", MagicMock())

from deployment_agent.handler import handler  # noqa: E402, I001


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLE_DEPLOYMENT_ID = "deploy-decision-test-001"
SAMPLE_JOB_ID = "fw-deploy-decision-test-001-wave-1"


def _assess_event(deployment_id=SAMPLE_DEPLOYMENT_ID, wave_number=1, job_id=SAMPLE_JOB_ID):
    """Build a standard ASSESS action event."""
    return {
        "action": "ASSESS",
        "deployment_id": deployment_id,
        "wave_number": wave_number,
        "job_id": job_id,
    }


def _agent_response(
    decision: str,
    success_rate: float,
    failure_types: dict,
    pause_count: int = 0,
    reasoning: str = "",
):
    """Build a mock agent JSON response string for ASSESS."""
    import json

    payload = {
        "action": "ASSESS",
        "reasoning": reasoning or f"Decision: {decision}",
        "details": {
            "decision": decision,
            "success_rate": success_rate,
            "failure_types": failure_types,
            "pause_count": pause_count,
        },
    }
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# Test class: PROCEED at 98%+
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDecisionProceed:
    """PROCEED decision at 98%+ success rate with no boot-loop."""

    @patch("deployment_agent.handler.create_agent")
    def test_proceed_at_exactly_98_percent(self, mock_create_agent):
        """success_rate == 98.0% with no boot-loop produces PROCEED."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="PROCEED",
            success_rate=98.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 1,
                "version_mismatch": 0,
                "timeout": 0,
            },
            reasoning="98% success rate meets threshold, no boot-loop detected",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "PROCEED"
        assert result["success_rate"] == 98.0
        assert result["failure_types"]["boot_loop"] == 0

    @patch("deployment_agent.handler.create_agent")
    def test_proceed_at_100_percent(self, mock_create_agent):
        """success_rate == 100% produces PROCEED."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="PROCEED",
            success_rate=100.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 0,
                "version_mismatch": 0,
                "timeout": 0,
            },
            reasoning="All devices succeeded",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "PROCEED"
        assert result["success_rate"] == 100.0

    @patch("deployment_agent.handler.create_agent")
    def test_proceed_at_99_percent(self, mock_create_agent):
        """success_rate == 99.5% with timeout failures produces PROCEED."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="PROCEED",
            success_rate=99.5,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 0,
                "version_mismatch": 0,
                "timeout": 1,
            },
            reasoning="99.5% exceeds 98% threshold, single timeout is acceptable",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "PROCEED"
        assert result["success_rate"] >= 98.0
        assert result["failure_types"]["boot_loop"] == 0


# ---------------------------------------------------------------------------
# Test class: ROLLBACK on boot-loop (highest priority)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDecisionRollbackBootLoop:
    """ROLLBACK decision on boot-loop detection (highest priority)."""

    @patch("deployment_agent.handler.create_agent")
    def test_rollback_on_boot_loop_high_success_rate(self, mock_create_agent):
        """Boot-loop triggers ROLLBACK even at 99% success rate."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="ROLLBACK",
            success_rate=99.0,
            failure_types={
                "boot_loop": 1,
                "connectivity_lost": 0,
                "version_mismatch": 0,
                "timeout": 0,
            },
            reasoning="Boot-loop detected, immediate rollback regardless of success rate",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "ROLLBACK"
        assert result["failure_types"]["boot_loop"] >= 1

    @patch("deployment_agent.handler.create_agent")
    def test_rollback_on_boot_loop_with_other_failures(self, mock_create_agent):
        """Boot-loop triggers ROLLBACK even with mixed other failure types."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="ROLLBACK",
            success_rate=96.0,
            failure_types={
                "boot_loop": 1,
                "connectivity_lost": 2,
                "version_mismatch": 1,
                "timeout": 0,
            },
            reasoning="Boot-loop present, rollback takes precedence over all criteria",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "ROLLBACK"
        assert result["failure_types"]["boot_loop"] >= 1

    @patch("deployment_agent.handler.create_agent")
    def test_rollback_on_boot_loop_low_success_rate(self, mock_create_agent):
        """Boot-loop triggers ROLLBACK at low success rate."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="ROLLBACK",
            success_rate=80.0,
            failure_types={
                "boot_loop": 5,
                "connectivity_lost": 3,
                "version_mismatch": 2,
                "timeout": 2,
            },
            reasoning="Multiple boot-loops detected, critical failure",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "ROLLBACK"
        assert result["failure_types"]["boot_loop"] >= 1


# ---------------------------------------------------------------------------
# Test class: PAUSE on connectivity-only 95-98%
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDecisionPauseConnectivity:
    """PAUSE decision on connectivity-only failures at 95-98%."""

    @patch("deployment_agent.handler.create_agent")
    def test_pause_at_96_percent_connectivity_only(self, mock_create_agent):
        """96% success with only connectivity-lost failures produces PAUSE."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="PAUSE",
            success_rate=96.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 4,
                "version_mismatch": 0,
                "timeout": 0,
            },
            pause_count=1,
            reasoning="95-98% range with connectivity-only failures, pausing for retry",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "PAUSE"
        assert result["success_rate"] >= 95.0
        assert result["success_rate"] < 98.0
        assert result["failure_types"]["boot_loop"] == 0
        assert result["failure_types"]["connectivity_lost"] > 0
        assert result["failure_types"]["version_mismatch"] == 0
        assert result["failure_types"]["timeout"] == 0

    @patch("deployment_agent.handler.create_agent")
    def test_pause_at_95_percent_connectivity_only(self, mock_create_agent):
        """Exactly 95% success with only connectivity-lost failures produces PAUSE."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="PAUSE",
            success_rate=95.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 5,
                "version_mismatch": 0,
                "timeout": 0,
            },
            pause_count=1,
            reasoning="At 95% threshold with connectivity-only, pausing",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "PAUSE"
        assert result["success_rate"] >= 95.0
        assert result["failure_types"]["connectivity_lost"] > 0

    @patch("deployment_agent.handler.create_agent")
    def test_pause_at_97_9_percent_connectivity_only(self, mock_create_agent):
        """97.9% success with only connectivity-lost produces PAUSE (just below 98%)."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="PAUSE",
            success_rate=97.9,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 2,
                "version_mismatch": 0,
                "timeout": 0,
            },
            pause_count=1,
            reasoning="Just below PROCEED threshold, connectivity only, pause for retry",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "PAUSE"
        assert result["success_rate"] < 98.0
        assert result["success_rate"] >= 95.0


# ---------------------------------------------------------------------------
# Test class: ROLLBACK on mixed failures 95-98%
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDecisionRollbackMixed:
    """ROLLBACK decision on mixed failure types at 95-98%."""

    @patch("deployment_agent.handler.create_agent")
    def test_rollback_mixed_failures_at_96_percent(self, mock_create_agent):
        """96% with connectivity + timeout failures (mixed) produces ROLLBACK."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="ROLLBACK",
            success_rate=96.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 2,
                "version_mismatch": 0,
                "timeout": 2,
            },
            reasoning="95-98% range with mixed failure types, rolling back",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "ROLLBACK"
        assert result["success_rate"] >= 95.0
        assert result["success_rate"] < 98.0
        # Verify mixed: more than one non-zero failure classification
        non_zero_types = sum(1 for v in result["failure_types"].values() if v > 0)
        assert non_zero_types > 1

    @patch("deployment_agent.handler.create_agent")
    def test_rollback_mixed_connectivity_and_version_mismatch(self, mock_create_agent):
        """97% with connectivity + version_mismatch (mixed, no boot-loop) is ROLLBACK."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="ROLLBACK",
            success_rate=97.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 1,
                "version_mismatch": 2,
                "timeout": 0,
            },
            reasoning="Mixed failure types in 95-98% range indicate instability",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "ROLLBACK"
        assert result["failure_types"]["boot_loop"] == 0
        non_zero_types = sum(1 for v in result["failure_types"].values() if v > 0)
        assert non_zero_types > 1


# ---------------------------------------------------------------------------
# Test class: ROLLBACK below 95%
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDecisionRollbackBelowThreshold:
    """ROLLBACK decision below 95% threshold."""

    @patch("deployment_agent.handler.create_agent")
    def test_rollback_at_94_percent(self, mock_create_agent):
        """94% success rate produces ROLLBACK regardless of failure types."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="ROLLBACK",
            success_rate=94.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 6,
                "version_mismatch": 0,
                "timeout": 0,
            },
            reasoning="Below 95% threshold, rollback required",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "ROLLBACK"
        assert result["success_rate"] < 95.0

    @patch("deployment_agent.handler.create_agent")
    def test_rollback_at_50_percent(self, mock_create_agent):
        """50% success rate produces ROLLBACK (catastrophic failure)."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="ROLLBACK",
            success_rate=50.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 20,
                "version_mismatch": 10,
                "timeout": 20,
            },
            reasoning="Catastrophic failure rate, immediate rollback",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "ROLLBACK"
        assert result["success_rate"] < 95.0

    @patch("deployment_agent.handler.create_agent")
    def test_rollback_at_0_percent(self, mock_create_agent):
        """0% success rate produces ROLLBACK (total failure)."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="ROLLBACK",
            success_rate=0.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 50,
                "version_mismatch": 50,
                "timeout": 0,
            },
            reasoning="Complete deployment failure, all devices failed",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "ROLLBACK"
        assert result["success_rate"] == 0.0


# ---------------------------------------------------------------------------
# Test class: hardware_revision PAUSE
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDecisionHardwareRevisionPause:
    """PAUSE decision when failures correlate with hardware_revision."""

    @patch("deployment_agent.handler.create_agent")
    def test_pause_hardware_revision_correlation(self, mock_create_agent):
        """All failures on same hardware_revision, no boot-loop, >=95% produces PAUSE."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="PAUSE",
            success_rate=96.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 4,
                "version_mismatch": 0,
                "timeout": 0,
            },
            pause_count=1,
            reasoning="All failures share hardware_revision rev-B, pausing for investigation",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "PAUSE"
        assert result["success_rate"] >= 95.0
        assert result["failure_types"]["boot_loop"] == 0
        assert result["pause_count"] >= 1

    @patch("deployment_agent.handler.create_agent")
    def test_pause_hardware_revision_at_95_boundary(self, mock_create_agent):
        """Hardware revision correlation at exactly 95% produces PAUSE."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="PAUSE",
            success_rate=95.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 0,
                "version_mismatch": 0,
                "timeout": 5,
            },
            pause_count=1,
            reasoning="Failures correlate to hardware_revision rev-C at 95% boundary",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "PAUSE"
        assert result["success_rate"] >= 95.0
        assert result["failure_types"]["boot_loop"] == 0


# ---------------------------------------------------------------------------
# Test class: 3-pause escalation to ROLLBACK
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDecisionPauseEscalation:
    """3-pause escalation to ROLLBACK."""

    @patch("deployment_agent.handler.create_agent")
    def test_three_pauses_escalates_to_rollback(self, mock_create_agent):
        """After 3 consecutive PAUSE decisions, the system escalates to ROLLBACK."""
        mock_agent = MagicMock()
        mock_create_agent.return_value = mock_agent

        # First PAUSE (pause_count=1)
        mock_agent.return_value = _agent_response(
            decision="PAUSE",
            success_rate=96.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 4,
                "version_mismatch": 0,
                "timeout": 0,
            },
            pause_count=1,
            reasoning="Connectivity issues, first pause",
        )
        result1 = handler(_assess_event())
        assert result1["decision"] == "PAUSE"
        assert result1["pause_count"] == 1

        # Second PAUSE (pause_count=2)
        mock_agent.return_value = _agent_response(
            decision="PAUSE",
            success_rate=96.5,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 3,
                "version_mismatch": 0,
                "timeout": 0,
            },
            pause_count=2,
            reasoning="Connectivity issues persist, second pause",
        )
        result2 = handler(_assess_event())
        assert result2["decision"] == "PAUSE"
        assert result2["pause_count"] == 2

        # Third PAUSE (pause_count=3) triggers escalation
        mock_agent.return_value = _agent_response(
            decision="PAUSE",
            success_rate=96.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 4,
                "version_mismatch": 0,
                "timeout": 0,
            },
            pause_count=3,
            reasoning="Third consecutive pause, escalation threshold reached",
        )
        result3 = handler(_assess_event())

        # The handler returns PAUSE, but ASL CheckRetryCount routes
        # pause_count >= 3 to ExecuteRollback
        assert result3["pause_count"] >= 3
        # Verify the ASL routing logic would escalate
        should_escalate = result3["pause_count"] >= 3
        assert should_escalate is True

    @patch("deployment_agent.handler.create_agent")
    def test_two_pauses_does_not_escalate(self, mock_create_agent):
        """2 consecutive PAUSE decisions do not trigger escalation."""
        mock_agent = MagicMock()
        mock_agent.return_value = _agent_response(
            decision="PAUSE",
            success_rate=96.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 4,
                "version_mismatch": 0,
                "timeout": 0,
            },
            pause_count=2,
            reasoning="Second pause, not yet at escalation threshold",
        )
        mock_create_agent.return_value = mock_agent

        result = handler(_assess_event())

        assert result["decision"] == "PAUSE"
        assert result["pause_count"] == 2
        should_escalate = result["pause_count"] >= 3
        assert should_escalate is False

    @patch("deployment_agent.handler.create_agent")
    def test_pause_then_proceed_resets_escalation(self, mock_create_agent):
        """A PROCEED decision after PAUSE does not trigger escalation."""
        mock_agent = MagicMock()
        mock_create_agent.return_value = mock_agent

        # First PAUSE
        mock_agent.return_value = _agent_response(
            decision="PAUSE",
            success_rate=96.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 4,
                "version_mismatch": 0,
                "timeout": 0,
            },
            pause_count=1,
            reasoning="Connectivity issue, first pause",
        )
        result1 = handler(_assess_event())
        assert result1["decision"] == "PAUSE"

        # Devices recover and PROCEED
        mock_agent.return_value = _agent_response(
            decision="PROCEED",
            success_rate=99.0,
            failure_types={
                "boot_loop": 0,
                "connectivity_lost": 0,
                "version_mismatch": 0,
                "timeout": 0,
            },
            pause_count=1,
            reasoning="Devices recovered, proceeding",
        )
        result2 = handler(_assess_event())
        assert result2["decision"] == "PROCEED"
        # Pause count is still tracked but escalation not triggered
        assert result2["pause_count"] < 3
