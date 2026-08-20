"""Tool to query fleet inventory by device type with semver filtering."""

import logging
import os

import boto3
from boto3.dynamodb.conditions import Key
from packaging.version import InvalidVersion, Version
from pydantic import ValidationError
from strands import tool

from deployment_agent.models.device import Device

logger = logging.getLogger(__name__)


@tool
def get_fleet_inventory(device_type: str, max_version: str) -> list[dict]:
    """Query fleet inventory for devices of a specific type below a target firmware version.

    Queries the Fleet_Inventory DynamoDB table using the device_type-index GSI
    and filters results to devices with firmware_version less than max_version
    using semantic version comparison. Validates each item through the Device
    model; malformed rows are logged and skipped.

    Args:
        device_type: The device type to query (e.g., "sensor-v2", "gateway-pro").
        max_version: Semantic version string. Only devices with firmware_version
            less than this value are returned.

    Returns:
        A list of validated device record dictionaries matching the criteria.

    Raises:
        ValueError: If device_type is empty or max_version is not a valid
            semantic version string.

    """
    if not device_type or not device_type.strip():
        raise ValueError("device_type must be a non-empty string")

    try:
        target_version = Version(max_version)
    except (InvalidVersion, TypeError) as err:
        raise ValueError(f"max_version '{max_version}' is not a valid semantic version") from err

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ.get("FLEET_INVENTORY_TABLE", "FleetInventory"))

    items: list[dict] = []
    query_kwargs = {
        "IndexName": "device_type-index",
        "KeyConditionExpression": Key("device_type").eq(device_type),
    }

    while True:
        response = table.query(**query_kwargs)
        items.extend(response.get("Items", []))

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        query_kwargs["ExclusiveStartKey"] = last_key

    # Validate each item through the Device model
    validated_devices: list[dict] = []
    skipped_count = 0

    for item in items:
        try:
            device = Device.model_validate(item)
        except ValidationError as e:
            logger.warning(
                "Skipping malformed device record: thing_name=%s, error=%s",
                item.get("thing_name", "unknown"),
                str(e),
            )
            skipped_count += 1
            continue

        # Filter by firmware version
        try:
            if Version(device.firmware_version) < target_version:
                validated_devices.append(device.model_dump(mode="json"))
        except InvalidVersion:
            logger.warning(
                "Skipping device with invalid firmware_version: thing_name=%s, version=%s",
                device.thing_name,
                device.firmware_version,
            )
            skipped_count += 1

    if skipped_count > 0:
        logger.info("Fleet inventory query: returned=%d, skipped=%d", len(validated_devices), skipped_count)

    return validated_devices
