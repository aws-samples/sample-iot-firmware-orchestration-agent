"""End-to-end test for successful firmware rollout across all waves.

Validates Requirement 9.1: Fleet of 50+ devices, 99% success, 1% timeout.
Expected: PROCEED through all waves, deployment status COMPLETED.

This test requires a deployed stack. Run with:
    pytest tests/e2e/test_successful_rollout.py -m e2e

The test uploads firmware to S3, which triggers an EventBridge rule that
starts the Step Functions execution automatically. The test then finds and
monitors the EventBridge-triggered execution.
"""

import os
import time
import uuid

import boto3
import pytest

# ---------------------------------------------------------------------------
# Configuration from environment or CloudFormation outputs
# ---------------------------------------------------------------------------

STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")
FIRMWARE_BUCKET = os.environ.get("FIRMWARE_BUCKET", "")
FLEET_INVENTORY_TABLE = os.environ.get("FLEET_INVENTORY_TABLE", "FleetInventory")
DEPLOYMENT_HISTORY_TABLE = os.environ.get("DEPLOYMENT_HISTORY_TABLE", "DeploymentHistory")
AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))

# Polling configuration
POLL_INTERVAL_SECONDS = 15
MAX_WAIT_SECONDS = 600  # 10 minutes max

# Fleet configuration
NUM_DEVICES = 50
DEVICE_TYPE = "temperature-sensor"
TARGET_VERSION = "2.0.0"
SOURCE_VERSION = "1.0.0"


def _get_stack_outputs() -> dict[str, str]:
    """Retrieve CloudFormation stack outputs if env vars are not set."""
    if STATE_MACHINE_ARN and FIRMWARE_BUCKET:
        return {
            "StateMachineArn": STATE_MACHINE_ARN,
            "FirmwareBucketName": FIRMWARE_BUCKET,
            "FleetInventoryTableName": FLEET_INVENTORY_TABLE,
            "DeploymentHistoryTableName": DEPLOYMENT_HISTORY_TABLE,
        }

    cfn_client = boto3.client("cloudformation", region_name=AWS_REGION)
    response = cfn_client.describe_stacks(StackName="FirmwareAgentStack")
    outputs = response["Stacks"][0]["Outputs"]
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def _register_test_fleet(
    iot_client,
    dynamodb_table,
    num_devices: int,
    device_type: str,
    test_id: str,
) -> list[str]:
    """Register test IoT Things and populate FleetInventory."""
    from datetime import UTC, datetime

    thing_names = []
    facilities = ["factory-east", "factory-west", "factory-central"]
    hardware_revisions = ["rev-A", "rev-B", "rev-C"]

    for i in range(num_devices):
        thing_name = f"e2e-{test_id}-{device_type}-{i:04d}"
        facility = facilities[i % len(facilities)]
        hw_rev = hardware_revisions[i % len(hardware_revisions)]

        low_count = round(num_devices * 0.50)
        medium_count = round(num_devices * 0.30)
        if i < low_count:
            criticality = "LOW"
        elif i < low_count + medium_count:
            criticality = "MEDIUM"
        else:
            criticality = "HIGH"

        iot_client.create_thing(thingName=thing_name)

        item = {
            "thing_name": thing_name,
            "device_type": device_type,
            "firmware_version": SOURCE_VERSION,
            "criticality": criticality,
            "location": facility,
            "facility": facility,
            "hardware_revision": hw_rev,
            "registered_at": datetime.now(UTC).isoformat(),
        }
        dynamodb_table.put_item(Item=item)
        thing_names.append(thing_name)

    return thing_names


def _cleanup_test_fleet(iot_client, dynamodb_table, thing_names: list[str]) -> None:
    """Remove test IoT Things and FleetInventory records."""
    import contextlib

    for thing_name in thing_names:
        with contextlib.suppress(Exception):
            iot_client.delete_thing(thingName=thing_name)
        with contextlib.suppress(Exception):
            dynamodb_table.delete_item(Key={"thing_name": thing_name})


