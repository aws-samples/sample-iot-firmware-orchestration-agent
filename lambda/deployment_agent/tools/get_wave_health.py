"""Tool to assess wave health by querying IoT Job execution statuses."""

import logging
import os

import boto3
from strands import tool

logger = logging.getLogger(__name__)


def _classify_failure(status: str, status_details: dict | None) -> str:
    """Classify a failed execution into a specific failure type.

    Args:
        status: The IoT Job execution status (TIMED_OUT, REJECTED, FAILED).
        status_details: Optional dictionary containing a detailsMap with
            additional context about the failure.

    Returns:
        A failure classification string: "timeout", "version_mismatch",
        "connectivity_lost", or "boot_loop".

    """
    if status == "TIMED_OUT":
        return "timeout"

    if status == "REJECTED":
        return "version_mismatch"

    # For FAILED status, inspect the detailsMap for indicators
    if status == "FAILED":
        details_map = {}
        if status_details and isinstance(status_details, dict):
            details_map = status_details.get("detailsMap", {})

        # Check all values in the details map for indicators
        details_values = " ".join(str(v).lower() for v in details_map.values())

        if "restart" in details_values:
            return "boot_loop"
        if "disconnect" in details_values:
            return "connectivity_lost"

        # Default FAILED classification (conservative)
        return "connectivity_lost"

    # Fallback for unexpected statuses
    return "connectivity_lost"


@tool
def get_wave_health(job_id: str) -> dict:
    """Assess the health of a deployment wave by analyzing IoT Job execution statuses.

    Queries all job executions for the given IoT Job, classifies failures into
    categories (timeout, version_mismatch, connectivity_lost, boot_loop), and
    calculates overall success metrics. Returns per-device failure details
    including thing names of failed devices for targeted rollback.

    Args:
        job_id: The IoT Job identifier to assess.

    Returns:
        A dictionary containing:
            - total_devices: Total number of device executions in the wave.
            - succeeded_count: Number of executions with SUCCEEDED status.
            - failed_count: Number of executions with FAILED, TIMED_OUT, or
              REJECTED status.
            - timed_out_count: Number of executions with TIMED_OUT status.
            - in_progress_count: Number of executions still IN_PROGRESS or QUEUED.
            - success_rate: Percentage of succeeded devices rounded to 1 decimal.
            - failure_types: Mapping of failure classification to count
              (boot_loop, connectivity_lost, version_mismatch, timeout).
            - failed_thing_names: List of thing names that failed (for targeted rollback).
            - failure_details: List of per-device failure details with thing_name,
              status, and failure_type.

    Raises:
        ValueError: If job_id is empty.

    """
    if not job_id or not job_id.strip():
        raise ValueError("job_id must be a non-empty string")

    iot_client = boto3.client("iot")

    # Collect all executions with pagination
    executions: list[dict] = []
    request_kwargs: dict = {"jobId": job_id}

    while True:
        response = iot_client.list_job_executions_for_job(**request_kwargs)
        executions.extend(response.get("executionSummaries", []))

        next_token = response.get("nextToken")
        if not next_token:
            break
        request_kwargs["nextToken"] = next_token

    # Initialize counters
    succeeded_count = 0
    failed_count = 0
    timed_out_count = 0
    in_progress_count = 0
    failure_types: dict[str, int] = {
        "boot_loop": 0,
        "connectivity_lost": 0,
        "version_mismatch": 0,
        "timeout": 0,
    }
    failed_thing_names: list[str] = []
    failure_details: list[dict] = []

    # Classify each execution
    for execution in executions:
        status = execution.get("status", "")
        thing_arn = execution.get("thingArn", "")
        # Extract thing_name from ARN: arn:aws:iot:region:account:thing/{thing_name}
        thing_name = thing_arn.split("/")[-1] if thing_arn else ""

        if status == "SUCCEEDED":
            succeeded_count += 1
        elif status == "IN_PROGRESS" or status == "QUEUED":
            in_progress_count += 1
        elif status in ("FAILED", "TIMED_OUT", "REJECTED"):
            failed_count += 1
            if status == "TIMED_OUT":
                timed_out_count += 1
            status_details = execution.get("statusDetails")
            classification = _classify_failure(status, status_details)
            failure_types[classification] += 1

            # Track failed device names for targeted rollback
            if thing_name:
                failed_thing_names.append(thing_name)
                failure_details.append({
                    "thing_name": thing_name,
                    "status": status,
                    "failure_type": classification,
                })
        # REMOVED and CANCELED are counted in total but not classified

    total_devices = len(executions)
    success_rate = round(succeeded_count / total_devices * 100, 1) if total_devices > 0 else 0.0

    # Enrich failure details with hardware_revision from FleetInventory
    if failure_details:
        failure_details = _enrich_with_hardware_revision(failure_details)

    return {
        "total_devices": total_devices,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
        "timed_out_count": timed_out_count,
        "in_progress_count": in_progress_count,
        "success_rate": success_rate,
        "failure_types": failure_types,
        "failed_thing_names": failed_thing_names,
        "failure_details": failure_details,
    }


def _enrich_with_hardware_revision(failure_details: list[dict]) -> list[dict]:
    """Enrich failure details with hardware_revision from FleetInventory.

    Uses BatchGetItem to fetch hardware_revision for failed devices, enabling
    the agent to evaluate hardware revision correlation rules.

    Args:
        failure_details: List of failure detail dicts with thing_name, status,
            and failure_type.

    Returns:
        The same list with hardware_revision added to each entry.

    """
    table_name = os.environ.get("FLEET_INVENTORY_TABLE", "FleetInventory")
    dynamodb = boto3.resource("dynamodb")

    thing_names = [d["thing_name"] for d in failure_details if d.get("thing_name")]
    if not thing_names:
        return failure_details

    revision_map: dict[str, str] = {}

    # BatchGetItem supports up to 100 keys per call
    for i in range(0, len(thing_names), 100):
        batch = thing_names[i : i + 100]
        keys = [{"thing_name": name} for name in batch]

        try:
            response = dynamodb.meta.client.batch_get_item(
                RequestItems={
                    table_name: {
                        "Keys": keys,
                        "ProjectionExpression": "thing_name, hardware_revision",
                    }
                }
            )

            for item in response.get("Responses", {}).get(table_name, []):
                revision_map[item["thing_name"]] = item.get("hardware_revision", "unknown")

            # Handle unprocessed keys with a single retry
            unprocessed = response.get("UnprocessedKeys", {}).get(table_name)
            if unprocessed:
                retry_response = dynamodb.meta.client.batch_get_item(
                    RequestItems={table_name: unprocessed}
                )
                for item in retry_response.get("Responses", {}).get(table_name, []):
                    revision_map[item["thing_name"]] = item.get("hardware_revision", "unknown")

        except Exception:
            logger.warning("Failed to enrich failure details with hardware_revision", exc_info=True)

    # Add hardware_revision to each failure detail
    for detail in failure_details:
        detail["hardware_revision"] = revision_map.get(detail.get("thing_name", ""), "unknown")

    return failure_details
