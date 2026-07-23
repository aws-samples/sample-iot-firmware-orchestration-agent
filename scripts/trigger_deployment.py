#!/usr/bin/env python3
"""Trigger a firmware deployment by uploading firmware to S3.

Uploads a dummy firmware binary to the expected S3 key path and stores
scenario metadata alongside it. EventBridge picks up the S3 Object Created
event and starts the Step Functions deployment workflow automatically.

Usage:
    python scripts/trigger_deployment.py --scenario successful_rollout --version 2.0.0
    python scripts/trigger_deployment.py --scenario canary_failure
    python scripts/trigger_deployment.py --scenario partial_connectivity_loss --device-type pressure-sensor
"""

import argparse
import json
import os
from datetime import UTC, datetime

import boto3

# ---------------------------------------------------------------------------
# Scenario configurations
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, dict] = {
    "successful_rollout": {
        "description": "99% success, 1% timeout. PROCEED all waves, deployment COMPLETED.",
        "success_rate": 0.99,
        "failure_distribution": {"timeout": 1.0},
    },
    "canary_failure": {
        "description": "Boot-loop in canary wave. Immediate ROLLBACK, no subsequent waves.",
        "success_rate": 0.80,
        "failure_distribution": {"boot_loop": 1.0},
        "target_wave": "canary",
    },
    "partial_connectivity_loss": {
        "description": "Connectivity loss in one facility (95-98% success). PAUSE then ROLLBACK after 3 PAUSEs.",
        "success_rate": 0.96,
        "failure_distribution": {"connectivity_lost": 1.0},
        "target_facility": "factory-east",
    },
}


def upload_firmware(
    bucket: str,
    device_type: str,
    version: str,
    scenario_name: str,
    region: str,
) -> str:
    """Upload a dummy firmware binary and scenario metadata to S3.

    Args:
        bucket: S3 bucket name for firmware storage.
        device_type: Target device type identifier.
        version: Target firmware version string.
        scenario_name: Name of the deployment scenario to configure.
        region: AWS region for the S3 client.

    Returns:
        The S3 key where firmware was uploaded.

    """
    s3_client = boto3.client("s3", region_name=region)

    firmware_key = f"firmware/{device_type}-v{version}.bin"
    metadata_key = f"firmware/{device_type}-v{version}.metadata.json"

    # Create a dummy firmware binary payload
    firmware_content = (f"FIRMWARE_BINARY:{device_type}:v{version}:{datetime.now(UTC).isoformat()}").encode()

    # Build scenario metadata
    scenario_config = SCENARIOS[scenario_name]
    metadata = {
        "scenario": scenario_name,
        "device_type": device_type,
        "target_version": version,
        "uploaded_at": datetime.now(UTC).isoformat(),
        "config": scenario_config,
    }

    # Upload firmware binary
    s3_client.put_object(
        Bucket=bucket,
        Key=firmware_key,
        Body=firmware_content,
        ContentType="application/octet-stream",
        Metadata={
            "scenario": scenario_name,
            "device-type": device_type,
            "target-version": version,
        },
    )
    print(f"  Uploaded firmware: s3://{bucket}/{firmware_key}")

    # Upload scenario metadata JSON
    s3_client.put_object(
        Bucket=bucket,
        Key=metadata_key,
        Body=json.dumps(metadata, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"  Uploaded metadata: s3://{bucket}/{metadata_key}")

    return firmware_key


def print_scenario_info(scenario_name: str, device_type: str, version: str) -> None:
    """Print scenario configuration and expected outcomes."""
    scenario = SCENARIOS[scenario_name]

    print(f"\nScenario: {scenario_name}")
    print(f"  Description: {scenario['description']}")
    print(f"  Device type: {device_type}")
    print(f"  Target version: {version}")
    print(f"  Expected success rate: {scenario['success_rate'] * 100:.0f}%")
    print(f"  Failure types: {json.dumps(scenario['failure_distribution'])}")

    if "target_wave" in scenario:
        print(f"  Target wave: {scenario['target_wave']}")
    if "target_facility" in scenario:
        print(f"  Target facility: {scenario['target_facility']}")

    print("\nExpected outcome:")
    match scenario_name:
        case "successful_rollout":
            print("  - PROCEED through all waves")
            print("  - Deployment status: COMPLETED")
            print("  - 1% of devices timeout (acceptable)")
        case "canary_failure":
            print("  - Boot-loop detected in canary wave")
            print("  - Immediate ROLLBACK triggered")
            print("  - No subsequent waves executed")
        case "partial_connectivity_loss":
            print("  - Connectivity loss in factory-east facility")
            print("  - Success rate 95-98% triggers PAUSE")
            print("  - After 3 PAUSEs, escalates to ROLLBACK")


def main() -> None:
    """Parse arguments and trigger the firmware deployment."""
    parser = argparse.ArgumentParser(description="Trigger a firmware deployment scenario by uploading firmware to S3.")
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()),
        required=True,
        help="Deployment scenario to simulate",
    )
    parser.add_argument(
        "--version",
        default="2.0.0",
        help="Target firmware version (default: 2.0.0)",
    )
    parser.add_argument(
        "--device-type",
        default="temperature-sensor",
        help="Device type to target (default: temperature-sensor)",
    )
    args = parser.parse_args()

    bucket = os.environ.get("FIRMWARE_BUCKET", "iot-firmware-bucket")
    region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))

    print(f"Triggering deployment scenario: {args.scenario}")
    print(f"  Bucket: {bucket}")
    print(f"  Region: {region}")

    firmware_key = upload_firmware(
        bucket=bucket,
        device_type=args.device_type,
        version=args.version,
        scenario_name=args.scenario,
        region=region,
    )

    print_scenario_info(args.scenario, args.device_type, args.version)

    print(f"\nEventBridge will detect the S3 upload at '{firmware_key}' and start")
    print("the Step Functions deployment workflow automatically.")


if __name__ == "__main__":
    main()
