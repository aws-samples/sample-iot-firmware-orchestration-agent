"""Tool to create an IoT Job for a deployment wave."""

import json

import boto3
from strands import tool

from shared.constants import MAX_WAVE_BATCH_SIZE
from shared.job_id import build_job_id


@tool
def create_deployment_wave(
    deployment_id: str,
    wave_number: int,
    thing_names: list[str],
    firmware_s3_url: str,
    timeout_minutes: int = 30,
) -> dict:
    """Create a deployment wave by launching an IoT Job targeting specific devices.

    Validates all inputs, generates a deterministic job ID, and creates an
    AWS IoT Job with SNAPSHOT target selection and timeout configuration.

    Args:
        deployment_id: Unique identifier for the deployment.
        wave_number: The wave sequence number (must be a positive integer).
        thing_names: List of IoT Thing names to target (max 500, non-empty).
        firmware_s3_url: S3 URL of the firmware binary (must start with s3://).
        timeout_minutes: Timeout in minutes for in-progress executions.
            Defaults to 30.

    Returns:
        A dictionary containing:
            - job_id: The generated IoT Job identifier.
            - job_arn: The ARN of the created IoT Job.
            - target_count: The number of devices targeted.

    Raises:
        ValueError: If any input parameter is invalid.

    """
    # --- Input validation ---
    if not deployment_id or not deployment_id.strip():
        raise ValueError("deployment_id must be a non-empty string")

    if not isinstance(wave_number, int) or wave_number < 1:
        raise ValueError("wave_number must be a positive integer")

    if not thing_names:
        raise ValueError("thing_names must be a non-empty list")

    if len(thing_names) > MAX_WAVE_BATCH_SIZE:
        raise ValueError(f"thing_names must not exceed {MAX_WAVE_BATCH_SIZE} items, got {len(thing_names)}")

    for i, name in enumerate(thing_names):
        if not name or not name.strip():
            raise ValueError(f"thing_names[{i}] must be a non-empty string")

    if not firmware_s3_url or not firmware_s3_url.startswith("s3://"):
        raise ValueError("firmware_s3_url must match the s3:// pattern")

    if not isinstance(timeout_minutes, int) or timeout_minutes < 1:
        raise ValueError("timeout_minutes must be a positive integer")

    # --- Resolve AWS account and region for thing ARNs ---
    session = boto3.session.Session()
    region = session.region_name

    sts_client = boto3.client("sts")
    account_id = sts_client.get_caller_identity()["Account"]

    # --- Generate job ID using shared helper (IoT job IDs only allow [a-zA-Z0-9_-]) ---
    job_id = build_job_id(deployment_id, wave_number)

    # --- Build thing ARN targets ---
    targets = [f"arn:aws:iot:{region}:{account_id}:thing/{name}" for name in thing_names]

    # --- Create IoT Job ---
    job_document = json.dumps({"firmware_url": firmware_s3_url})

    iot_client = boto3.client("iot")

    try:
        response = iot_client.create_job(
            jobId=job_id,
            targets=targets,
            document=job_document,
            targetSelection="SNAPSHOT",
            timeoutConfig={"inProgressTimeoutInMinutes": timeout_minutes},
        )
        job_arn = response["jobArn"]
    except iot_client.exceptions.ResourceAlreadyExistsException:
        # Job already exists (retry scenario). Describe the existing job.
        describe_response = iot_client.describe_job(jobId=job_id)
        job_arn = describe_response["job"]["jobArn"]

    return {
        "job_id": job_id,
        "job_arn": job_arn,
        "target_count": len(thing_names),
    }
