"""Lambda entry point with action routing for the deployment agent.

Routes incoming requests to the appropriate handler based on the action field.
Implements structured JSON logging with deployment_id/wave_number context and
emits CloudWatch custom metrics via aws-embedded-metrics.
"""

import asyncio
import json
import logging
import time
from typing import Any

from aws_embedded_metrics import metric_scope
from aws_embedded_metrics.logger.metrics_logger import MetricsLogger
from aws_embedded_metrics.unit import Unit

from deployment_agent.agent import create_agent
from deployment_agent.tools.create_deployment_wave import create_deployment_wave
from deployment_agent.tools.get_wave_health import get_wave_health

# ---------------------------------------------------------------------------
# Structured JSON logging configuration
# ---------------------------------------------------------------------------


class StructuredJsonFormatter(logging.Formatter):
    """Format log records as structured JSON with deployment context fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a JSON string with required context fields.

        Args:
            record: The log record to format.

        Returns:
            A JSON-encoded string with structured deployment context.

        """
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "deployment_id": getattr(record, "deployment_id", None),
            "wave_number": getattr(record, "wave_number", None),
            "action": getattr(record, "action", None),
            "reasoning": getattr(record, "reasoning", None),
            "outcome": getattr(record, "outcome", None),
            "message": record.getMessage(),
        }
        # Remove None values for cleaner output
        log_entry = {k: v for k, v in log_entry.items() if v is not None}
        return json.dumps(log_entry, default=str)


# Configure the root logger with our structured formatter
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Avoid duplicate handlers on Lambda warm starts
if not logger.handlers:
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(StructuredJsonFormatter())
    logger.addHandler(stream_handler)


def _log_with_context(
    level: int,
    message: str,
    *,
    deployment_id: str | None = None,
    wave_number: int | None = None,
    action: str | None = None,
    reasoning: str | None = None,
    outcome: str | None = None,
) -> None:
    """Emit a structured log entry with deployment context fields.

    Args:
        level: Logging level (e.g., logging.INFO).
        message: The log message.
        deployment_id: Deployment identifier for correlation.
        wave_number: Wave number for correlation.
        action: The action being processed.
        reasoning: Agent reasoning text.
        outcome: Result or decision outcome.

    """
    extra = {
        "deployment_id": deployment_id,
        "wave_number": wave_number,
        "action": action,
        "reasoning": reasoning,
        "outcome": outcome,
    }
    logger.log(level, message, extra=extra)


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


def handle_plan(event: dict) -> dict:
    """Handle the PLAN action by invoking the Strands agent.

    Constructs a natural language prompt from the event parameters, invokes the
    agent, and parses the structured JSON response.

    Args:
        event: The Lambda event containing firmware_s3_url, target_version,
            and device_type.

    Returns:
        A dictionary with deployment_id, wave_plan, total_devices, and status.

    """
    import uuid

    firmware_s3_url = event.get("firmware_s3_url", "")
    target_version = event.get("target_version", "")
    device_type = event.get("device_type", "")

    # Generate a unique deployment ID to prevent IoT Job name collisions
    unique_suffix = uuid.uuid4().hex[:8]

    _log_with_context(
        logging.INFO,
        "Planning deployment",
        action="PLAN",
        deployment_id=event.get("deployment_id"),
    )

    prompt = (
        f"Plan a firmware deployment with action=PLAN. "
        f"Target device type: {device_type}. "
        f"Target firmware version: {target_version}. "
        f"Firmware S3 URL: {firmware_s3_url}. "
        f"Use deployment_id: deploy-{device_type}-{unique_suffix}. "
        f"Query the fleet inventory, check device risk profiles, and create a wave plan."
    )

    agent = create_agent()
    result = agent(prompt)

    # Parse the agent's response text as JSON
    response_text = str(result)
    try:
        # Try to extract JSON from the response
        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            parsed = json.loads(response_text[json_start:json_end])
        else:
            parsed = json.loads(response_text)
    except (json.JSONDecodeError, ValueError):
        parsed = {"raw_response": response_text}

    details = parsed.get("details", parsed)
    deployment_id = f"deploy-{device_type}-{unique_suffix}"
    wave_plan = details.get("waves", details.get("wave_plan", []))
    total_devices = details.get("total_eligible_devices", details.get("total_devices", 0))

    _log_with_context(
        logging.INFO,
        "Deployment planned",
        action="PLAN",
        deployment_id=deployment_id,
        reasoning=parsed.get("reasoning", ""),
        outcome="planned",
    )

    return {
        "deployment_id": deployment_id,
        "wave_plan": wave_plan,
        "total_devices": total_devices,
        "status": "PLANNING",
    }


