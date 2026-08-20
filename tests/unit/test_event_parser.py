"""Unit tests for the EventBridge input parser Lambda."""

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
    """Set required environment variables for tests."""
    monkeypatch.setenv("STATE_MACHINE_ARN", "arn:aws:states:us-east-1:000000000001:stateMachine:test")


def _make_s3_event(bucket: str, key: str) -> dict:
    """Create a minimal S3 Object Created EventBridge event."""
    return {
        "detail": {
            "bucket": {"name": bucket},
            "object": {"key": key},
        }
    }


@pytest.mark.unit
class TestEventParserValidKeys:
    """Test successful parsing of valid firmware keys."""

    @patch("event_parser.handler.boto3")
    def test_standard_firmware_key(self, mock_boto3):
        from event_parser.handler import handler

        mock_sfn = MagicMock()
        mock_sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:000000000001:execution:test:run-1"
        }
        mock_boto3.client.return_value = mock_sfn

        event = _make_s3_event("my-firmware-bucket", "firmware/sensor-v1.0.0.bin")
        result = handler(event)

        assert result["firmware_s3_url"] == "s3://my-firmware-bucket/firmware/sensor-v1.0.0.bin"
        assert result["device_type"] == "sensor"
        assert result["target_version"] == "1.0.0"
        assert "execution_arn" in result

    @patch("event_parser.handler.boto3")
    def test_device_type_with_hyphens(self, mock_boto3):
        from event_parser.handler import handler

        mock_sfn = MagicMock()
        mock_sfn.start_execution.return_value = {"executionArn": "arn:test"}
        mock_boto3.client.return_value = mock_sfn

        event = _make_s3_event("bucket", "firmware/gateway-pro-v2.1.3.bin")
        result = handler(event)

        assert result["device_type"] == "gateway-pro"
        assert result["target_version"] == "2.1.3"

    @patch("event_parser.handler.boto3")
    def test_device_type_with_underscores(self, mock_boto3):
        from event_parser.handler import handler

        mock_sfn = MagicMock()
        mock_sfn.start_execution.return_value = {"executionArn": "arn:test"}
        mock_boto3.client.return_value = mock_sfn

        event = _make_s3_event("bucket", "firmware/temp_sensor_v2-v10.20.30.bin")
        result = handler(event)

        assert result["device_type"] == "temp_sensor_v2"
        assert result["target_version"] == "10.20.30"

    @patch("event_parser.handler.boto3")
    def test_sfn_start_execution_called_correctly(self, mock_boto3):
        from event_parser.handler import handler

        mock_sfn = MagicMock()
        mock_sfn.start_execution.return_value = {"executionArn": "arn:test"}
        mock_boto3.client.return_value = mock_sfn

        event = _make_s3_event("my-bucket", "firmware/sensor-v1.2.3.bin")
        handler(event)

        mock_sfn.start_execution.assert_called_once()
        call_kwargs = mock_sfn.start_execution.call_args[1]
        assert call_kwargs["stateMachineArn"] == "arn:aws:states:us-east-1:000000000001:stateMachine:test"
        assert '"firmware_s3_url": "s3://my-bucket/firmware/sensor-v1.2.3.bin"' in call_kwargs["input"]


@pytest.mark.unit
class TestEventParserInvalidKeys:
    """Test rejection of invalid firmware keys."""

    def test_missing_firmware_prefix(self):
        from event_parser.handler import handler

        event = _make_s3_event("bucket", "uploads/sensor-v1.0.0.bin")
        with pytest.raises(ValueError, match="does not match expected pattern"):
            handler(event)

    def test_missing_version(self):
        from event_parser.handler import handler

        event = _make_s3_event("bucket", "firmware/sensor.bin")
        with pytest.raises(ValueError, match="does not match expected pattern"):
            handler(event)

    def test_invalid_version_format(self):
        from event_parser.handler import handler

        event = _make_s3_event("bucket", "firmware/sensor-v1.0.bin")
        with pytest.raises(ValueError, match="does not match expected pattern"):
            handler(event)

    def test_missing_bin_extension(self):
        from event_parser.handler import handler

        event = _make_s3_event("bucket", "firmware/sensor-v1.0.0.zip")
        with pytest.raises(ValueError, match="does not match expected pattern"):
            handler(event)

    def test_subdirectory_in_path(self):
        from event_parser.handler import handler

        event = _make_s3_event("bucket", "firmware/subdir/sensor-v1.0.0.bin")
        with pytest.raises(ValueError, match="does not match expected pattern"):
            handler(event)

    def test_device_type_starting_with_hyphen(self):
        from event_parser.handler import handler

        event = _make_s3_event("bucket", "firmware/-sensor-v1.0.0.bin")
        with pytest.raises(ValueError, match="does not match expected pattern"):
            handler(event)

    def test_empty_device_type(self):
        from event_parser.handler import handler

        event = _make_s3_event("bucket", "firmware/-v1.0.0.bin")
        with pytest.raises(ValueError, match="does not match expected pattern"):
            handler(event)
