"""Tool to write per-device update outcomes to the FleetInventory table."""

import logging
import os
from datetime import datetime, timezone

import boto3

logger = logging.getLogger(__name__)


def write_device_outcomes(
    thing_names: list[str],
    failed_thing_names: list[str],
    target_version: str,
    deployment_id: str,
) -> dict:
    """Write last_update_result to FleetInventory for each device in the wave.

    Updates each device's record with the outcome of the firmware deployment,
    enabling canary ordering by prior success in subsequent deployments.

    Args:
        thing_names: All device thing names in the wave.
        failed_thing_names: Device thing names that failed the update.
        target_version: The firmware version that was deployed.
        deployment_id: The deployment identifier for correlation.

    Returns:
        A dictionary with updated_count and failed_to_write_count.

    """
    table_name = os.environ.get("FLEET_INVENTORY_TABLE", "FleetInventory")
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    failed_set = set(failed_thing_names)
    timestamp = datetime.now(tz=timezone.utc).isoformat()
    updated_count = 0
    failed_to_write_count = 0

    with table.batch_writer() as batch:
        for thing_name in thing_names:
            result = "FAILURE" if thing_name in failed_set else "SUCCESS"
            try:
                batch.put_item(
                    Item={
                        "thing_name": thing_name,
                        "last_update_result": result,
                        "last_update_version": target_version,
                        "last_update_deployment_id": deployment_id,
                        "last_update_timestamp": timestamp,
                    }
                )
                updated_count += 1
            except Exception:
                logger.warning(
                    "Failed to write outcome for device %s",
                    thing_name,
                    exc_info=True,
                )
                failed_to_write_count += 1

    logger.info(
        "Wrote device outcomes: updated=%d, failed_to_write=%d",
        updated_count,
        failed_to_write_count,
    )

    return {
        "updated_count": updated_count,
        "failed_to_write_count": failed_to_write_count,
    }
