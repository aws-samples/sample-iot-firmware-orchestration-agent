"""Tool to retrieve device risk profile with production window computation."""

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3
from pydantic import ValidationError
from strands import tool

from deployment_agent.models.device import Device

logger = logging.getLogger(__name__)


def _is_in_production_window(production_schedule: dict | None) -> bool:
    """Determine if a device is currently within its production window.

    Handles both normal windows (start_hour <= end_hour) and overnight
    windows that span midnight (start_hour > end_hour).

    Args:
        production_schedule: Dictionary with start_hour, end_hour, and timezone
            keys, or None if the device has no schedule.

    Returns:
        True if the device is currently in its production window, False otherwise.
        Returns False when no schedule is defined (device is always available).

    """
    if production_schedule is None:
        return False

    start_hour = int(production_schedule["start_hour"])
    end_hour = int(production_schedule["end_hour"])
    timezone_str = production_schedule["timezone"]

    tz = ZoneInfo(timezone_str)
    current_hour = datetime.now(tz=tz).hour

    if start_hour <= end_hour:
        # Normal window: e.g., start=8, end=17 means active from 08:00 to 16:59
        return start_hour <= current_hour < end_hour
    else:
        # Overnight window: e.g., start=22, end=6 means active from 22:00 to 05:59
        return current_hour >= start_hour or current_hour < end_hour


@tool
def get_device_risk_profile(thing_name: str) -> dict:
    """Retrieve the risk profile for a specific device including production window status.

    Fetches the device record from Fleet_Inventory and enriches it with a
    computed is_in_production_window boolean that indicates whether the device
    is currently within its active production hours.

    Args:
        thing_name: The IoT Thing name identifying the device.

    Returns:
        A dictionary containing:
            - thing_name: The device identifier.
            - criticality: The device criticality level (LOW, MEDIUM, HIGH).
            - production_schedule: The schedule dict or None if not defined.
            - last_update_result: The most recent update outcome or None.
            - hardware_revision: The hardware revision string.
            - is_in_production_window: Whether the device is currently in its
              active production window.

    Raises:
        ValueError: If thing_name is empty or the device is not found in
            Fleet_Inventory.

    """
    if not thing_name or not thing_name.strip():
        raise ValueError("thing_name must be a non-empty string")

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ.get("FLEET_INVENTORY_TABLE", "FleetInventory"))

    response = table.get_item(Key={"thing_name": thing_name})
    item = response.get("Item")

    if item is None:
        raise ValueError(f"Device '{thing_name}' not found in Fleet Inventory")

    # Validate through Device model
    try:
        device = Device.model_validate(item)
    except ValidationError as e:
        logger.warning(
            "Device record validation failed for %s: %s. Using raw item.",
            thing_name,
            str(e),
        )
        # Fallback to raw item access for partially valid records
        production_schedule = item.get("production_schedule")
        in_production = _is_in_production_window(production_schedule)
        return {
            "thing_name": item.get("thing_name", thing_name),
            "criticality": item.get("criticality", "MEDIUM"),
            "production_schedule": production_schedule,
            "last_update_result": item.get("last_update_result"),
            "hardware_revision": item.get("hardware_revision", "unknown"),
            "is_in_production_window": in_production,
        }

    production_schedule_dict = (
        device.production_schedule.model_dump() if device.production_schedule else None
    )
    in_production = _is_in_production_window(production_schedule_dict)

    return {
        "thing_name": device.thing_name,
        "criticality": device.criticality.value,
        "production_schedule": production_schedule_dict,
        "last_update_result": device.last_update_result.value if device.last_update_result else None,
        "hardware_revision": device.hardware_revision,
        "is_in_production_window": in_production,
    }
