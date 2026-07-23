"""Unit tests for the create_deployment_wave tool."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add lambda directory to path for imports
_lambda_dir = str(Path(__file__).resolve().parent.parent.parent / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

# Mock the strands module before importing the tool
_mock_strands = MagicMock()
_mock_strands.tool = lambda fn: fn
sys.modules["strands"] = _mock_strands

from deployment_agent.tools.create_deployment_wave import create_deployment_wave  # noqa: E402

# Fake AWS account IDs for testing (not real credentials)
MOCK_ACCOUNT_ID = "000000000001"
MOCK_ACCOUNT_ID_2 = "000000000002"
MOCK_ACCOUNT_ID_3 = "000000000003"


@pytest.mark.unit
class TestCreateDeploymentWaveValidation:
    """Test input validation."""

    def test_raises_on_empty_deployment_id(self):
        with pytest.raises(ValueError, match="deployment_id must be a non-empty string"):
            create_deployment_wave("", 1, ["device-1"], "s3://bucket/firmware.bin")

    def test_raises_on_whitespace_only_deployment_id(self):
        with pytest.raises(ValueError, match="deployment_id must be a non-empty string"):
            create_deployment_wave("   ", 1, ["device-1"], "s3://bucket/firmware.bin")

    def test_raises_on_zero_wave_number(self):
        with pytest.raises(ValueError, match="wave_number must be a positive integer"):
            create_deployment_wave("deploy-1", 0, ["device-1"], "s3://bucket/firmware.bin")

    def test_raises_on_negative_wave_number(self):
        with pytest.raises(ValueError, match="wave_number must be a positive integer"):
            create_deployment_wave("deploy-1", -1, ["device-1"], "s3://bucket/firmware.bin")

    def test_raises_on_empty_thing_names(self):
        with pytest.raises(ValueError, match="thing_names must be a non-empty list"):
            create_deployment_wave("deploy-1", 1, [], "s3://bucket/firmware.bin")

    def test_raises_on_thing_names_exceeding_500(self):
        names = [f"device-{i}" for i in range(501)]
        with pytest.raises(ValueError, match="thing_names must not exceed 500 items"):
            create_deployment_wave("deploy-1", 1, names, "s3://bucket/firmware.bin")

    def test_raises_on_empty_string_in_thing_names(self):
        with pytest.raises(ValueError, match=r"thing_names\[1\] must be a non-empty string"):
            create_deployment_wave("deploy-1", 1, ["device-1", ""], "s3://bucket/firmware.bin")

    def test_raises_on_whitespace_only_in_thing_names(self):
        with pytest.raises(ValueError, match=r"thing_names\[0\] must be a non-empty string"):
            create_deployment_wave("deploy-1", 1, ["   "], "s3://bucket/firmware.bin")

    def test_raises_on_invalid_firmware_s3_url(self):
        with pytest.raises(ValueError, match="firmware_s3_url must match the s3:// pattern"):
            create_deployment_wave("deploy-1", 1, ["device-1"], "https://bucket/firmware.bin")

    def test_raises_on_empty_firmware_s3_url(self):
        with pytest.raises(ValueError, match="firmware_s3_url must match the s3:// pattern"):
            create_deployment_wave("deploy-1", 1, ["device-1"], "")

    def test_raises_on_invalid_timeout_minutes(self):
        with pytest.raises(ValueError, match="timeout_minutes must be a positive integer"):
            create_deployment_wave("deploy-1", 1, ["device-1"], "s3://bucket/fw.bin", 0)


@pytest.mark.unit
class TestCreateDeploymentWaveJobCreation:
    """Test IoT Job creation."""

    @patch("deployment_agent.tools.create_deployment_wave.boto3")
    def test_creates_job_with_correct_parameters(self, mock_boto3):
        mock_session = MagicMock()
        mock_session.region_name = "us-east-1"
        mock_boto3.session.Session.return_value = mock_session

        mock_sts_client = MagicMock()
        mock_sts_client.get_caller_identity.return_value = {"Account": MOCK_ACCOUNT_ID}

        mock_iot_client = MagicMock()
        mock_iot_client.create_job.return_value = {
            "jobArn": "arn:aws:iot:us-east-1:000000000001:job/fw-deploy-dep-1-wave-1",
            "jobId": "fw-deploy-dep-1-wave-1",
        }

        mock_boto3.client.side_effect = lambda svc: {
            "sts": mock_sts_client,
            "iot": mock_iot_client,
        }[svc]

        result = create_deployment_wave(
            deployment_id="dep-1",
            wave_number=1,
            thing_names=["device-a", "device-b"],
            firmware_s3_url="s3://my-bucket/firmware/v2.0.0.bin",
            timeout_minutes=45,
        )

        assert result["job_id"] == "fw-deploy-dep-1-wave-1"
        assert result["job_arn"] == "arn:aws:iot:us-east-1:000000000001:job/fw-deploy-dep-1-wave-1"
        assert result["target_count"] == 2

        # Verify create_job was called with correct args
        mock_iot_client.create_job.assert_called_once()
        call_kwargs = mock_iot_client.create_job.call_args[1]
        assert call_kwargs["jobId"] == "fw-deploy-dep-1-wave-1"
        assert call_kwargs["targetSelection"] == "SNAPSHOT"
        assert call_kwargs["timeoutConfig"] == {"inProgressTimeoutInMinutes": 45}
        assert call_kwargs["targets"] == [
            "arn:aws:iot:us-east-1:000000000001:thing/device-a",
            "arn:aws:iot:us-east-1:000000000001:thing/device-b",
        ]
        assert '"firmware_url": "s3://my-bucket/firmware/v2.0.0.bin"' in call_kwargs["document"]

    @patch("deployment_agent.tools.create_deployment_wave.boto3")
    def test_uses_default_timeout_of_30_minutes(self, mock_boto3):
        mock_session = MagicMock()
        mock_session.region_name = "us-west-2"
        mock_boto3.session.Session.return_value = mock_session

        mock_sts_client = MagicMock()
        mock_sts_client.get_caller_identity.return_value = {"Account": MOCK_ACCOUNT_ID_2}

        mock_iot_client = MagicMock()
        mock_iot_client.create_job.return_value = {
            "jobArn": "arn:aws:iot:us-west-2:000000000002:job/fw-deploy-x-wave-2",
            "jobId": "fw-deploy-x-wave-2",
        }

        mock_boto3.client.side_effect = lambda svc: {
            "sts": mock_sts_client,
            "iot": mock_iot_client,
        }[svc]

        result = create_deployment_wave(
            deployment_id="x",
            wave_number=2,
            thing_names=["thing-1"],
            firmware_s3_url="s3://bucket/fw.bin",
        )

        call_kwargs = mock_iot_client.create_job.call_args[1]
        assert call_kwargs["timeoutConfig"] == {"inProgressTimeoutInMinutes": 30}
        assert result["target_count"] == 1

    @patch("deployment_agent.tools.create_deployment_wave.boto3")
    def test_generates_correct_job_id_format(self, mock_boto3):
        mock_session = MagicMock()
        mock_session.region_name = "eu-west-1"
        mock_boto3.session.Session.return_value = mock_session

        mock_sts_client = MagicMock()
        mock_sts_client.get_caller_identity.return_value = {"Account": MOCK_ACCOUNT_ID_3}

        mock_iot_client = MagicMock()
        mock_iot_client.create_job.return_value = {
            "jobArn": "arn:aws:iot:eu-west-1:000000000003:job/fw-deploy-abc-123-wave-3",
            "jobId": "fw-deploy-abc-123-wave-3",
        }

        mock_boto3.client.side_effect = lambda svc: {
            "sts": mock_sts_client,
            "iot": mock_iot_client,
        }[svc]

        result = create_deployment_wave(
            deployment_id="abc-123",
            wave_number=3,
            thing_names=["dev-1"],
            firmware_s3_url="s3://bucket/fw.bin",
        )

        assert result["job_id"] == "fw-deploy-abc-123-wave-3"

    @patch("deployment_agent.tools.create_deployment_wave.boto3")
    def test_accepts_max_500_things(self, mock_boto3):
        mock_session = MagicMock()
        mock_session.region_name = "us-east-1"
        mock_boto3.session.Session.return_value = mock_session

        mock_sts_client = MagicMock()
        mock_sts_client.get_caller_identity.return_value = {"Account": MOCK_ACCOUNT_ID}

        mock_iot_client = MagicMock()
        mock_iot_client.create_job.return_value = {
            "jobArn": "arn:aws:iot:us-east-1:000000000001:job/fw-deploy-big-wave-1",
            "jobId": "fw-deploy-big-wave-1",
        }

        mock_boto3.client.side_effect = lambda svc: {
            "sts": mock_sts_client,
            "iot": mock_iot_client,
        }[svc]

        names = [f"device-{i}" for i in range(500)]
        result = create_deployment_wave(
            deployment_id="big",
            wave_number=1,
            thing_names=names,
            firmware_s3_url="s3://bucket/fw.bin",
        )

        assert result["target_count"] == 500
