"""EventBridge input parser Lambda.

Transforms S3 Object Created events into valid state machine input by parsing
the firmware filename convention: firmware/{device_type}-v{version}.bin

This Lambda is invoked by EventBridge and starts the Step Functions workflow
with correctly structured input.
"""

import json
import logging
import os
import re

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Pattern: firmware/{device_type}-v{version}.bin
# device_type: alphanumeric with hyphens and underscores
# version: semantic version (major.minor.patch)
FIRMWARE_KEY_PATTERN = re.compile(
    r"^firmware/(?P<device_type>[a-zA-Z0-9][a-zA-Z0-9_-]*)-v(?P<version>\d+\.\d+\.\d+)\.bin$"
)

STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")


def handler(event: dict, context=None) -> dict:
    """Parse S3 event and start the deployment state machine with valid input.

    Extracts bucket name and object key from the EventBridge S3 Object Created
    event, parses device_type and target_version from the filename convention,
    constructs a valid s3:// URL, and starts the state machine execution.

    Args:
        event: EventBridge event with detail.bucket.name and detail.object.key.
        context: Lambda context (unused).

    Returns:
        Dictionary with execution ARN and parsed input fields.

    Raises:
        ValueError: If the S3 key does not match the expected firmware pattern.

    """
    bucket = event["detail"]["bucket"]["name"]
    key = event["detail"]["object"]["key"]

    logger.info("Parsing firmware upload event: bucket=%s, key=%s", bucket, key)

    match = FIRMWARE_KEY_PATTERN.match(key)
    if not match:
        raise ValueError(
            f"S3 key '{key}' does not match expected pattern: "
            "firmware/{device_type}-v{version}.bin"
        )

    device_type = match.group("device_type")
    target_version = match.group("version")
    firmware_s3_url = f"s3://{bucket}/{key}"

    logger.info(
        "Parsed firmware event: device_type=%s, target_version=%s, url=%s",
        device_type,
        target_version,
        firmware_s3_url,
    )

    # Start the state machine execution
    sfn_client = boto3.client("stepfunctions")

    sfn_input = {
        "firmware_s3_url": firmware_s3_url,
        "device_type": device_type,
        "target_version": target_version,
    }

    response = sfn_client.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        input=json.dumps(sfn_input),
    )

    logger.info("Started state machine execution: %s", response["executionArn"])

    return {
        "execution_arn": response["executionArn"],
        "firmware_s3_url": firmware_s3_url,
        "device_type": device_type,
        "target_version": target_version,
    }
