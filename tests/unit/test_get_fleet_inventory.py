"""Unit tests for the get_fleet_inventory tool."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

# Add lambda directory to path for imports
_lambda_dir = str(Path(__file__).resolve().parent.parent.parent / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

# Mock the strands module before importing the tool
_mock_strands = MagicMock()
_mock_strands.tool = lambda fn: fn
sys.modules.setdefault("strands", _mock_strands)

os.environ["FLEET_INVENTORY_TABLE"] = "FleetInventory"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

from deployment_agent.tools.get_fleet_inventory import get_fleet_inventory  # noqa: E402


@pytest.fixture
def fleet_table():
    """Create a mocked DynamoDB FleetInventory table with device_type-index GSI."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="FleetInventory",
            KeySchema=[{"AttributeName": "thing_name", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "thing_name", "AttributeType": "S"},
                {"AttributeName": "device_type", "AttributeType": "S"},
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
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield table


def _put_device(table, thing_name, device_type, firmware_version, **overrides):
    """Insert a device record into the mocked table."""
    item = {
        "thing_name": thing_name,
        "device_type": device_type,
        "firmware_version": firmware_version,
        "criticality": "LOW",
        "location": "building-a",
        "facility": "factory-east",
        "hardware_revision": "rev-1",
        "registered_at": "2024-01-15T10:00:00Z",
    }
    item.update(overrides)
    table.put_item(Item=item)


@pytest.mark.unit
class TestGetFleetInventoryGSIQuery:
    """Tests for GSI query behavior."""

    def test_queries_correct_device_type(self, fleet_table):
        """Devices matching device_type are returned."""
        _put_device(fleet_table, "sensor-001", "sensor-v2", "1.0.0")
        _put_device(fleet_table, "sensor-002", "sensor-v2", "1.5.0")
        _put_device(fleet_table, "gateway-001", "gateway-pro", "1.0.0")

        result = get_fleet_inventory(device_type="sensor-v2", max_version="5.0.0")

        thing_names = {d["thing_name"] for d in result}
        assert thing_names == {"sensor-001", "sensor-002"}

    def test_does_not_return_other_device_types(self, fleet_table):
        """Devices of a different device_type are not returned."""
        _put_device(fleet_table, "gateway-001", "gateway-pro", "1.0.0")
        _put_device(fleet_table, "gateway-002", "gateway-pro", "1.5.0")

        result = get_fleet_inventory(device_type="sensor-v2", max_version="5.0.0")

        assert result == []


@pytest.mark.unit
class TestGetFleetInventorySemverFiltering:
    """Tests for semantic version filtering."""

    def test_returns_devices_below_max_version(self, fleet_table):
        """Devices with firmware_version < max_version are returned."""
        _put_device(fleet_table, "sensor-001", "sensor-v2", "1.0.0")
        _put_device(fleet_table, "sensor-002", "sensor-v2", "1.5.0")
        _put_device(fleet_table, "sensor-003", "sensor-v2", "2.0.0")
        _put_device(fleet_table, "sensor-004", "sensor-v2", "2.1.0")
        _put_device(fleet_table, "sensor-005", "sensor-v2", "3.0.0")

        result = get_fleet_inventory(device_type="sensor-v2", max_version="2.0.0")

        thing_names = {d["thing_name"] for d in result}
        assert thing_names == {"sensor-001", "sensor-002"}

    def test_excludes_devices_at_max_version(self, fleet_table):
        """Devices with firmware_version == max_version are excluded."""
        _put_device(fleet_table, "sensor-001", "sensor-v2", "2.0.0")

        result = get_fleet_inventory(device_type="sensor-v2", max_version="2.0.0")

        assert result == []

    def test_excludes_devices_above_max_version(self, fleet_table):
        """Devices with firmware_version > max_version are excluded."""
        _put_device(fleet_table, "sensor-001", "sensor-v2", "3.0.0")
        _put_device(fleet_table, "sensor-002", "sensor-v2", "2.5.0")

        result = get_fleet_inventory(device_type="sensor-v2", max_version="2.0.0")

        assert result == []

    def test_returns_all_devices_when_max_version_above_all(self, fleet_table):
        """All devices returned when max_version exceeds all firmware versions."""
        _put_device(fleet_table, "sensor-001", "sensor-v2", "1.0.0")
        _put_device(fleet_table, "sensor-002", "sensor-v2", "1.5.0")
        _put_device(fleet_table, "sensor-003", "sensor-v2", "2.0.0")
        _put_device(fleet_table, "sensor-004", "sensor-v2", "2.1.0")
        _put_device(fleet_table, "sensor-005", "sensor-v2", "3.0.0")

        result = get_fleet_inventory(device_type="sensor-v2", max_version="4.0.0")

        assert len(result) == 5


@pytest.mark.unit
class TestGetFleetInventoryPagination:
    """Tests for pagination handling with LastEvaluatedKey."""

    def test_handles_paginated_results(self, fleet_table):
        """All devices are returned even when DynamoDB paginates results."""
        # Insert enough devices to potentially trigger pagination
        # moto may not paginate at the same threshold as real DynamoDB,
        # but this validates the pagination loop logic handles multiple items correctly
        for i in range(25):
            _put_device(fleet_table, f"sensor-{i:03d}", "sensor-v2", "1.0.0")

        result = get_fleet_inventory(device_type="sensor-v2", max_version="2.0.0")

        assert len(result) == 25

    def test_pagination_collects_all_pages(self, fleet_table):
        """Inserting many items and querying returns all matching records."""
        for i in range(50):
            _put_device(fleet_table, f"device-{i:03d}", "sensor-v2", "1.2.3")

        result = get_fleet_inventory(device_type="sensor-v2", max_version="2.0.0")

        assert len(result) == 50
        returned_names = {d["thing_name"] for d in result}
        expected_names = {f"device-{i:03d}" for i in range(50)}
        assert returned_names == expected_names


@pytest.mark.unit
class TestGetFleetInventoryEmptyResults:
    """Tests for empty result scenarios."""

    def test_empty_list_when_no_matching_device_type(self, fleet_table):
        """Returns empty list when no devices match the device_type."""
        _put_device(fleet_table, "gateway-001", "gateway-pro", "1.0.0")

        result = get_fleet_inventory(device_type="sensor-v2", max_version="5.0.0")

        assert result == []

    def test_empty_list_when_all_versions_at_or_above_max(self, fleet_table):
        """Returns empty list when all devices have firmware_version >= max_version."""
        _put_device(fleet_table, "sensor-001", "sensor-v2", "2.0.0")
        _put_device(fleet_table, "sensor-002", "sensor-v2", "3.0.0")

        result = get_fleet_inventory(device_type="sensor-v2", max_version="2.0.0")

        assert result == []

    def test_empty_list_when_table_is_empty(self, fleet_table):
        """Returns empty list when the table has no records."""
        result = get_fleet_inventory(device_type="sensor-v2", max_version="5.0.0")

        assert result == []


@pytest.mark.unit
class TestGetFleetInventoryInputValidation:
    """Tests for ValueError on invalid inputs."""

    def test_raises_for_empty_device_type(self, fleet_table):
        """Empty device_type raises ValueError."""
        with pytest.raises(ValueError, match="device_type must be a non-empty string"):
            get_fleet_inventory(device_type="", max_version="1.0.0")

    def test_raises_for_whitespace_device_type(self, fleet_table):
        """Whitespace-only device_type raises ValueError."""
        with pytest.raises(ValueError, match="device_type must be a non-empty string"):
            get_fleet_inventory(device_type="   ", max_version="1.0.0")

    def test_raises_for_invalid_semver(self, fleet_table):
        """Invalid semantic version string raises ValueError."""
        with pytest.raises(ValueError, match="not a valid semantic version"):
            get_fleet_inventory(device_type="sensor-v2", max_version="not-a-version")

    def test_raises_for_empty_max_version(self, fleet_table):
        """Empty max_version raises ValueError."""
        with pytest.raises(ValueError, match="not a valid semantic version"):
            get_fleet_inventory(device_type="sensor-v2", max_version="")