def handle_assess(event: dict) -> dict:
    """Handle the ASSESS action by invoking the Strands agent.

    Constructs a natural language prompt from the event parameters, invokes the
    agent to assess wave health, and parses the structured JSON response.

    Args:
        event: The Lambda event containing deployment_id, wave_number, and job_id.

    Returns:
        A dictionary with decision, reasoning, success_rate, failure_types,
        and pause_count.

    """
    deployment_id = event.get("deployment_id", "")
    wave_number = event.get("wave_number", 0)
    job_id = event.get("job_id", "")

    _log_with_context(
        logging.INFO,
        "Assessing wave health",
        action="ASSESS",
        deployment_id=deployment_id,
        wave_number=wave_number,
    )

    prompt = (
        f"Assess wave health with action=ASSESS. "
        f"Deployment ID: {deployment_id}. "
        f"Wave number: {wave_number}. "
        f"IoT Job ID: {job_id}. "
        f"Get the wave health data and make a PROCEED, PAUSE, or ROLLBACK decision."
    )

    agent = create_agent()
    result = agent(prompt)

    # Parse the agent's response text as JSON
    response_text = str(result)
    try:
        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            parsed = json.loads(response_text[json_start:json_end])
        else:
            parsed = json.loads(response_text)
    except (json.JSONDecodeError, ValueError):
        parsed = {"raw_response": response_text}

    details = parsed.get("details", parsed)
    decision = details.get("decision", "PAUSE")
    reasoning = parsed.get("reasoning", details.get("reasoning", ""))
    success_rate = details.get("success_rate", 0.0)
    failure_types = details.get("failure_types", {})
    pause_count = details.get("pause_count", 0)

    _log_with_context(
        logging.INFO,
        f"Assessment complete: {decision}",
        action="ASSESS",
        deployment_id=deployment_id,
        wave_number=wave_number,
        reasoning=reasoning,
        outcome=decision,
    )

    return {
        "decision": decision,
        "reasoning": reasoning,
        "success_rate": success_rate,
        "failure_types": failure_types,
        "pause_count": pause_count,
    }


def handle_rollback(event: dict) -> dict:
    """Handle the ROLLBACK action by invoking the Strands agent.

    Constructs a natural language prompt from the event parameters, invokes the
    agent to execute the rollback, and parses the structured JSON response.

    Args:
        event: The Lambda event containing deployment_id, job_id,
            failed_thing_names, and previous_firmware_s3_url.

    Returns:
        A dictionary with rollback_job_id, rollback_job_arn, target_count,
        and cancelled_job_id.

    """
    deployment_id = event.get("deployment_id", "")
    job_id = event.get("job_id", "")
    failed_thing_names = event.get("failed_thing_names", [])
    previous_firmware_s3_url = event.get("previous_firmware_s3_url", "")

    _log_with_context(
        logging.INFO,
        "Executing rollback",
        action="ROLLBACK",
        deployment_id=deployment_id,
    )

    prompt = (
        f"Execute rollback with action=ROLLBACK. "
        f"Deployment ID: {deployment_id}. "
        f"Current Job ID: {job_id}. "
        f"Failed device names: {json.dumps(failed_thing_names)}. "
        f"Previous firmware S3 URL: {previous_firmware_s3_url}. "
        f"Cancel the current job and create a rollback job for failed devices."
    )

    agent = create_agent()
    result = agent(prompt)

    # Parse the agent's response text as JSON
    response_text = str(result)
    try:
        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            parsed = json.loads(response_text[json_start:json_end])
        else:
            parsed = json.loads(response_text)
    except (json.JSONDecodeError, ValueError):
        parsed = {"raw_response": response_text}

    details = parsed.get("details", parsed)

    rollback_result = {
        "rollback_job_id": details.get("rollback_job_id", ""),
        "rollback_job_arn": details.get("rollback_job_arn", ""),
        "target_count": details.get("target_count", len(failed_thing_names)),
        "cancelled_job_id": details.get("cancelled_job_id", details.get("cancelled_job", job_id)),
    }

    _log_with_context(
        logging.INFO,
        "Rollback executed",
        action="ROLLBACK",
        deployment_id=deployment_id,
        reasoning=parsed.get("reasoning", ""),
        outcome="rolled_back",
    )

    return rollback_result


