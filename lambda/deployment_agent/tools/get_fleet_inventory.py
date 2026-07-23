"""Tool to query fleet inventory by device type with semver filtering."""

import os

import boto3
from boto3.dynamodb.conditions import Key
from packaging.version import InvalidVersion, Version
from strands import tool


@tool
def get_fleet_inventory(device_type: str, max_version: str) -> list[dict]:
    """Query fleet inventory for devices of a specific type below a target firmware version.

    Queries the Fleet_Inventory DynamoDB table using the device_type-index GSI
    and filters results to devices with firmware_version less than max_version
    using semantic version comparison.

    Args:
        device_type: The device type to query (e.g., "sensor-v2", "gateway-pro").
        max_version: Semantic version string. Only devices with firmware_version
            less than this value are returned.

    Returns:
        A list of device record dictionaries matching the criteria.

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

    filtered_devices = [item for item in items if Version(item["firmware_version"]) < target_version]

    return filtered_devices
