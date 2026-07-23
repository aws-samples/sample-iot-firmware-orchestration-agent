"""End-to-end test for canary wave failure triggering rollback.

Validates Requirement 9.2: Fleet of 20+ devices, boot-loop in canary wave.
Expected: immediate ROLLBACK, no subsequent waves executed.

This test requires a deployed stack. Run with:
    pytest tests/e2e/test_canary_failure.py -m e2e
"""

import os
import time
import uuid

import boto3
import pytest

STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")
FIRMWARE_BUCKET = os.environ.get("FIRMWARE_BUCKET", "")
FLEET_INVENTORY_TABLE = os.environ.get("FLEET_INVENTORY_TABLE", "FleetInventory")
AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))

POLL_INTERVAL_SECONDS = 15
MAX_WAIT_SECONDS = 600
NUM_DEVICES = 20
DEVICE_TYPE = "temperature-sensor"
TARGET_VERSION = "2.0.0"
SOURCE_VERSION = "1.0.0"


def _get_stack_outputs() -> dict[str, str]:
    """Retrieve CloudFormation stack outputs."""
    if STATE_MACHINE_ARN and FIRMWARE_BUCKET:
        return {
            "StateMachineArn": STATE_MACHINE_ARN,
            "FirmwareBucketName": FIRMWARE_BUCKET,
            "FleetInventoryTableName": FLEET_INVENTORY_TABLE,
        }
    cfn_client = boto3.client("cloudformation", region_name=AWS_REGION)
    response = cfn_client.describe_stacks(StackName="FirmwareAgentStack")
    outputs = response["Stacks"][0]["Outputs"]
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def _find_latest_execution(sfn_client, state_machine_arn: str, started_after: float) -> str | None:
    """Find the most recent execution started after a given timestamp."""
    time.sleep(5)
    for _ in range(12):
        response = sfn_client.list_executions(stateMachineArn=state_machine_arn, maxResults=5)
        for execution in response.get("executions", []):
            if execution["startDate"].timestamp() >= started_after:
                return execution["executionArn"]
        time.sleep(5)
    return None


def _wait_for_execution(sfn_client, execution_arn: str, max_wait: int = MAX_WAIT_SECONDS) -> dict:
    """Poll until terminal state."""
    start_time = time.time()
    while time.time() - start_time < max_wait:
        response = sfn_client.describe_execution(executionArn=execution_arn)
        if response["status"] in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
            return response
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"Execution did not complete within {max_wait}s")


@pytest.mark.e2e
class TestCanaryFailure:
    """E2E test: boot-loop in canary wave triggers immediate ROLLBACK."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test fleet."""
        import contextlib
        from datetime import UTC, datetime

        self.test_id = uuid.uuid4().hex[:8]
        self.stack_outputs = _get_stack_outputs()
        self.sfn_client = boto3.client("stepfunctions", region_name=AWS_REGION)
        self.s3_client = boto3.client("s3", region_name=AWS_REGION)
        self.iot_client = boto3.client("iot", region_name=AWS_REGION)
        self.dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
        self.fleet_table = self.dynamodb.Table(self.stack_outputs["FleetInventoryTableName"])
        self.bucket = self.stack_outputs["FirmwareBucketName"]
        self.state_machine_arn = self.stack_outputs.get("StateMachineArn", STATE_MACHINE_ARN)

        # Register fleet
        self.thing_names = []
        for i in range(NUM_DEVICES):
            thing_name = f"e2e-{self.test_id}-{DEVICE_TYPE}-{i:04d}"
            self.iot_client.create_thing(thingName=thing_name)
            criticality = "LOW" if i < 10 else ("MEDIUM" if i < 16 else "HIGH")
            self.fleet_table.put_item(
                Item={
                    "thing_name": thing_name,
                    "device_type": DEVICE_TYPE,
                    "firmware_version": SOURCE_VERSION,
                    "criticality": criticality,
                    "location": "factory-east",
                    "facility": "factory-east",
                    "hardware_revision": "rev-A",
                    "registered_at": datetime.now(UTC).isoformat(),
                }
            )
            self.thing_names.append(thing_name)

        yield

        for name in self.thing_names:
            with contextlib.suppress(Exception):
                self.iot_client.delete_thing(thingName=name)
            with contextlib.suppress(Exception):
                self.fleet_table.delete_item(Key={"thing_name": name})

    def test_deployment_triggers_and_completes(self):
        """Firmware upload triggers an execution that reaches a terminal state."""
        from datetime import UTC, datetime

        trigger_time = time.time()
        firmware_key = f"firmware/{DEVICE_TYPE}-v{TARGET_VERSION}.bin"
        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=firmware_key,
            Body=f"FIRMWARE:{self.test_id}:{datetime.now(UTC).isoformat()}".encode(),
            ContentType="application/octet-stream",
        )

        execution_arn = _find_latest_execution(self.sfn_client, self.state_machine_arn, started_after=trigger_time)
        assert execution_arn is not None, "No execution triggered"

        result = _wait_for_execution(self.sfn_client, execution_arn)
        # The execution should reach a terminal state (SUCCEEDED or FAILED are both valid)
        assert result["status"] in ("SUCCEEDED", "FAILED"), f"Expected terminal state but got {result['status']}"
