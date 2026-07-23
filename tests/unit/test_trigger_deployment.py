"""Unit tests for the trigger_deployment script."""

import json
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

# Add scripts to path for import
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import trigger_deployment


@pytest.fixture
def aws_env(monkeypatch):
    """Set up environment variables for AWS mocking."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("FIRMWARE_BUCKET", "amzn-s3-demo-firmware-bucket")


@pytest.fixture
def s3_bucket(aws_env):
    """Create mocked S3 bucket."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="amzn-s3-demo-firmware-bucket")
        yield s3


@pytest.mark.unit
class TestScenarioConfigurations:
    def test_successful_rollout_scenario_exists(self):
        assert "successful_rollout" in trigger_deployment.SCENARIOS

    def test_canary_failure_scenario_exists(self):
        assert "canary_failure" in trigger_deployment.SCENARIOS

    def test_partial_connectivity_loss_scenario_exists(self):
        assert "partial_connectivity_loss" in trigger_deployment.SCENARIOS

    def test_successful_rollout_has_99_percent_success(self):
        scenario = trigger_deployment.SCENARIOS["successful_rollout"]
        assert scenario["success_rate"] == 0.99

    def test_successful_rollout_failure_is_timeout(self):
        scenario = trigger_deployment.SCENARIOS["successful_rollout"]
        assert scenario["failure_distribution"] == {"timeout": 1.0}

    def test_canary_failure_has_boot_loop(self):
        scenario = trigger_deployment.SCENARIOS["canary_failure"]
        assert scenario["failure_distribution"] == {"boot_loop": 1.0}

    def test_canary_failure_targets_canary_wave(self):
        scenario = trigger_deployment.SCENARIOS["canary_failure"]
        assert scenario["target_wave"] == "canary"

    def test_partial_connectivity_loss_has_connectivity_lost(self):
        scenario = trigger_deployment.SCENARIOS["partial_connectivity_loss"]
        assert scenario["failure_distribution"] == {"connectivity_lost": 1.0}

    def test_partial_connectivity_loss_targets_factory_east(self):
        scenario = trigger_deployment.SCENARIOS["partial_connectivity_loss"]
        assert scenario["target_facility"] == "factory-east"

    def test_partial_connectivity_loss_rate_between_95_and_98(self):
        scenario = trigger_deployment.SCENARIOS["partial_connectivity_loss"]
        assert 0.95 <= scenario["success_rate"] <= 0.98


@pytest.mark.unit
class TestUploadFirmware:
    @mock_aws
    def test_uploads_firmware_binary(self, aws_env):
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="amzn-s3-demo-firmware-bucket")

        key = trigger_deployment.upload_firmware(
            bucket="amzn-s3-demo-firmware-bucket",
            device_type="temperature-sensor",
            version="2.0.0",
            scenario_name="successful_rollout",
            region="us-east-1",
        )

        assert key == "firmware/temperature-sensor-v2.0.0.bin"

        # Verify object exists
        obj = s3.get_object(Bucket="amzn-s3-demo-firmware-bucket", Key=key)
        body = obj["Body"].read().decode()
        assert "FIRMWARE_BINARY:temperature-sensor:v2.0.0:" in body

    @mock_aws
    def test_uploads_metadata_json(self, aws_env):
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="amzn-s3-demo-firmware-bucket")

        trigger_deployment.upload_firmware(
            bucket="amzn-s3-demo-firmware-bucket",
            device_type="temperature-sensor",
            version="2.0.0",
            scenario_name="successful_rollout",
            region="us-east-1",
        )

        metadata_key = "firmware/temperature-sensor-v2.0.0.metadata.json"
        obj = s3.get_object(Bucket="amzn-s3-demo-firmware-bucket", Key=metadata_key)
        metadata = json.loads(obj["Body"].read())

        assert metadata["scenario"] == "successful_rollout"
        assert metadata["device_type"] == "temperature-sensor"
        assert metadata["target_version"] == "2.0.0"
        assert "uploaded_at" in metadata
        assert "config" in metadata

    @mock_aws
    def test_firmware_key_path_format(self, aws_env):
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="amzn-s3-demo-firmware-bucket")

        key = trigger_deployment.upload_firmware(
            bucket="amzn-s3-demo-firmware-bucket",
            device_type="pressure-sensor",
            version="3.1.0",
            scenario_name="canary_failure",
            region="us-east-1",
        )

        assert key == "firmware/pressure-sensor-v3.1.0.bin"

    @mock_aws
    def test_firmware_object_has_scenario_metadata_header(self, aws_env):
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="amzn-s3-demo-firmware-bucket")

        trigger_deployment.upload_firmware(
            bucket="amzn-s3-demo-firmware-bucket",
            device_type="temperature-sensor",
            version="2.0.0",
            scenario_name="canary_failure",
            region="us-east-1",
        )

        obj = s3.head_object(
            Bucket="amzn-s3-demo-firmware-bucket",
            Key="firmware/temperature-sensor-v2.0.0.bin",
        )
        assert obj["Metadata"]["scenario"] == "canary_failure"
        assert obj["Metadata"]["device-type"] == "temperature-sensor"
        assert obj["Metadata"]["target-version"] == "2.0.0"

    @mock_aws
    def test_metadata_json_contains_scenario_config(self, aws_env):
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="amzn-s3-demo-firmware-bucket")

        trigger_deployment.upload_firmware(
            bucket="amzn-s3-demo-firmware-bucket",
            device_type="temperature-sensor",
            version="2.0.0",
            scenario_name="partial_connectivity_loss",
            region="us-east-1",
        )

        metadata_key = "firmware/temperature-sensor-v2.0.0.metadata.json"
        obj = s3.get_object(Bucket="amzn-s3-demo-firmware-bucket", Key=metadata_key)
        metadata = json.loads(obj["Body"].read())

        config = metadata["config"]
        assert config["success_rate"] == 0.96
        assert config["failure_distribution"] == {"connectivity_lost": 1.0}
        assert config["target_facility"] == "factory-east"


@pytest.mark.unit
class TestPrintScenarioInfo:
    def test_successful_rollout_prints_without_error(self, capsys):
        trigger_deployment.print_scenario_info("successful_rollout", "temperature-sensor", "2.0.0")
        captured = capsys.readouterr()
        assert "successful_rollout" in captured.out
        assert "PROCEED" in captured.out
        assert "COMPLETED" in captured.out

    def test_canary_failure_prints_without_error(self, capsys):
        trigger_deployment.print_scenario_info("canary_failure", "temperature-sensor", "2.0.0")
        captured = capsys.readouterr()
        assert "canary_failure" in captured.out
        assert "ROLLBACK" in captured.out
        assert "Boot-loop" in captured.out

    def test_partial_connectivity_prints_without_error(self, capsys):
        trigger_deployment.print_scenario_info("partial_connectivity_loss", "temperature-sensor", "2.0.0")
        captured = capsys.readouterr()
        assert "partial_connectivity_loss" in captured.out
        assert "PAUSE" in captured.out
        assert "factory-east" in captured.out
