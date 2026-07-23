"""Shared constants, environment variable lookups, and decision thresholds.

This module centralizes all configuration values used by the IoT Firmware
Orchestration Agent. Environment variables are read at import time with
sensible defaults where appropriate. Decision thresholds govern the agent's
proceed/pause/rollback logic during wave execution.
"""

import os

# ---------------------------------------------------------------------------
# Environment variable lookups (AWS resource references)
# ---------------------------------------------------------------------------

FLEET_INVENTORY_TABLE: str = os.environ.get("FLEET_INVENTORY_TABLE", "")
"""DynamoDB table name for fleet device inventory."""

DEPLOYMENT_HISTORY_TABLE: str = os.environ.get("DEPLOYMENT_HISTORY_TABLE", "")
"""DynamoDB table name for deployment history records."""

FIRMWARE_BUCKET: str = os.environ.get("FIRMWARE_BUCKET", "")
"""S3 bucket name where firmware binaries are stored."""

NOTIFICATION_TOPIC_ARN: str = os.environ.get("NOTIFICATION_TOPIC_ARN", "")
"""SNS topic ARN for operator deployment notifications."""

BEDROCK_MODEL_ID: str = os.environ.get(
    "BEDROCK_MODEL_ID",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
)
"""Amazon Bedrock foundation model ID for agent reasoning."""

WAVE_TIMEOUT_MINUTES: int = int(os.environ.get("WAVE_TIMEOUT_MINUTES", "30"))
"""Timeout in minutes for each IoT Job wave execution."""

# ---------------------------------------------------------------------------
# Decision thresholds
# ---------------------------------------------------------------------------

PROCEED_THRESHOLD: float = 98.0
"""Success rate (%) at or above which the agent decides PROCEED."""

PAUSE_THRESHOLD: float = 95.0
"""Success rate (%) at or above which the agent may decide PAUSE (if below PROCEED_THRESHOLD)."""

MAX_PAUSE_RETRIES: int = 3
"""Maximum consecutive PAUSE decisions before escalating to ROLLBACK."""

MAX_WAVE_BATCH_SIZE: int = 500
"""Maximum number of devices in a single full rollout wave."""

CANARY_PERCENTAGE: float = 0.05
"""Fraction of eligible devices for the canary wave (5%)."""

EARLY_ADOPTER_PERCENTAGE: float = 0.20
"""Fraction of remaining eligible devices for the early adopter wave (20%)."""

# ---------------------------------------------------------------------------
# Timing and operational constants
# ---------------------------------------------------------------------------

POLL_INTERVAL_SECONDS: int = 30
"""How often (seconds) the orchestrator checks wave completion status."""

PAUSE_WAIT_MINUTES: int = 10
"""Time (minutes) to wait before re-assessing after a PAUSE decision."""

SCHEDULING_RETRY_MINUTES: int = 15
"""Retry interval (minutes) when all devices are in production windows."""

MAX_SCHEDULING_DEFER_HOURS: int = 4
"""Maximum time (hours) to defer a wave for scheduling before timing out."""

GLOBAL_TIMEOUT_SECONDS: int = 14400
"""Overall Step Functions execution timeout (4 hours)."""

LAMBDA_TIMEOUT_SECONDS: int = 300
"""Per-invocation Lambda timeout (5 minutes)."""
