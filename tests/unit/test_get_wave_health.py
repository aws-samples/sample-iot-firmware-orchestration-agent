"""Unit tests for the get_wave_health tool."""

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

from deployment_agent.tools.get_wave_health import get_wave_health  # noqa: E402


def _make_execution(status, details_map=None):
    """Helper to create an execution summary dict."""
    execution = {"status": status}
    if details_map is not None:
        execution["statusDetails"] = {"detailsMap": details_map}
    return execution


@pytest.mark.unit
class TestGetWaveHealthValidation:
    """Test input validation."""

    def test_raises_on_empty_job_id(self):
        with pytest.raises(ValueError, match="job_id must be a non-empty string"):
            get_wave_health("")

    def test_raises_on_whitespace_only_job_id(self):
        with pytest.raises(ValueError, match="job_id must be a non-empty string"):
            get_wave_health("   ")


@pytest.mark.unit
class TestGetWaveHealthAllSucceeded:
    """Test scenario where all devices succeed."""

    @patch("deployment_agent.tools.get_wave_health.boto3")
    def test_all_succeeded(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.list_job_executions_for_job.return_value = {
            "executionSummaries": [
                _make_execution("SUCCEEDED"),
                _make_execution("SUCCEEDED"),
                _make_execution("SUCCEEDED"),
            ]
        }

        result = get_wave_health("test-job-123")

        assert result["total_devices"] == 3
        assert result["succeeded_count"] == 3
        assert result["failed_count"] == 0
        assert result["timed_out_count"] == 0
        assert result["in_progress_count"] == 0
        assert result["success_rate"] == 100.0
        assert result["failure_types"] == {
            "boot_loop": 0,
            "connectivity_lost": 0,
            "version_mismatch": 0,
            "timeout": 0,
        }


@pytest.mark.unit
class TestGetWaveHealthFailureClassification:
    """Test failure classification logic."""

    @patch("deployment_agent.tools.get_wave_health.boto3")
    def test_timed_out_classified_as_timeout(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.list_job_executions_for_job.return_value = {
            "executionSummaries": [
                _make_execution("SUCCEEDED"),
                _make_execution("TIMED_OUT"),
            ]
        }

        result = get_wave_health("job-1")

        assert result["failure_types"]["timeout"] == 1
        assert result["timed_out_count"] == 1
        assert result["failed_count"] == 1

    @patch("deployment_agent.tools.get_wave_health.boto3")
    def test_rejected_classified_as_version_mismatch(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.list_job_executions_for_job.return_value = {
            "executionSummaries": [
                _make_execution("SUCCEEDED"),
                _make_execution("REJECTED"),
            ]
        }

        result = get_wave_health("job-1")

        assert result["failure_types"]["version_mismatch"] == 1
        assert result["failed_count"] == 1

    @patch("deployment_agent.tools.get_wave_health.boto3")
    def test_failed_with_disconnect_classified_as_connectivity_lost(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.list_job_executions_for_job.return_value = {
            "executionSummaries": [
                _make_execution("SUCCEEDED"),
                _make_execution("FAILED", {"reason": "device disconnect detected"}),
            ]
        }

        result = get_wave_health("job-1")

        assert result["failure_types"]["connectivity_lost"] == 1
        assert result["failed_count"] == 1

    @patch("deployment_agent.tools.get_wave_health.boto3")
    def test_failed_with_restart_classified_as_boot_loop(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.list_job_executions_for_job.return_value = {
            "executionSummaries": [
                _make_execution("SUCCEEDED"),
                _make_execution("FAILED", {"reason": "device restart loop detected"}),
            ]
        }

        result = get_wave_health("job-1")

        assert result["failure_types"]["boot_loop"] == 1
        assert result["failed_count"] == 1

    @patch("deployment_agent.tools.get_wave_health.boto3")
    def test_failed_with_no_details_defaults_to_connectivity_lost(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.list_job_executions_for_job.return_value = {
            "executionSummaries": [
                _make_execution("FAILED"),
            ]
        }

        result = get_wave_health("job-1")

        assert result["failure_types"]["connectivity_lost"] == 1


@pytest.mark.unit
class TestGetWaveHealthSuccessRate:
    """Test success rate calculation."""

    @patch("deployment_agent.tools.get_wave_health.boto3")
    def test_success_rate_rounded_to_one_decimal(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        # 2 succeeded out of 3 = 66.666...% -> 66.7%
        mock_client.list_job_executions_for_job.return_value = {
            "executionSummaries": [
                _make_execution("SUCCEEDED"),
                _make_execution("SUCCEEDED"),
                _make_execution("FAILED", {"reason": "disconnect"}),
            ]
        }

        result = get_wave_health("job-1")

        assert result["success_rate"] == 66.7

    @patch("deployment_agent.tools.get_wave_health.boto3")
    def test_success_rate_zero_when_no_devices(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.list_job_executions_for_job.return_value = {"executionSummaries": []}

        result = get_wave_health("job-1")

        assert result["success_rate"] == 0.0
        assert result["total_devices"] == 0


@pytest.mark.unit
class TestGetWaveHealthPagination:
    """Test pagination handling."""

    @patch("deployment_agent.tools.get_wave_health.boto3")
    def test_handles_pagination_with_next_token(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.list_job_executions_for_job.side_effect = [
            {
                "executionSummaries": [_make_execution("SUCCEEDED")],
                "nextToken": "page2",
            },
            {
                "executionSummaries": [_make_execution("SUCCEEDED")],
            },
        ]

        result = get_wave_health("job-1")

        assert result["total_devices"] == 2
        assert result["succeeded_count"] == 2
        assert mock_client.list_job_executions_for_job.call_count == 2
        # Verify nextToken was passed in second call
        second_call_kwargs = mock_client.list_job_executions_for_job.call_args_list[1][1]
        assert second_call_kwargs["nextToken"] == "page2"


@pytest.mark.unit
class TestGetWaveHealthMixedStatuses:
    """Test scenarios with mixed execution statuses."""

    @patch("deployment_agent.tools.get_wave_health.boto3")
    def test_mixed_statuses(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.list_job_executions_for_job.return_value = {
            "executionSummaries": [
                _make_execution("SUCCEEDED"),
                _make_execution("SUCCEEDED"),
                _make_execution("IN_PROGRESS"),
                _make_execution("QUEUED"),
                _make_execution("TIMED_OUT"),
                _make_execution("REJECTED"),
                _make_execution("FAILED", {"reason": "device restart loop"}),
                _make_execution("FAILED", {"reason": "network disconnect"}),
                _make_execution("REMOVED"),
                _make_execution("CANCELED"),
            ]
        }

        result = get_wave_health("job-1")

        assert result["total_devices"] == 10
        assert result["succeeded_count"] == 2
        assert result["failed_count"] == 4
        assert result["timed_out_count"] == 1
        assert result["in_progress_count"] == 2
        assert result["success_rate"] == 20.0
        assert result["failure_types"] == {
            "boot_loop": 1,
            "connectivity_lost": 1,
            "version_mismatch": 1,
            "timeout": 1,
        }
