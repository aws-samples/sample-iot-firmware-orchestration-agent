"""Tool to roll back a failed deployment wave by cancelling the current job and deploying previous firmware."""

import json
import logging

import boto3
from botocore.exceptions import ClientError
from strands import tool

from shared.constants import WAVE_TIMEOUT_MINUTES
from shared.job_id import build_job_id

logger = logging.getLogger(__name__)


@tool
def rollback_wave(
    job_id: str,
    failed_thing_names: list[str],
    previous_firmware_s3_url: str,
) -> dict:
    """Roll back a failed wave by cancelling the current job and deploying previous firmware.

    Cancels the specified IoT Job with force=True (gracefully handling
    already-completed jobs), then creates a new rollback IoT Job targeting
    only the failed devices with the previous firmware S3 URL.

    Args:
        job_id: The IoT Job ID of the failed deployment wave to cancel.
        failed_thing_names: List of IoT Thing names that failed and need rollback.
        previous_firmware_s3_url: S3 URL of the previous firmware binary to restore.

    Returns:
        A dictionary containing:
            - rollback_job_id: The generated rollback IoT Job identifier.
            - rollback_job_arn: The ARN of the created rollback IoT Job.
            - target_count: The number of failed devices targeted for rollback.
            - cancelled_job_id: The original job ID that was cancelled.

    Raises:
        ValueError: If any input parameter is invalid.

    """
    # --- Input validation ---
    if not job_id or not job_id.strip():
        raise ValueError("job_id must be a non-empty string")

    if not failed_thing_names:
        raise ValueError("failed_thing_names must be a non-empty list")

    for i, name in enumerate(failed_thing_names):
        if not name or not name.strip():
            raise ValueError(f"failed_thing_names[{i}] must be a non-empty string")

    if not previous_firmware_s3_url or not previous_firmware_s3_url.startswith("s3://"):
        raise ValueError("previous_firmware_s3_url must match the s3:// pattern")

    # --- Cancel the current job (force=True, catch already-completed errors) ---
    iot_client = boto3.client("iot")

    try:
        iot_client.cancel_job(jobId=job_id, force=True)
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code in ("InvalidStateTransitionException", "ResourceNotFoundException"):
            logger.warning(
                "Could not cancel job %s (error: %s). Proceeding with rollback.",
                job_id,
                error_code,
            )
        else:
            raise

    # --- Resolve AWS account and region for thing ARNs ---
    session = boto3.session.Session()
    region = session.region_name

    sts_client = boto3.client("sts")
    account_id = sts_client.get_caller_identity()["Account"]

    # --- Generate rollback job ID using shared helper ---
    rollback_job_id = f"rollback-{job_id}"

    # --- Build thing ARN targets ---
    targets = [f"arn:aws:iot:{region}:{account_id}:thing/{name}" for name in failed_thing_names]

    # --- Create rollback IoT Job (handle ResourceAlreadyExistsException for retry idempotency) ---
    job_document = json.dumps({"firmware_url": previous_firmware_s3_url, "rollback": True})

    try:
        response = iot_client.create_job(
            jobId=rollback_job_id,
            targets=targets,
            document=job_document,
            targetSelection="SNAPSHOT",
            timeoutConfig={"inProgressTimeoutInMinutes": WAVE_TIMEOUT_MINUTES},
        )
        rollback_job_arn = response["jobArn"]
    except iot_client.exceptions.ResourceAlreadyExistsException:
        # Retry scenario: rollback job already exists. Describe and return existing.
        logger.warning(
            "Rollback job %s already exists (retry scenario). Using existing job.",
            rollback_job_id,
        )
        describe_response = iot_client.describe_job(jobId=rollback_job_id)
        rollback_job_arn = describe_response["job"]["jobArn"]

    return {
        "rollback_job_id": rollback_job_id,
        "rollback_job_arn": rollback_job_arn,
        "target_count": len(failed_thing_names),
        "cancelled_job_id": job_id,
    }