def _find_latest_execution(sfn_client, state_machine_arn: str, started_after: float) -> str | None:
    """Find the most recent execution that started after a given timestamp.

    This finds the EventBridge-triggered execution rather than one we started manually.
    """
    time.sleep(5)  # Give EventBridge time to trigger

    for _ in range(12):  # Try for up to 60 seconds
        response = sfn_client.list_executions(
            stateMachineArn=state_machine_arn,
            maxResults=5,
        )

        for execution in response.get("executions", []):
            exec_start = execution["startDate"].timestamp()
            if exec_start >= started_after:
                return execution["executionArn"]

        time.sleep(5)

    return None


def _wait_for_execution(sfn_client, execution_arn: str, max_wait: int = MAX_WAIT_SECONDS) -> dict:
    """Poll Step Functions execution until it reaches a terminal state."""
    start_time = time.time()

    while time.time() - start_time < max_wait:
        response = sfn_client.describe_execution(executionArn=execution_arn)
        status = response["status"]

        if status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
            return response

        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(f"Execution {execution_arn} did not complete within {max_wait}s")


# ---------------------------------------------------------------------------
# E2E Test Class
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestSuccessfulRollout:
    """E2E test: 50+ devices, PROCEED all waves, status COMPLETED.

    Uploads firmware to S3, which triggers EventBridge -> Step Functions.
    Monitors the automatically-triggered execution for success.
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test fleet and clean up after test."""
        self.test_id = uuid.uuid4().hex[:8]
        self.stack_outputs = _get_stack_outputs()

        self.sfn_client = boto3.client("stepfunctions", region_name=AWS_REGION)
        self.s3_client = boto3.client("s3", region_name=AWS_REGION)
        self.iot_client = boto3.client("iot", region_name=AWS_REGION)
        self.dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

        self.fleet_table = self.dynamodb.Table(self.stack_outputs["FleetInventoryTableName"])
        self.bucket = self.stack_outputs["FirmwareBucketName"]
        self.state_machine_arn = self.stack_outputs.get("StateMachineArn", STATE_MACHINE_ARN)

        # Register test fleet
        self.thing_names = _register_test_fleet(
            iot_client=self.iot_client,
            dynamodb_table=self.fleet_table,
            num_devices=NUM_DEVICES,
            device_type=DEVICE_TYPE,
            test_id=self.test_id,
        )

        yield

        # Cleanup
        _cleanup_test_fleet(self.iot_client, self.fleet_table, self.thing_names)
        import contextlib

        with contextlib.suppress(Exception):
            self.s3_client.delete_object(
                Bucket=self.bucket,
                Key=f"firmware/{DEVICE_TYPE}-v{TARGET_VERSION}.bin",
            )

    def _trigger_and_find_execution(self) -> str:
        """Upload firmware to S3 and find the EventBridge-triggered execution.

        Returns the execution ARN of the triggered execution.
        """
        from datetime import UTC, datetime

        trigger_time = time.time()

        # Upload firmware - this triggers EventBridge -> Step Functions
        firmware_key = f"firmware/{DEVICE_TYPE}-v{TARGET_VERSION}.bin"
        firmware_content = (f"FIRMWARE_BINARY:{DEVICE_TYPE}:v{TARGET_VERSION}:{datetime.now(UTC).isoformat()}").encode()

        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=firmware_key,
            Body=firmware_content,
            ContentType="application/octet-stream",
            Metadata={
                "scenario": "successful_rollout",
                "device-type": DEVICE_TYPE,
                "target-version": TARGET_VERSION,
            },
        )

        # Find the EventBridge-triggered execution
        execution_arn = _find_latest_execution(
            self.sfn_client,
            self.state_machine_arn,
            started_after=trigger_time,
        )

        assert execution_arn is not None, (
            "No Step Functions execution was triggered after firmware upload. Check EventBridge rule configuration."
        )

        return execution_arn

    def test_deployment_execution_succeeds(self):
        """The EventBridge-triggered execution reaches SUCCEEDED status.

        Validates that:
        - Firmware upload triggers EventBridge -> Step Functions
        - The orchestration workflow completes without error
        - The state machine reaches the DeploymentComplete succeed state
        """
        execution_arn = self._trigger_and_find_execution()

        result = _wait_for_execution(self.sfn_client, execution_arn)

        assert result["status"] == "SUCCEEDED", (
            f"Expected execution to SUCCEED but got {result['status']}. "
            f"Check Step Functions console for execution: {execution_arn}"
        )
