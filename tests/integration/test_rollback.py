"""Integration tests for rollback flow (H1, H2, H5).

Validates that:
- ExecuteRollback uses the actual job_id from CreateIoTJob (not reconstructed)
- Only failed devices are targeted for rollback
- Previous firmware is resolved per-device from FleetInventory
- Job ID is consistent between create and rollback paths
"""

import functools
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add lambda directory to path for imports
_lambda_dir = str(Path(__file__).resolve().parent.parent.parent / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

# Mock external dependencies
_mock_strands = MagicMock()
_mock_strands.tool = lambda fn: fn
sys.modules["strands"] = _mock_strands
sys.modules["strands.models"] = MagicMock()
sys.modules["strands.models.bedrock"] = MagicMock()


def _fake_metric_scope(fn=None):
    if fn is not None:

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
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

from shared.job_id import build_job_id  # noqa: E402


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    """Set required environment variables."""
    monkeypatch.setenv("FLEET_INVENTORY_TABLE", "FleetInventory")
    monkeypatch.setenv("DEPLOYMENT_HISTORY_TABLE", "DeploymentHistory")
    monkeypatch.setenv("FIRMWARE_BUCKET", "test-firmware-bucket")
    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:000000000001:test")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")


@pytest.mark.integration
class TestJobIdConsistency:
    """Test that job IDs are consistent between create and rollback (H5)."""

    def test_job_id_sanitizes_dots_in_deployment_id(self):
        """Dots in deployment_id are sanitized consistently."""
        # Simulates the EventBridge path where deployment_id might contain dots
        deployment_id = "deploy-sensor-v1.0.0"
        wave_number = 1

        job_id = build_job_id(deployment_id, wave_number)

        assert "." not in job_id
        assert job_id == "fw-deploy-deploy-sensor-v1-0-0-wave-1"

    def test_job_id_sanitizes_slashes(self):
        """Slashes from S3 paths are sanitized."""
        deployment_id = "firmware/sensor-v1.0.0.bin"
        wave_number = 2

        job_id = build_job_id(deployment_id, wave_number)

        assert "/" not in job_id
        assert "." not in job_id

    def test_same_input_produces_same_job_id(self):
        """Job ID construction is deterministic."""
        result1 = build_job_id("deploy-sensor-abc123", 3)
        result2 = build_job_id("deploy-sensor-abc123", 3)
        assert result1 == result2


@pytest.mark.integration
class TestRollbackTargetsOnlyFailedDevices:
    """Test that rollback targets only failed devices (H2)."""

    @patch("deployment_agent.handler.create_agent")
    @patch("deployment_agent.handler.write_deployment_history")
    def test_assess_returns_failed_thing_names(self, mock_write_history, mock_create_agent):
        """handle_assess surfaces failed_thing_names from agent response."""
        from deployment_agent.handler import handle_assess

        # Mock agent to return a response with failed_thing_names
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = json.dumps({
            "decision": "ROLLBACK",
            "reasoning": "Boot-loop detected on 2 devices",
            "success_rate": 80.0,
            "failure_types": {"boot_loop": 2},
            "failed_thing_names": ["device-003", "device-007"],
        })
        mock_create_agent.return_value = mock_agent_instance

        event = {
            "action": "ASSESS",
            "deployment_id": "deploy-sensor-abc123",
            "wave_number": 1,
            "job_id": "fw-deploy-deploy-sensor-abc123-wave-1",
            "thing_names": ["device-001", "device-002", "device-003", "device-004", "device-007"],
            "target_version": "1.2.0",
        }

        result = handle_assess(event)

        assert result["decision"] == "ROLLBACK"
        assert result["failed_thing_names"] == ["device-003", "device-007"]
        assert len(result["failed_thing_names"]) == 2  # Not all 5 devices

    @patch("deployment_agent.handler.create_agent")
    @patch("deployment_agent.handler.write_deployment_history")
    @patch("deployment_agent.handler.write_device_outcomes")
    def test_proceed_returns_empty_failed_list(self, mock_outcomes, mock_history, mock_create_agent):
        """When decision is PROCEED, failed_thing_names is empty."""
        from deployment_agent.handler import handle_assess

        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = json.dumps({
            "decision": "PROCEED",
            "reasoning": "All devices succeeded",
            "success_rate": 100.0,
            "failure_types": {},
            "failed_thing_names": [],
        })
        mock_create_agent.return_value = mock_agent_instance

        event = {
            "action": "ASSESS",
            "deployment_id": "deploy-sensor-abc123",
            "wave_number": 1,
            "job_id": "fw-deploy-deploy-sensor-abc123-wave-1",
            "thing_names": ["device-001", "device-002"],
            "target_version": "1.2.0",
        }

        result = handle_assess(event)

        assert result["decision"] == "PROCEED"
        assert result["failed_thing_names"] == []


@pytest.mark.integration
class TestRollbackResolvesPreviousFirmware:
    """Test that rollback resolves previous firmware per-device (H1)."""

    @patch("deployment_agent.handler.create_agent")
    @patch("deployment_agent.handler.boto3")
    def test_rollback_queries_fleet_inventory_for_firmware_version(
        self, mock_boto3, mock_create_agent
    ):
        """handle_rollback queries FleetInventory to resolve previous firmware URLs."""
        from deployment_agent.handler import handle_rollback

        # Mock DynamoDB BatchGetItem response
        mock_dynamodb = MagicMock()
        mock_table = MagicMock()
        mock_dynamodb.Table.return_value = mock_table
        mock_dynamodb.meta.client.batch_get_item.return_value = {
            "Responses": {
                "FleetInventory": [
                    {"thing_name": "device-003", "firmware_version": "1.0.0"},
                    {"thing_name": "device-007", "firmware_version": "1.1.0"},
                ]
            },
            "UnprocessedKeys": {},
        }
        mock_boto3.resource.return_value = mock_dynamodb

        # Mock agent response
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = json.dumps({
            "rollback_job_id": "rollback-fw-deploy-test-wave-1",
            "rollback_job_arn": "arn:aws:iot:us-east-1:000000000001:job/rollback-fw-deploy-test-wave-1",
            "target_count": 2,
            "cancelled_job_id": "fw-deploy-test-wave-1",
        })
        mock_create_agent.return_value = mock_agent_instance

        event = {
            "action": "ROLLBACK",
            "deployment_id": "deploy-sensor-abc123",
            "job_id": "fw-deploy-deploy-sensor-abc123-wave-1",
            "failed_thing_names": ["device-003", "device-007"],
            "device_type": "sensor",
            "firmware_s3_url": "s3://test-firmware-bucket/firmware/sensor-v1.2.0.bin",
        }

        result = handle_rollback(event)

        # Verify BatchGetItem was called to resolve firmware versions
        mock_dynamodb.meta.client.batch_get_item.assert_called_once()
        batch_call = mock_dynamodb.meta.client.batch_get_item.call_args[1]
        request_items = batch_call["RequestItems"]["FleetInventory"]
        assert {"thing_name": "device-003"} in request_items["Keys"]
        assert {"thing_name": "device-007"} in request_items["Keys"]

        # Verify the agent was prompted with the resolved previous firmware URL
        mock_agent_instance.assert_called_once()
        prompt = mock_agent_instance.call_args[0][0]
        assert "Previous firmware S3 URL:" in prompt

        assert result["target_count"] == 2
