"""Unit tests for the rollback_wave tool."""

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
sys.modules.setdefault("strands", _mock_strands)

from deployment_agent.tools.rollback_wave import rollback_wave  # noqa: E402

# Fake AWS account IDs for testing (not real credentials)
MOCK_ACCOUNT_ID = "000000000001"
MOCK_ACCOUNT_ID_2 = "000000000002"
MOCK_ACCOUNT_ID_3 = "000000000003"


@pytest.mark.unit
class TestRollbackWaveValidation:
    """Test input validation."""

    def test_raises_on_empty_job_id(self):
        with pytest.raises(ValueError, match="job_id must be a non-empty string"):
            rollback_wave("", ["device-1"], "s3://bucket/firmware-v1.0.0.bin")

    def test_raises_on_whitespace_only_job_id(self):
        with pytest.raises(ValueError, match="job_id must be a non-empty string"):
            rollback_wave("   ", ["device-1"], "s3://bucket/firmware-v1.0.0.bin")

    def test_raises_on_empty_failed_thing_names(self):
        with pytest.raises(ValueError, match="failed_thing_names must be a non-empty list"):
            rollback_wave("job-123", [], "s3://bucket/firmware-v1.0.0.bin")

    def test_raises_on_empty_string_in_failed_thing_names(self):
        with pytest.raises(ValueError, match=r"failed_thing_names\[1\] must be a non-empty string"):
            rollback_wave("job-123", ["device-1", ""], "s3://bucket/firmware-v1.0.0.bin")

    def test_raises_on_whitespace_only_in_failed_thing_names(self):
        with pytest.raises(ValueError, match=r"failed_thing_names\[0\] must be a non-empty string"):
            rollback_wave("job-123", ["   "], "s3://bucket/firmware-v1.0.0.bin")

    def test_raises_on_invalid_firmware_s3_url(self):
        with pytest.raises(ValueError, match="previous_firmware_s3_url must match the s3:// pattern"):
            rollback_wave("job-123", ["device-1"], "https://bucket/firmware.bin")

    def test_raises_on_empty_firmware_s3_url(self):
        with pytest.raises(ValueError, match="previous_firmware_s3_url must match the s3:// pattern"):
            rollback_wave("job-123", ["device-1"], "")


@pytest.mark.unit
class TestRollbackWaveCancellationAndCreation:
    """Test successful job cancellation followed by rollback job creation."""

    @patch("deployment_agent.tools.rollback_wave.boto3")
    def test_cancels_job_and_creates_rollback(self, mock_boto3):
        mock_session = MagicMock()
        mock_session.region_name = "us-east-1"
        mock_boto3.session.Session.return_value = mock_session

        mock_sts_client = MagicMock()
        mock_sts_client.get_caller_identity.return_value = {"Account": MOCK_ACCOUNT_ID}

        mock_iot_client = MagicMock()
        mock_iot_client.cancel_job.return_value = {}
        mock_iot_client.create_job.return_value = {
            "jobArn": "arn:aws:iot:us-east-1:000000000001:job/rollback-fw-deploy-dep-1-wave-1",
            "jobId": "rollback-fw-deploy-dep-1-wave-1",
        }

        mock_boto3.client.side_effect = lambda svc: {
            "sts": mock_sts_client,
            "iot": mock_iot_client,
        }[svc]

        result = rollback_wave(
            job_id="fw-deploy-dep-1-wave-1",
            failed_thing_names=["device-a", "device-b"],
            previous_firmware_s3_url="s3://my-bucket/firmware/v1.0.0.bin",
        )

        assert result["rollback_job_id"] == "rollback-fw-deploy-dep-1-wave-1"
        assert result["rollback_job_arn"] == "arn:aws:iot:us-east-1:000000000001:job/rollback-fw-deploy-dep-1-wave-1"
        assert result["target_count"] == 2
        assert result["cancelled_job_id"] == "fw-deploy-dep-1-wave-1"

        # Verify cancel was called with force=True
        mock_iot_client.cancel_job.assert_called_once_with(jobId="fw-deploy-dep-1-wave-1", force=True)

        # Verify create_job was called with correct args
        mock_iot_client.create_job.assert_called_once()
        call_kwargs = mock_iot_client.create_job.call_args[1]
        assert call_kwargs["jobId"] == "rollback-fw-deploy-dep-1-wave-1"
        assert call_kwargs["targetSelection"] == "SNAPSHOT"
        assert call_kwargs["targets"] == [
            "arn:aws:iot:us-east-1:000000000001:thing/device-a",
            "arn:aws:iot:us-east-1:000000000001:thing/device-b",
        ]
        assert '"firmware_url": "s3://my-bucket/firmware/v1.0.0.bin"' in call_kwargs["document"]
        assert '"rollback": true' in call_kwargs["document"]

    @patch("deployment_agent.tools.rollback_wave.boto3")
    def test_rollback_job_id_format(self, mock_boto3):
        mock_session = MagicMock()
        mock_session.region_name = "eu-west-1"
        mock_boto3.session.Session.return_value = mock_session

        mock_sts_client = MagicMock()
        mock_sts_client.get_caller_identity.return_value = {"Account": MOCK_ACCOUNT_ID_3}

        mock_iot_client = MagicMock()
        mock_iot_client.cancel_job.return_value = {}
        mock_iot_client.create_job.return_value = {
            "jobArn": "arn:aws:iot:eu-west-1:000000000003:job/rollback-my-job-42",
            "jobId": "rollback-my-job-42",
        }

        mock_boto3.client.side_effect = lambda svc: {
            "sts": mock_sts_client,
            "iot": mock_iot_client,
        }[svc]

        result = rollback_wave(
            job_id="my-job-42",
            failed_thing_names=["thing-1"],
            previous_firmware_s3_url="s3://bucket/fw-old.bin",
        )

        assert result["rollback_job_id"] == "rollback-my-job-42"

    @patch("deployment_agent.tools.rollback_wave.boto3")
    def test_uses_wave_timeout_minutes(self, mock_boto3):
        mock_session = MagicMock()
        mock_session.region_name = "us-west-2"
        mock_boto3.session.Session.return_value = mock_session

        mock_sts_client = MagicMock()
        mock_sts_client.get_caller_identity.return_value = {"Account": MOCK_ACCOUNT_ID_2}

        mock_iot_client = MagicMock()
        mock_iot_client.cancel_job.return_value = {}
        mock_iot_client.create_job.return_value = {
            "jobArn": "arn:aws:iot:us-west-2:000000000002:job/rollback-job-x",
            "jobId": "rollback-job-x",
        }

        mock_boto3.client.side_effect = lambda svc: {
            "sts": mock_sts_client,
            "iot": mock_iot_client,
        }[svc]

        rollback_wave(
            job_id="job-x",
            failed_thing_names=["device-1"],
            previous_firmware_s3_url="s3://bucket/fw.bin",
        )

        call_kwargs = mock_iot_client.create_job.call_args[1]
        assert call_kwargs["timeoutConfig"] == {"inProgressTimeoutInMinutes": 30}


