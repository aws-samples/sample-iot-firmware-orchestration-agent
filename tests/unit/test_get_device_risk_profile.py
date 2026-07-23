"""Unit tests for the get_device_risk_profile tool."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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

from deployment_agent.tools.get_device_risk_profile import (  # noqa: E402
    _is_in_production_window,
    get_device_risk_profile,
)


@pytest.fixture
def dynamodb_table():
    """Create a mocked DynamoDB FleetInventory table."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="FleetInventory",
            KeySchema=[{"AttributeName": "thing_name", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "thing_name", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield table


def _put_device(table, thing_name="sensor-001", production_schedule=None, **overrides):
    """Insert a device record into the mocked table."""
    item = {
        "thing_name": thing_name,
        "device_type": "sensor-v2",
        "firmware_version": "1.0.0",
        "criticality": "MEDIUM",
        "location": "building-a",
        "facility": "factory-east",
        "hardware_revision": "rev-3",
        "registered_at": "2024-01-15T10:00:00Z",
    }
    if production_schedule is not None:
        item["production_schedule"] = production_schedule
    item.update(overrides)
    table.put_item(Item=item)


@pytest.mark.unit
class TestIsInProductionWindow:
    """Tests for the _is_in_production_window helper function."""

    def test_no_schedule_returns_false(self):
        """No production schedule means device is not in production window."""
        assert _is_in_production_window(None) is False

    @patch("deployment_agent.tools.get_device_risk_profile.datetime")
    def test_normal_window_inside(self, mock_datetime):
        """Device is in production during normal daytime hours."""
        from datetime import datetime as real_datetime
        from zoneinfo import ZoneInfo

        mock_now = real_datetime(2024, 6, 15, 10, 30, tzinfo=ZoneInfo("US/Eastern"))
        mock_datetime.now.return_value = mock_now

        schedule = {"start_hour": 8, "end_hour": 17, "timezone": "US/Eastern"}
        assert _is_in_production_window(schedule) is True

    @patch("deployment_agent.tools.get_device_risk_profile.datetime")
    def test_normal_window_outside(self, mock_datetime):
        """Device is NOT in production outside normal daytime hours."""
        from datetime import datetime as real_datetime
        from zoneinfo import ZoneInfo

        mock_now = real_datetime(2024, 6, 15, 20, 0, tzinfo=ZoneInfo("US/Eastern"))
        mock_datetime.now.return_value = mock_now

        schedule = {"start_hour": 8, "end_hour": 17, "timezone": "US/Eastern"}
        assert _is_in_production_window(schedule) is False

    @patch("deployment_agent.tools.get_device_risk_profile.datetime")
    def test_overnight_window_before_midnight(self, mock_datetime):
        """Device is in production during overnight window (before midnight)."""
        from datetime import datetime as real_datetime
        from zoneinfo import ZoneInfo

        mock_now = real_datetime(2024, 6, 15, 23, 0, tzinfo=ZoneInfo("US/Eastern"))
        mock_datetime.now.return_value = mock_now

        schedule = {"start_hour": 22, "end_hour": 6, "timezone": "US/Eastern"}
        assert _is_in_production_window(schedule) is True

    @patch("deployment_agent.tools.get_device_risk_profile.datetime")
    def test_overnight_window_after_midnight(self, mock_datetime):
        """Device is in production during overnight window (after midnight)."""
        from datetime import datetime as real_datetime
        from zoneinfo import ZoneInfo

        mock_now = real_datetime(2024, 6, 16, 3, 0, tzinfo=ZoneInfo("US/Eastern"))
        mock_datetime.now.return_value = mock_now

        schedule = {"start_hour": 22, "end_hour": 6, "timezone": "US/Eastern"}
        assert _is_in_production_window(schedule) is True

    @patch("deployment_agent.tools.get_device_risk_profile.datetime")
    def test_overnight_window_outside(self, mock_datetime):
        """Device is NOT in production outside overnight window."""
        from datetime import datetime as real_datetime
        from zoneinfo import ZoneInfo

        mock_now = real_datetime(2024, 6, 16, 12, 0, tzinfo=ZoneInfo("US/Eastern"))
        mock_datetime.now.return_value = mock_now

        schedule = {"start_hour": 22, "end_hour": 6, "timezone": "US/Eastern"}
        assert _is_in_production_window(schedule) is False

    @patch("deployment_agent.tools.get_device_risk_profile.datetime")
    def test_boundary_start_hour_inclusive(self, mock_datetime):
        """Start hour is inclusive (device IS in production at exactly start_hour)."""
        from datetime import datetime as real_datetime
        from zoneinfo import ZoneInfo

        mock_now = real_datetime(2024, 6, 15, 8, 0, tzinfo=ZoneInfo("US/Eastern"))
        mock_datetime.now.return_value = mock_now

        schedule = {"start_hour": 8, "end_hour": 17, "timezone": "US/Eastern"}
        assert _is_in_production_window(schedule) is True

    @patch("deployment_agent.tools.get_device_risk_profile.datetime")
    def test_boundary_end_hour_exclusive(self, mock_datetime):
        """End hour is exclusive (device is NOT in production at exactly end_hour)."""
        from datetime import datetime as real_datetime
        from zoneinfo import ZoneInfo

        mock_now = real_datetime(2024, 6, 15, 17, 0, tzinfo=ZoneInfo("US/Eastern"))
        mock_datetime.now.return_value = mock_now

        schedule = {"start_hour": 8, "end_hour": 17, "timezone": "US/Eastern"}
        assert _is_in_production_window(schedule) is False


@pytest.mark.unit
class TestGetDeviceRiskProfile:
    """Tests for the get_device_risk_profile tool function."""

    def test_raises_for_empty_thing_name(self, dynamodb_table):
        """Empty thing_name raises ValueError."""
        with pytest.raises(ValueError, match="thing_name must be a non-empty string"):
            get_device_risk_profile(thing_name="")

    def test_raises_for_whitespace_thing_name(self, dynamodb_table):
        """Whitespace-only thing_name raises ValueError."""
        with pytest.raises(ValueError, match="thing_name must be a non-empty string"):
            get_device_risk_profile(thing_name="   ")

    def test_raises_for_device_not_found(self, dynamodb_table):
        """Missing device raises ValueError."""
        with pytest.raises(ValueError, match="not found in Fleet Inventory"):
            get_device_risk_profile(thing_name="nonexistent-device")

    @patch("deployment_agent.tools.get_device_risk_profile.datetime")
    def test_returns_profile_with_no_schedule(self, mock_datetime, dynamodb_table):
        """Device with no production_schedule returns is_in_production_window=False."""
        _put_device(dynamodb_table, thing_name="sensor-no-schedule")

        result = get_device_risk_profile(thing_name="sensor-no-schedule")

        assert result["thing_name"] == "sensor-no-schedule"
        assert result["criticality"] == "MEDIUM"
        assert result["production_schedule"] is None
        assert result["last_update_result"] is None
        assert result["hardware_revision"] == "rev-3"
        assert result["is_in_production_window"] is False

    @patch("deployment_agent.tools.get_device_risk_profile.datetime")
    def test_returns_profile_in_normal_window(self, mock_datetime, dynamodb_table):
        """Device within normal production window returns is_in_production_window=True."""
        from datetime import datetime as real_datetime
        from zoneinfo import ZoneInfo

        mock_now = real_datetime(2024, 6, 15, 10, 0, tzinfo=ZoneInfo("US/Eastern"))
        mock_datetime.now.return_value = mock_now

        schedule = {"start_hour": 8, "end_hour": 17, "timezone": "US/Eastern"}
        _put_device(
            dynamodb_table,
            thing_name="sensor-in-window",
            production_schedule=schedule,
            last_update_result="SUCCESS",
        )

        result = get_device_risk_profile(thing_name="sensor-in-window")

        assert result["thing_name"] == "sensor-in-window"
        assert result["is_in_production_window"] is True
        assert result["production_schedule"] == schedule
        assert result["last_update_result"] == "SUCCESS"

    @patch("deployment_agent.tools.get_device_risk_profile.datetime")
    def test_returns_profile_in_overnight_window(self, mock_datetime, dynamodb_table):
        """Device within overnight production window returns is_in_production_window=True."""
        from datetime import datetime as real_datetime
        from zoneinfo import ZoneInfo

        mock_now = real_datetime(2024, 6, 15, 23, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
        mock_datetime.now.return_value = mock_now

        schedule = {"start_hour": 22, "end_hour": 6, "timezone": "Asia/Tokyo"}
        _put_device(
            dynamodb_table,
            thing_name="sensor-overnight",
            production_schedule=schedule,
            criticality="HIGH",
        )

        result = get_device_risk_profile(thing_name="sensor-overnight")

        assert result["thing_name"] == "sensor-overnight"
        assert result["criticality"] == "HIGH"
        assert result["is_in_production_window"] is True

    @patch("deployment_agent.tools.get_device_risk_profile.datetime")
    def test_returns_profile_outside_window(self, mock_datetime, dynamodb_table):
        """Device outside production window returns is_in_production_window=False."""
        from datetime import datetime as real_datetime
        from zoneinfo import ZoneInfo

        mock_now = real_datetime(2024, 6, 15, 20, 0, tzinfo=ZoneInfo("Europe/London"))
        mock_datetime.now.return_value = mock_now

        schedule = {"start_hour": 8, "end_hour": 17, "timezone": "Europe/London"}
        _put_device(
            dynamodb_table,
            thing_name="sensor-outside",
            production_schedule=schedule,
        )

        result = get_device_risk_profile(thing_name="sensor-outside")

        assert result["thing_name"] == "sensor-outside"
        assert result["is_in_production_window"] is False