def handle_create_wave(event: dict) -> dict:
    """Handle the CREATE_WAVE action by directly calling the create_deployment_wave tool.

    No agent reasoning is needed for this simple operational action.

    Args:
        event: The Lambda event containing deployment_id, wave_number,
            thing_names, firmware_s3_url, and optional timeout_minutes.

    Returns:
        A dictionary with job_id, job_arn, and target_count.

    """
    deployment_id = event.get("deployment_id", "")
    wave_number = event.get("wave_number", 1)
    thing_names = event.get("thing_names", [])
    firmware_s3_url = event.get("firmware_s3_url", "")
    timeout_minutes = event.get("timeout_minutes", 30)

    _log_with_context(
        logging.INFO,
        f"Creating deployment wave {wave_number}",
        action="CREATE_WAVE",
        deployment_id=deployment_id,
        wave_number=wave_number,
    )

    start_time = time.time()

    result = create_deployment_wave(
        deployment_id=deployment_id,
        wave_number=wave_number,
        thing_names=thing_names,
        firmware_s3_url=firmware_s3_url,
        timeout_minutes=timeout_minutes,
    )

    elapsed_ms = (time.time() - start_time) * 1000

    _log_with_context(
        logging.INFO,
        f"Wave {wave_number} created with job {result['job_id']}",
        action="CREATE_WAVE",
        deployment_id=deployment_id,
        wave_number=wave_number,
        outcome="wave_created",
    )

    # Store elapsed time for metrics emission
    result["_elapsed_ms"] = elapsed_ms

    return result


def handle_check_wave_status(event: dict) -> dict:
    """Handle the CHECK_WAVE_STATUS action by calling get_wave_health.

    Determines if the wave is complete based on the in_progress_count.

    Args:
        event: The Lambda event containing job_id.

    Returns:
        A dictionary with is_complete (boolean) and summary (health metrics).

    """
    job_id = event.get("job_id", "")

    _log_with_context(
        logging.INFO,
        f"Checking wave status for job {job_id}",
        action="CHECK_WAVE_STATUS",
    )

    health = get_wave_health(job_id=job_id)

    is_complete = health.get("in_progress_count", 0) == 0

    _log_with_context(
        logging.INFO,
        f"Wave status: {'complete' if is_complete else 'in_progress'}",
        action="CHECK_WAVE_STATUS",
        outcome="complete" if is_complete else "in_progress",
    )

    return {
        "is_complete": is_complete,
        "summary": health,
    }


# ---------------------------------------------------------------------------
# Metrics emission
# ---------------------------------------------------------------------------


@metric_scope
async def _emit_assess_metrics(
    deployment_id: str,
    wave_number: int,
    success_rate: float,
    metrics: MetricsLogger,
) -> None:
    """Emit deployment success and failure rate metrics after ASSESS action.

    Args:
        deployment_id: Deployment identifier for metric dimensions.
        wave_number: Wave number for metric dimensions.
        success_rate: The calculated success rate percentage.
        metrics: The embedded metrics logger instance.

    """
    metrics.set_namespace("FirmwareDeployment")
    metrics.set_dimensions({"deployment_id": deployment_id, "wave_number": str(wave_number)})
    metrics.put_metric("DeploymentSuccessRate", success_rate, Unit.PERCENT)
    metrics.put_metric("DeploymentFailureRate", 100.0 - success_rate, Unit.PERCENT)


