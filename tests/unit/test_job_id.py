"""Unit tests for the shared job ID helper."""

import sys
from pathlib import Path

import pytest

# Add lambda directory to path for imports
_lambda_dir = str(Path(__file__).resolve().parent.parent.parent / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

from shared.job_id import build_job_id  # noqa: E402


@pytest.mark.unit
class TestBuildJobId:
    """Test canonical job ID construction."""

    def test_simple_id(self):
        result = build_job_id("deploy-sensor-abc123", 1)
        assert result == "fw-deploy-deploy-sensor-abc123-wave-1"

    def test_dots_are_sanitized(self):
        result = build_job_id("deploy-sensor-v1.0.0", 2)
        assert result == "fw-deploy-deploy-sensor-v1-0-0-wave-2"

    def test_slashes_are_sanitized(self):
        result = build_job_id("firmware/sensor-v1.0.0.bin", 1)
        assert result == "fw-deploy-firmware-sensor-v1-0-0-bin-wave-1"

    def test_spaces_are_sanitized(self):
        result = build_job_id("deploy sensor v1", 3)
        assert result == "fw-deploy-deploy-sensor-v1-wave-3"

    def test_special_chars_sanitized(self):
        result = build_job_id("deploy@sensor#v1!2$3", 1)
        assert result == "fw-deploy-deploy-sensor-v1-2-3-wave-1"

    def test_underscores_preserved(self):
        result = build_job_id("deploy_sensor_abc", 1)
        assert result == "fw-deploy-deploy_sensor_abc-wave-1"

    def test_hyphens_preserved(self):
        result = build_job_id("deploy-sensor-abc", 1)
        assert result == "fw-deploy-deploy-sensor-abc-wave-1"

    def test_wave_number_is_included(self):
        result1 = build_job_id("deploy-1", 1)
        result2 = build_job_id("deploy-1", 5)
        assert result1.endswith("-wave-1")
        assert result2.endswith("-wave-5")

    def test_result_contains_only_valid_chars(self):
        """IoT Job IDs only allow [a-zA-Z0-9_-]."""
        import re

        tricky_ids = [
            "firmware/sensor-v1.0.0.bin",
            "a.b.c/d.e.f",
            "deploy (test) #1",
            "id+with=equals&ampersand",
        ]
        valid_pattern = re.compile(r"^[a-zA-Z0-9_-]+$")
        for deployment_id in tricky_ids:
            result = build_job_id(deployment_id, 1)
            assert valid_pattern.match(result), f"Invalid chars in: {result} (from {deployment_id})"
