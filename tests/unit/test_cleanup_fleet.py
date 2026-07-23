"""Unit tests for the cleanup_fleet script."""

import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

# Add scripts to path for import
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import cleanup_fleet


@pytest.fixture
def aws_env(monkeypatch):
    """Set up environment variables for AWS mocking."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("FLEET_INVENTORY_TABLE", "FleetInventory")
    monkeypatch.setenv("DEPLOYMENT_HISTORY_TABLE", "DeploymentHistory")


@pytest.fixture
def mock_resources(aws_env):
    """Create mocked AWS resources with sample data."""
    with mock_aws():
        region = "us-east-1"
        iot_client = boto3.client("iot", region_name=region)
        dynamodb = boto3.resource("dynamodb", region_name=region)

        # Create FleetInventory table
        fleet_table = dynamodb.create_table(
            TableName="FleetInventory",
            KeySchema=[{"AttributeName": "thing_name", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "thing_name", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Create DeploymentHistory table
        history_table = dynamodb.create_table(
            TableName="DeploymentHistory",
            KeySchema=[
                {"AttributeName": "deployment_id", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "deployment_id", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        yield {
            "iot_client": iot_client,
            "fleet_table": fleet_table,
            "history_table": history_table,
        }


@pytest.mark.unit
class TestDeleteIoTThings:
    @mock_aws
    def test_deletes_sim_prefixed_things(self, aws_env):
        iot_client = boto3.client("iot", region_name="us-east-1")

        # Create simulated things
        for i in range(5):
            iot_client.create_thing(thingName=f"sim-sensor-factory-{i:04d}")

        deleted = cleanup_fleet._delete_iot_things(iot_client)

        assert deleted == 5
        things = iot_client.list_things()["things"]
        assert len(things) == 0

    @mock_aws
    def test_does_not_delete_non_sim_things(self, aws_env):
        iot_client = boto3.client("iot", region_name="us-east-1")

        # Create a mix of sim and non-sim things
        iot_client.create_thing(thingName="sim-sensor-001")
        iot_client.create_thing(thingName="sim-sensor-002")
        iot_client.create_thing(thingName="production-device-001")
        iot_client.create_thing(thingName="real-sensor-003")

        deleted = cleanup_fleet._delete_iot_things(iot_client)

        assert deleted == 2
        things = iot_client.list_things()["things"]
        remaining_names = {t["thingName"] for t in things}
        assert remaining_names == {"production-device-001", "real-sensor-003"}

    @mock_aws
    def test_handles_empty_thing_list(self, aws_env):
        iot_client = boto3.client("iot", region_name="us-east-1")

        deleted = cleanup_fleet._delete_iot_things(iot_client)

        assert deleted == 0


@pytest.mark.unit
class TestDeleteFleetInventoryRecords:
    @mock_aws
    def test_deletes_sim_prefixed_records(self, aws_env):
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="FleetInventory",
            KeySchema=[{"AttributeName": "thing_name", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "thing_name", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Add records
        for i in range(5):
            table.put_item(Item={"thing_name": f"sim-sensor-{i:04d}", "device_type": "sensor"})

        deleted = cleanup_fleet._delete_fleet_inventory_records(table)

        assert deleted == 5
        response = table.scan()
        assert response["Count"] == 0

    @mock_aws
    def test_preserves_non_sim_records(self, aws_env):
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="FleetInventory",
            KeySchema=[{"AttributeName": "thing_name", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "thing_name", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        table.put_item(Item={"thing_name": "sim-sensor-0001", "device_type": "sensor"})
        table.put_item(Item={"thing_name": "production-device-001", "device_type": "sensor"})

        deleted = cleanup_fleet._delete_fleet_inventory_records(table)

        assert deleted == 1
        response = table.scan()
        assert response["Count"] == 1
        assert response["Items"][0]["thing_name"] == "production-device-001"

    @mock_aws
    def test_handles_empty_table(self, aws_env):
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="FleetInventory",
            KeySchema=[{"AttributeName": "thing_name", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "thing_name", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        deleted = cleanup_fleet._delete_fleet_inventory_records(table)

        assert deleted == 0


@pytest.mark.unit
class TestDeleteDeploymentHistoryRecords:
    @mock_aws
    def test_deletes_all_records(self, aws_env):
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="DeploymentHistory",
            KeySchema=[
                {"AttributeName": "deployment_id", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "deployment_id", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Add deployment records
        for i in range(3):
            table.put_item(
                Item={
                    "deployment_id": f"deploy-{i}",
                    "timestamp": f"2024-01-0{i + 1}T00:00:00Z",
                    "status": "COMPLETED",
                }
            )

        deleted = cleanup_fleet._delete_deployment_history_records(table)

        assert deleted == 3
        response = table.scan()
        assert response["Count"] == 0

    @mock_aws
    def test_handles_empty_table(self, aws_env):
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="DeploymentHistory",
            KeySchema=[
                {"AttributeName": "deployment_id", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "deployment_id", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        deleted = cleanup_fleet._delete_deployment_history_records(table)

        assert deleted == 0


@pytest.mark.unit
class TestCleanupFleet:
    @mock_aws
    def test_full_cleanup_workflow(self, aws_env):
        region = "us-east-1"
        iot_client = boto3.client("iot", region_name=region)
        dynamodb = boto3.resource("dynamodb", region_name=region)

        # Create tables
        fleet_table = dynamodb.create_table(
            TableName="FleetInventory",
            KeySchema=[{"AttributeName": "thing_name", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "thing_name", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        history_table = dynamodb.create_table(
            TableName="DeploymentHistory",
            KeySchema=[
                {"AttributeName": "deployment_id", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "deployment_id", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Set up test data
        for i in range(10):
            thing_name = f"sim-sensor-factory-{i:04d}"
            iot_client.create_thing(thingName=thing_name)
            fleet_table.put_item(Item={"thing_name": thing_name, "device_type": "sensor"})

        history_table.put_item(
            Item={"deployment_id": "deploy-1", "timestamp": "2024-01-01T00:00:00Z", "status": "COMPLETED"}
        )

        # Run cleanup
        cleanup_fleet.cleanup_fleet()

        # Verify all sim things deleted
        things = iot_client.list_things()["things"]
        assert len(things) == 0

        # Verify fleet inventory cleared
        response = fleet_table.scan()
        assert response["Count"] == 0

        # Verify deployment history cleared
        response = history_table.scan()
        assert response["Count"] == 0