@metric_scope
async def _emit_rollback_metrics(
    deployment_id: str,
    wave_number: int,
    metrics: MetricsLogger,
) -> None:
    """Emit rollback count metric after ROLLBACK action.

    Args:
        deployment_id: Deployment identifier for metric dimensions.
        wave_number: Wave number for metric dimensions.
        metrics: The embedded metrics logger instance.

    """
    metrics.set_namespace("FirmwareDeployment")
    metrics.set_dimensions({"deployment_id": deployment_id, "wave_number": str(wave_number)})
    metrics.put_metric("RollbackCount", 1, Unit.COUNT)


@metric_scope
async def _emit_wave_completion_metrics(
    deployment_id: str,
    wave_number: int,
    elapsed_ms: float,
    metrics: MetricsLogger,
) -> None:
    """Emit wave completion time metric after CREATE_WAVE completes.

    Args:
        deployment_id: Deployment identifier for metric dimensions.
        wave_number: Wave number for metric dimensions.
        elapsed_ms: The time in milliseconds for wave creation.
        metrics: The embedded metrics logger instance.

    """
    metrics.set_namespace("FirmwareDeployment")
    metrics.set_dimensions({"deployment_id": deployment_id, "wave_number": str(wave_number)})
    metrics.put_metric("WaveCompletionTime", elapsed_ms, Unit.MILLISECONDS)


# ---------------------------------------------------------------------------
# Main Lambda handler
# ---------------------------------------------------------------------------


def handler(event: dict, context: Any = None) -> dict:
    """Route incoming requests to the appropriate agent action.

    Extracts the action field from the event and dispatches to the corresponding
    handler function. Emits structured logs and CloudWatch metrics for
    observability.

    Args:
        event: The Lambda invocation event containing an 'action' field and
            action-specific parameters.
        context: The Lambda context object (unused but required by Lambda runtime).

    Returns:
        A dictionary containing the action-specific response.

    Raises:
        ValueError: If the action field is missing or unknown.

    """
    action = event.get("action")
    deployment_id = event.get("deployment_id", "")
    wave_number = event.get("wave_number", 0)

    _log_with_context(
        logging.INFO,
        f"Handler invoked with action={action}",
        action=action,
        deployment_id=deployment_id,
        wave_number=wave_number,
    )

    match action:
        case "PLAN":
            result = handle_plan(event)
            return result

        case "ASSESS":
            result = handle_assess(event)
            # Emit success/failure rate metrics (best effort)
            try:
                asyncio.run(
                    _emit_assess_metrics(
                        deployment_id=deployment_id,
                        wave_number=wave_number,
                        success_rate=result.get("success_rate", 0.0),
                    )
                )
            except Exception:
                logger.warning("Failed to emit assess metrics", exc_info=True)
            return result

        case "ROLLBACK":
            result = handle_rollback(event)
            # Emit rollback count metric (best effort)
            try:
                asyncio.run(
                    _emit_rollback_metrics(
                        deployment_id=deployment_id,
                        wave_number=wave_number,
                    )
                )
            except Exception:
                logger.warning("Failed to emit rollback metrics", exc_info=True)
            return result

        case "CREATE_WAVE":
            result = handle_create_wave(event)
            # Emit wave completion time metric (best effort)
            elapsed_ms = result.pop("_elapsed_ms", 0.0)
            try:
                asyncio.run(
                    _emit_wave_completion_metrics(
                        deployment_id=deployment_id,
                        wave_number=wave_number,
                        elapsed_ms=elapsed_ms,
                    )
                )
            except Exception:
                logger.warning("Failed to emit wave metrics", exc_info=True)
            return result

        case "CHECK_WAVE_STATUS":
            return handle_check_wave_status(event)

        case _:
            raise ValueError(f"Unknown action: {action}")
