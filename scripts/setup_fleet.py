#!/usr/bin/env python3
"""Register simulated IoT devices and populate the fleet inventory table."""

import argparse
import itertools
import os
from datetime import UTC, datetime

import boto3

# ---------------------------------------------------------------------------
# Fleet configuration
# ---------------------------------------------------------------------------

DEVICE_TYPES: list[str] = [
    "temperature-sensor",
    "pressure-sensor",
    "flow-meter",
    "vibration-monitor",
]

FACILITIES: list[str] = [
    "factory-east",
    "factory-west",
    "factory-central",
]

HARDWARE_REVISIONS: list[str] = [
    "rev-A",
    "rev-B",
    "rev-C",
]

# Production schedules by facility
PRODUCTION_SCHEDULES: dict[str, dict] = {
    "factory-east": {
        "start_hour": 8,
        "end_hour": 17,
        "timezone": "US/Eastern",
    },
    "factory-west": {
        "start_hour": 6,
        "end_hour": 14,
        "timezone": "US/Pacific",
    },
    "factory-central": {
        "start_hour": 22,
        "end_hour": 6,
        "timezone": "US/Central",
    },
}

# Criticality distribution: 50% LOW, 30% MEDIUM, 20% HIGH
CRITICALITY_DISTRIBUTION: list[tuple[str, float]] = [
    ("LOW", 0.50),
    ("MEDIUM", 0.30),
    ("HIGH", 0.20),
]


def _assign_criticality(index: int, total: int) -> str:
    """Assign criticality based on device index position within the fleet.

    Distributes devices into 50% LOW, 30% MEDIUM, 20% HIGH buckets
    based on their sequential position.
    """
    low_count = round(total * 0.50)
    medium_count = round(total * 0.30)

    if index < low_count:
        return "LOW"
    elif index < low_count + medium_count:
        return "MEDIUM"
    else:
        return "HIGH"


def _build_device_assignments(num_devices: int) -> list[dict]:
    """Build the full list of device assignments with all attributes.

    Distributes devices round-robin across device types, facilities,
    and hardware revisions to get even spread.
    """
    # Create a cyclic iterator over device_type x facility x hardware_revision
    combos = list(itertools.product(DEVICE_TYPES, FACILITIES, HARDWARE_REVISIONS))
    combo_cycle = itertools.cycle(combos)

    devices = []
    for i in range(num_devices):
        device_type, facility, hw_rev = next(combo_cycle)
        thing_name = f"sim-{device_type}-{facility}-{i:04d}"
        criticality = _assign_criticality(i, num_devices)

        devices.append(
            {
                "thing_name": thing_name,
                "device_type": device_type,
                "facility": facility,
                "hardware_revision": hw_rev,
                "criticality": criticality,
                "production_schedule": PRODUCTION_SCHEDULES[facility],
            }
        )

    return devices


def register_devices(num_devices: int) -> None:
    """Register IoT Things and populate FleetInventory DynamoDB table."""
    table_name = os.environ.get("FLEET_INVENTORY_TABLE", "FleetInventory")
    region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))

    iot_client = boto3.client("iot", region_name=region)
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    devices = _build_device_assignments(num_devices)
    registered_at = datetime.now(UTC).isoformat()

    print(f"Registering {num_devices} IoT devices...")
    print(f"  Table: {table_name}")
    print(f"  Region: {region}")
    print(f"  Device types: {len(DEVICE_TYPES)}")
    print(f"  Facilities: {len(FACILITIES)}")
    print(f"  Hardware revisions: {len(HARDWARE_REVISIONS)}")
    print()

    for i, device in enumerate(devices, start=1):
        thing_name = device["thing_name"]

        # Register IoT Thing
        iot_client.create_thing(thingName=thing_name)

        # Write to FleetInventory DynamoDB table
        item = {
            "thing_name": thing_name,
            "device_type": device["device_type"],
            "firmware_version": "1.0.0",
            "criticality": device["criticality"],
            "location": device["facility"],
            "facility": device["facility"],
            "production_schedule": device["production_schedule"],
            "hardware_revision": device["hardware_revision"],
            "registered_at": registered_at,
        }
        table.put_item(Item=item)

        # Print progress every 10 devices or on the last one
        if i % 10 == 0 or i == num_devices:
            print(f"  [{i}/{num_devices}] Registered {thing_name}")

    print()
    print(f"Successfully registered {num_devices} devices.")
    _print_summary(devices)


def _print_summary(devices: list[dict]) -> None:
    """Print a distribution summary of registered devices."""
    print("\nDistribution summary:")

    # Criticality breakdown
    criticality_counts: dict[str, int] = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    for d in devices:
        criticality_counts[d["criticality"]] += 1
    total = len(devices)
    print("  Criticality:")
    for level, count in criticality_counts.items():
        pct = (count / total) * 100
        print(f"    {level}: {count} ({pct:.0f}%)")

    # Device type breakdown
    type_counts: dict[str, int] = {}
    for d in devices:
        type_counts[d["device_type"]] = type_counts.get(d["device_type"], 0) + 1
    print("  Device types:")
    for dtype, count in sorted(type_counts.items()):
        print(f"    {dtype}: {count}")

    # Facility breakdown
    facility_counts: dict[str, int] = {}
    for d in devices:
        facility_counts[d["facility"]] = facility_counts.get(d["facility"], 0) + 1
    print("  Facilities:")
    for facility, count in sorted(facility_counts.items()):
        print(f"    {facility}: {count}")


def main() -> None:
    """Parse arguments and register simulated IoT fleet."""
    parser = argparse.ArgumentParser(description="Register simulated IoT devices for the firmware deployment agent.")
    parser.add_argument(
        "--devices",
        type=int,
        default=100,
        help="Number of devices to register (default: 100, minimum: 20)",
    )
    args = parser.parse_args()

    if args.devices < 20:
        parser.error("Minimum 20 devices required for meaningful wave splits")

    register_devices(args.devices)


if __name__ == "__main__":
    main()
