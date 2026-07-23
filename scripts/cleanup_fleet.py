#!/usr/bin/env python3
"""Remove all simulated IoT devices and clean up DynamoDB records."""

import os

import boto3


def cleanup_fleet() -> None:
    """Remove all simulated IoT Things and associated DynamoDB records."""
    region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    fleet_table_name = os.environ.get("FLEET_INVENTORY_TABLE", "FleetInventory")
    history_table_name = os.environ.get("DEPLOYMENT_HISTORY_TABLE", "DeploymentHistory")

    iot_client = boto3.client("iot", region_name=region)
    dynamodb = boto3.resource("dynamodb", region_name=region)

    fleet_table = dynamodb.Table(fleet_table_name)
    history_table = dynamodb.Table(history_table_name)

    print("Starting fleet cleanup...")
    print(f"  Region: {region}")
    print(f"  Fleet table: {fleet_table_name}")
    print(f"  History table: {history_table_name}")
    print()

    # Step 1: Delete IoT Things with names starting with "sim-"
    things_deleted = _delete_iot_things(iot_client)

    # Step 2: Delete FleetInventory records with thing_name starting with "sim-"
    fleet_deleted = _delete_fleet_inventory_records(fleet_table)

    # Step 3: Delete all DeploymentHistory records
    history_deleted = _delete_deployment_history_records(history_table)

    # Print summary
    print()
    print("Cleanup complete:")
    print(f"  IoT Things deleted: {things_deleted}")
    print(f"  FleetInventory records deleted: {fleet_deleted}")
    print(f"  DeploymentHistory records deleted: {history_deleted}")


def _delete_iot_things(iot_client) -> int:
    """Scan IoT Core for things with names starting with 'sim-' and delete them."""
    print("Scanning for simulated IoT Things (sim-*)...")
    deleted_count = 0
    next_token = None

    while True:
        kwargs: dict = {"maxResults": 250}
        if next_token:
            kwargs["nextToken"] = next_token

        response = iot_client.list_things(**kwargs)
        things = response.get("things", [])

        for thing in things:
            thing_name = thing["thingName"]
            if thing_name.startswith("sim-"):
                iot_client.delete_thing(thingName=thing_name)
                deleted_count += 1
                if deleted_count % 10 == 0:
                    print(f"  Deleted {deleted_count} IoT Things...")

        next_token = response.get("nextToken")
        if not next_token:
            break

    print(f"  Total IoT Things deleted: {deleted_count}")
    return deleted_count


def _delete_fleet_inventory_records(table) -> int:
    """Delete FleetInventory records where thing_name starts with 'sim-'."""
    print("Scanning FleetInventory for simulated device records...")
    deleted_count = 0
    last_evaluated_key = None

    while True:
        kwargs: dict = {}
        if last_evaluated_key:
            kwargs["ExclusiveStartKey"] = last_evaluated_key

        response = table.scan(**kwargs)
        items = response.get("Items", [])

        sim_items = [item for item in items if item["thing_name"].startswith("sim-")]

        if sim_items:
            with table.batch_writer() as batch:
                for item in sim_items:
                    batch.delete_item(Key={"thing_name": item["thing_name"]})
                    deleted_count += 1

            if deleted_count % 25 == 0 or not response.get("LastEvaluatedKey"):
                print(f"  Deleted {deleted_count} FleetInventory records...")

        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

    print(f"  Total FleetInventory records deleted: {deleted_count}")
    return deleted_count


def _delete_deployment_history_records(table) -> int:
    """Delete all DeploymentHistory records."""
    print("Scanning DeploymentHistory for all records...")
    deleted_count = 0
    last_evaluated_key = None

    while True:
        kwargs: dict = {}
        if last_evaluated_key:
            kwargs["ExclusiveStartKey"] = last_evaluated_key

        response = table.scan(**kwargs)
        items = response.get("Items", [])

        if items:
            with table.batch_writer() as batch:
                for item in items:
                    batch.delete_item(
                        Key={
                            "deployment_id": item["deployment_id"],
                            "timestamp": item["timestamp"],
                        }
                    )
                    deleted_count += 1

            if deleted_count % 25 == 0 or not response.get("LastEvaluatedKey"):
                print(f"  Deleted {deleted_count} DeploymentHistory records...")

        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

    print(f"  Total DeploymentHistory records deleted: {deleted_count}")
    return deleted_count


def main() -> None:
    """Run fleet cleanup."""
    cleanup_fleet()


if __name__ == "__main__":
    main()
