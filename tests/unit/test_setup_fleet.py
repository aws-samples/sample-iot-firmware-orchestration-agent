"""Unit tests for the setup_fleet script."""

import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

# Add scripts to path for import
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import setup_fleet


@pytest.fixture
def aws_env(monkeypatch):
    """Set up environment variables for AWS mocking."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("FLEET_INVENTORY_TABLE", "FleetInventory")


@pytest.fixture
def dynamodb_table(aws_env):
    """Create mocked DynamoDB FleetInventory table."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="FleetInventory",
            KeySchema=[{"AttributeName": "thing_name", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "thing_name", "AttributeType": "S"},
                {"AttributeName": "device_type", "AttributeType": "S"},
                {"AttributeName": "facility", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "device_type-index",
                    "KeySchema": [
                        {"AttributeName": "device_type", "KeyType": "HASH"},
                        {"AttributeName": "thing_name", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "facility-index",
                    "KeySchema": [
                        {"AttributeName": "facility", "KeyType": "HASH"},
                        {"AttributeName": "thing_name", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield table


@pytest.mark.unit
class TestDeviceAssignments:
    def test_builds_correct_number_of_devices(self):
        devices = setup_fleet._build_device_assignments(100)
        assert len(devices) == 100

    def test_builds_minimum_devices(self):
        devices = setup_fleet._build_device_assignments(20)
        assert len(devices) == 20

    def test_thing_names_follow_naming_pattern(self):
        devices = setup_fleet._build_device_assignments(20)
        for d in devices:
            assert d["thing_name"].startswith("sim-")
            parts = d["thing_name"].split("-")
            # sim-{device_type}-{facility}-{index}
            assert len(parts) >= 4

    def test_all_device_types_represented(self):
        devices = setup_fleet._build_device_assignments(100)
        types_seen = {d["device_type"] for d in devices}
        assert types_seen == set(setup_fleet.DEVICE_TYPES)

    def test_all_facilities_represented(self):
        devices = setup_fleet._build_device_assignments(100)
        facilities_seen = {d["facility"] for d in devices}
        assert facilities_seen == set(setup_fleet.FACILITIES)

    def test_all_hardware_revisions_represented(self):
        devices = setup_fleet._build_device_assignments(100)
        revisions_seen = {d["hardware_revision"] for d in devices}
        assert revisions_seen == set(setup_fleet.HARDWARE_REVISIONS)

    def test_criticality_distribution_50_30_20(self):
        devices = setup_fleet._build_device_assignments(100)
        counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
        for d in devices:
            counts[d["criticality"]] += 1

        assert counts["LOW"] == 50
        assert counts["MEDIUM"] == 30
        assert counts["HIGH"] == 20

    def test_criticality_distribution_minimum_fleet(self):
        devices = setup_fleet._build_device_assignments(20)
        counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
        for d in devices:
            counts[d["criticality"]] += 1

        # 50% of 20 = 10, 30% of 20 = 6, 20% of 20 = 4
        assert counts["LOW"] == 10
        assert counts["MEDIUM"] == 6
        assert counts["HIGH"] == 4

    def test_production_schedule_assigned_by_facility(self):
        devices = setup_fleet._build_device_assignments(36)
        for d in devices:
            expected_schedule = setup_fleet.PRODUCTION_SCHEDULES[d["facility"]]
            assert d["production_schedule"] == expected_schedule

    def test_factory_east_schedule(self):
        schedule = setup_fleet.PRODUCTION_SCHEDULES["factory-east"]
        assert schedule["start_hour"] == 8
        assert schedule["end_hour"] == 17
        assert schedule["timezone"] == "US/Eastern"

    def test_factory_west_schedule(self):
        schedule = setup_fleet.PRODUCTION_SCHEDULES["factory-west"]
        assert schedule["start_hour"] == 6
        assert schedule["end_hour"] == 14
        assert schedule["timezone"] == "US/Pacific"

    def test_factory_central_schedule(self):
        schedule = setup_fleet.PRODUCTION_SCHEDULES["factory-central"]
        assert schedule["start_hour"] == 22
        assert schedule["end_hour"] == 6
        assert schedule["timezone"] == "US/Central"


@pytest.mark.unit
class TestCriticalityAssignment:
    def test_first_half_is_low(self):
        assert setup_fleet._assign_criticality(0, 100) == "LOW"
        assert setup_fleet._assign_criticality(49, 100) == "LOW"

    def test_next_30_percent_is_medium(self):
        assert setup_fleet._assign_criticality(50, 100) == "MEDIUM"
        assert setup_fleet._assign_criticality(79, 100) == "MEDIUM"

    def test_last_20_percent_is_high(self):
        assert setup_fleet._assign_criticality(80, 100) == "HIGH"
        assert setup_fleet._assign_criticality(99, 100) == "HIGH"


@pytest.mark.unit
class TestRegisterDevices:
    @mock_aws
    def test_registers_iot_things(self, aws_env):
        # Create table
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName="FleetInventory",
            KeySchema=[{"AttributeName": "thing_name", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "thing_name", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        setup_fleet.register_devices(20)

        # Verify IoT things were created
        iot_client = boto3.client("iot", region_name="us-east-1")
        things = iot_client.list_things()["things"]
        assert len(things) == 20

    @mock_aws
    def test_populates_dynamodb_table(self, aws_env):
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="FleetInventory",
            KeySchema=[{"AttributeName": "thing_name", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "thing_name", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        setup_fleet.register_devices(20)

        # Scan all items
        response = table.scan()
        assert response["Count"] == 20

    @mock_aws
    def test_sets_firmware_version_1_0_0(self, aws_env):
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="FleetInventory",
            KeySchema=[{"AttributeName": "thing_name", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "thing_name", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        setup_fleet.register_devices(20)

        response = table.scan()
        for item in response["Items"]:
            assert item["firmware_version"] == "1.0.0"

    @mock_aws
    def test_sets_registered_at_timestamp(self, aws_env):
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="FleetInventory",
            KeySchema=[{"AttributeName": "thing_name", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "thing_name", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        setup_fleet.register_devices(20)

        response = table.scan()
        for item in response["Items"]:
            assert "registered_at" in item
            # Verify it's a valid ISO timestamp
            assert "T" in item["registered_at"]
