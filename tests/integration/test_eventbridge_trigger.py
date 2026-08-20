"""Integration tests for EventBridge trigger path.

Validates that the event parser Lambda correctly transforms S3 Object Created
events into valid state machine input, exercising the full end-to-end trigger
path from firmware upload to state machine start.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add lambda directory to path for imports
_lambda_dir = str(Path(__file__).resolve().parent.parent.parent / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    """Set required environment variables."""
    monkeypatch.setenv(
        "STATE_MACHINE_ARN",
        "arn:aws:states:us-east-1:000000000001:stateMachine:FirmwareDeploymentOrchestrator",
    )


def _make_full_s3_event(bucket: str, key: str) -> dict:
    """Create a full EventBridge S3 Object Created event."""
    return {
        "version": "0",
        "id": "test-event-id",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "account": "000000000001",
        "time": "2025-01-15T10:30:00Z",
        "region": "us-east-1",
        "detail": {
            "version": "0",
            "bucket": {"name": bucket},
            "object": {
                "key": key,
                "size": 1048576,
                "etag": "abcdef123456",
            },
        },
    }


@pytest.mark.integration
class TestEventBridgeTriggerEndToEnd:
    """Test the full trigger path from S3 event to state machine start."""

    @patch("event_parser.handler.boto3")
    def test_firmware_upload_triggers_valid_deployment(self, mock_boto3):
        """S3 upload of valid firmware triggers state machine with correct input."""
        from event_parser.handler import handler

        mock_sfn_client = MagicMock()
        mock_sfn_client.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:000000000001:execution:FirmwareDeploymentOrchestrator:run-1",
            "startDate": "2025-01-15T10:30:01Z",
        }
        mock_boto3.client.return_value = mock_sfn_client

        event = _make_full_s3_event(
            "firmware-bucket-123",
            "firmware/gateway-pro-v2.1.0.bin",
        )
        result = handler(event)

        # Verify parsed output
        assert result["firmware_s3_url"] == "s3://firmware-bucket-123/firmware/gateway-pro-v2.1.0.bin"
        assert result["device_type"] == "gateway-pro"
        assert result["target_version"] == "2.1.0"
        assert "execution_arn" in result

        # Verify SFN was called with correct input
        mock_sfn_client.start_execution.assert_called_once()
        import json

        call_kwargs = mock_sfn_client.start_execution.call_args[1]
        sfn_input = json.loads(call_kwargs["input"])
        assert sfn_input["firmware_s3_url"] == "s3://firmware-bucket-123/firmware/gateway-pro-v2.1.0.bin"
        assert sfn_input["device_type"] == "gateway-pro"
        assert sfn_input["target_version"] == "2.1.0"

    @patch("event_parser.handler.boto3")
    def test_complex_device_type_parsed_correctly(self, mock_boto3):
        """Device types with hyphens and underscores are parsed correctly."""
        from event_parser.handler import handler

        mock_sfn_client = MagicMock()
        mock_sfn_client.start_execution.return_value = {"executionArn": "arn:test"}
        mock_boto3.client.return_value = mock_sfn_client

        event = _make_full_s3_event("bucket", "firmware/temp_sensor-mk2-v10.0.1.bin")
        result = handler(event)

        assert result["device_type"] == "temp_sensor-mk2"
        assert result["target_version"] == "10.0.1"
        assert result["firmware_s3_url"] == "s3://bucket/firmware/temp_sensor-mk2-v10.0.1.bin"

    def test_invalid_firmware_key_raises_error(self):
        """Non-matching S3 keys raise ValueError before starting SFN."""
        from event_parser.handler import handler

        event = _make_full_s3_event("bucket", "uploads/random-file.zip")
        with pytest.raises(ValueError, match="does not match expected pattern"):
            handler(event)

    def test_firmware_in_subdirectory_raises_error(self):
        """Firmware in subdirectories is rejected."""
        from event_parser.handler import handler

        event = _make_full_s3_event("bucket", "firmware/v2/sensor-v1.0.0.bin")
        with pytest.raises(ValueError, match="does not match expected pattern"):
            handler(event)