@pytest.mark.unit
class TestRollbackWaveAlreadyCompleted:
    """Test handling when cancel raises InvalidStateTransitionException."""

    @patch("deployment_agent.tools.rollback_wave.boto3")
    def test_proceeds_with_rollback_when_cancel_raises_invalid_state(self, mock_boto3):
        from botocore.exceptions import ClientError

        mock_session = MagicMock()
        mock_session.region_name = "us-east-1"
        mock_boto3.session.Session.return_value = mock_session

        mock_sts_client = MagicMock()
        mock_sts_client.get_caller_identity.return_value = {"Account": MOCK_ACCOUNT_ID}

        mock_iot_client = MagicMock()
        mock_iot_client.cancel_job.side_effect = ClientError(
            {"Error": {"Code": "InvalidStateTransitionException", "Message": "Job already completed"}},
            "CancelJob",
        )
        mock_iot_client.create_job.return_value = {
            "jobArn": "arn:aws:iot:us-east-1:000000000001:job/rollback-completed-job",
            "jobId": "rollback-completed-job",
        }

        mock_boto3.client.side_effect = lambda svc: {
            "sts": mock_sts_client,
            "iot": mock_iot_client,
        }[svc]

        result = rollback_wave(
            job_id="completed-job",
            failed_thing_names=["device-x"],
            previous_firmware_s3_url="s3://bucket/firmware-v1.bin",
        )

        # Rollback should still succeed despite cancel error
        assert result["rollback_job_id"] == "rollback-completed-job"
        assert result["target_count"] == 1
        assert result["cancelled_job_id"] == "completed-job"

    @patch("deployment_agent.tools.rollback_wave.boto3")
    def test_proceeds_with_rollback_when_cancel_raises_resource_not_found(self, mock_boto3):
        from botocore.exceptions import ClientError

        mock_session = MagicMock()
        mock_session.region_name = "us-east-1"
        mock_boto3.session.Session.return_value = mock_session

        mock_sts_client = MagicMock()
        mock_sts_client.get_caller_identity.return_value = {"Account": MOCK_ACCOUNT_ID}

        mock_iot_client = MagicMock()
        mock_iot_client.cancel_job.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Job not found"}},
            "CancelJob",
        )
        mock_iot_client.create_job.return_value = {
            "jobArn": "arn:aws:iot:us-east-1:000000000001:job/rollback-missing-job",
            "jobId": "rollback-missing-job",
        }

        mock_boto3.client.side_effect = lambda svc: {
            "sts": mock_sts_client,
            "iot": mock_iot_client,
        }[svc]

        result = rollback_wave(
            job_id="missing-job",
            failed_thing_names=["device-1"],
            previous_firmware_s3_url="s3://bucket/fw.bin",
        )

        assert result["rollback_job_id"] == "rollback-missing-job"
        assert result["target_count"] == 1

    @patch("deployment_agent.tools.rollback_wave.boto3")
    def test_raises_on_unexpected_client_error(self, mock_boto3):
        from botocore.exceptions import ClientError

        mock_iot_client = MagicMock()
        mock_iot_client.cancel_job.side_effect = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
            "CancelJob",
        )

        mock_boto3.client.return_value = mock_iot_client

        with pytest.raises(ClientError) as exc_info:
            rollback_wave(
                job_id="throttled-job",
                failed_thing_names=["device-1"],
                previous_firmware_s3_url="s3://bucket/fw.bin",
            )

        assert exc_info.value.response["Error"]["Code"] == "ThrottlingException"
