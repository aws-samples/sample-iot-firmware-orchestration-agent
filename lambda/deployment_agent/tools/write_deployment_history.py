"""Tool to write agent decision records to the DeploymentHistory table."""

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3

logger = logging.getLogger(__name__)


def write_deployment_history(
    deployment_id: str,
    wave_number: int,
    action: str,
    decision: str,
    reasoning: str,
    success_rate: float,
    failure_types: dict,
    thing_names: list[str],
) -> dict:
    """Record an agent decision to the DeploymentHistory table.

    Creates an audit trail of every PROCEED/PAUSE/ROLLBACK decision for
    post-incident review and compliance.

    Args:
        deployment_id: The deployment identifier (partition key).
        wave_number: The wave number this decision applies to.
        action: The action being performed (ASSESS, ROLLBACK).
        decision: The agent's decision (PROCEED, PAUSE, ROLLBACK).
        reasoning: The agent's reasoning text for the decision.
        success_rate: The success rate percentage at decision time.
        failure_types: Mapping of failure type to count.
        thing_names: List of device thing names in the wave.

    Returns:
        A dictionary with status indicating success or failure.

    """
    table_name = os.environ.get("DEPLOYMENT_HISTORY_TABLE", "DeploymentHistory")
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    timestamp = datetime.now(tz=timezone.utc).isoformat()

    # Convert float to Decimal for DynamoDB
    success_rate_decimal = Decimal(str(round(success_rate, 2)))

    # Convert failure_types values to Decimal
    failure_types_decimal = {k: Decimal(str(v)) for k, v in failure_types.items()}

    try:
        table.put_item(
            Item={
                "deployment_id": deployment_id,
                "timestamp": timestamp,
                "action": action,
                "wave_number": wave_number,
                "decision": decision,
                "reasoning": reasoning,
                "success_rate": success_rate_decimal,
                "failure_types": failure_types_decimal,
                "thing_names": thing_names,
                "device_count": len(thing_names),
            }
        )

        logger.info(
            "Recorded deployment history: deployment=%s, wave=%d, decision=%s",
            deployment_id,
            wave_number,
            decision,
        )

        return {"status": "recorded"}

    except Exception:
        logger.warning(
            "Failed to write deployment history for %s wave %d",
            deployment_id,
            wave_number,
            exc_info=True,
        )
        return {"status": "failed"}
