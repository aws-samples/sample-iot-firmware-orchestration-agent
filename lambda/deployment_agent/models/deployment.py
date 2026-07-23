"""Pydantic models for deployment records and decision tracking."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from deployment_agent.models.wave import Wave


class DeploymentStatus(StrEnum):
    """Lifecycle states of a firmware deployment."""

    PLANNING = "PLANNING"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    SCHEDULING_TIMEOUT = "SCHEDULING_TIMEOUT"


class AgentDecision(StrEnum):
    """Possible decisions the deployment agent can make after health assessment."""

    PROCEED = "PROCEED"
    PAUSE = "PAUSE"
    ROLLBACK = "ROLLBACK"


class FailureClassification(BaseModel):
    """Counts of each failure type observed in a wave health assessment.

    Used by the agent decision logic to determine whether failures
    indicate a critical issue (boot_loop triggers immediate rollback)
    or a transient condition (connectivity_lost may trigger pause).
    """

    boot_loop: int = 0
    connectivity_lost: int = 0
    version_mismatch: int = 0
    timeout: int = 0


class DecisionRecord(BaseModel):
    """Record of a single agent decision for audit and traceability.

    Captures the wave context, health metrics, and reasoning that
    led to the agent's proceed/pause/rollback decision.
    """

    wave_number: int
    timestamp: datetime
    decision: AgentDecision
    reasoning: str
    success_rate: float
    failure_classifications: FailureClassification
    pause_count: int = 0


class DeploymentRecord(BaseModel):
    """Complete deployment record stored in the Deployment History DynamoDB table.

    Tracks the full lifecycle of a firmware deployment including the wave plan,
    current progress, status, and all agent decisions made during execution.
    """

    deployment_id: str
    timestamp: datetime
    target_version: str
    source_version: str
    wave_plan: list[Wave]
    current_wave: int = 0
    status: DeploymentStatus
    decisions: list[DecisionRecord] = Field(default_factory=list)
    total_devices: int = 0
    firmware_s3_url: str
