"""Pydantic models for device records and related enums."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Criticality(StrEnum):
    """Device criticality level for wave planning prioritization."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class UpdateResult(StrEnum):
    """Possible outcomes of a firmware update execution."""

    SUCCESS = "SUCCESS"
    BOOT_LOOP = "BOOT_LOOP"
    CONNECTIVITY_LOST = "CONNECTIVITY_LOST"
    TIMEOUT = "TIMEOUT"
    VERSION_MISMATCH = "VERSION_MISMATCH"


class ProductionSchedule(BaseModel):
    """Time window when a device is actively in production use.

    Devices within their production window are excluded from deployment waves
    to avoid disrupting active operations. An overnight window is indicated
    when start_hour is greater than end_hour (spans midnight).
    """

    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=0, le=23)
    timezone: str


class Device(BaseModel):
    """IoT device record stored in the Fleet Inventory DynamoDB table.

    Represents a single IoT device with its metadata, firmware state,
    criticality classification, and scheduling constraints used by
    the deployment agent for wave planning decisions.
    """

    thing_name: str = Field(max_length=128)
    device_type: str = Field(max_length=64)
    firmware_version: str
    target_version: str | None = None
    criticality: Criticality
    location: str = Field(max_length=128)
    facility: str = Field(max_length=64)
    production_schedule: ProductionSchedule | None = None
    last_update_result: UpdateResult | None = None
    hardware_revision: str = Field(max_length=32)
    registered_at: datetime
