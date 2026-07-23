"""Property-based tests for wave planning logic using Hypothesis."""

import math
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis.strategies import integers

# Add lambda directory to path for imports
_lambda_dir = str(Path(__file__).resolve().parent.parent.parent / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

from deployment_agent.models.wave import WaveType  # noqa: E402
from deployment_agent.wave_planner import plan_waves  # noqa: E402
from shared.constants import CANARY_PERCENTAGE, MAX_WAVE_BATCH_SIZE  # noqa: E402

# Possible last_update_result values (including None for never-updated devices)
UPDATE_RESULTS = ["SUCCESS", "BOOT_LOOP", "CONNECTIVITY_LOST", "TIMEOUT", "VERSION_MISMATCH", None]


def _generate_fleet(num_devices: int) -> list[dict]:
    """Generate a fleet of devices with 50% LOW, 30% MEDIUM, 20% HIGH distribution.

    Assigns thing_names like device-{i:05d} and random last_update_result values
    based on a deterministic pattern for reproducibility.
    """
    devices = []
    for i in range(num_devices):
        # Distribution: 50% LOW, 30% MEDIUM, 20% HIGH
        pct = (i * 100) // num_devices
        if pct < 50:
            criticality = "LOW"
        elif pct < 80:
            criticality = "MEDIUM"
        else:
            criticality = "HIGH"

        # Assign last_update_result in a rotating pattern
        result = UPDATE_RESULTS[i % len(UPDATE_RESULTS)]

        devices.append(
            {
                "thing_name": f"device-{i:05d}",
                "criticality": criticality,
                "last_update_result": result,
            }
        )
    return devices


@pytest.mark.unit
@pytest.mark.property
class TestWavePlanningProperties:
    """Property-based tests for wave planning invariants.

    **Validates: Requirements 2.1, 2.3, 2.4, 2.7, 14.3, 14.4, 14.5, 14.6, 14.7**
    """

    @settings(max_examples=100)
    @given(num_devices=integers(min_value=1, max_value=10000))
    def test_wave_plan_covers_all_devices(self, num_devices):
        """Every eligible device appears in exactly one wave (coverage property).

        **Validates: Requirements 2.1, 14.4**
        """
        devices = _generate_fleet(num_devices)
        waves = plan_waves(devices)

        # Collect all thing_names from all waves
        all_assigned = []
        for wave in waves:
            all_assigned.extend(wave.thing_names)

        original_names = {d["thing_name"] for d in devices}
        assigned_names = set(all_assigned)

        # Every device appears exactly once: no duplicates, no omissions
        assert len(all_assigned) == len(assigned_names), "Duplicate device assignment detected"
        assert assigned_names == original_names, "Not all devices are covered by the wave plan"

    @settings(max_examples=100)
    @given(num_devices=integers(min_value=1, max_value=10000))
    def test_canary_wave_only_low_criticality(self, num_devices):
        """Canary wave contains only LOW criticality devices.

        **Validates: Requirements 2.1, 14.5**
        """
        devices = _generate_fleet(num_devices)
        waves = plan_waves(devices)

        # Find the canary wave
        canary_waves = [w for w in waves if w.wave_type == WaveType.CANARY]
        assert len(canary_waves) == 1, "Expected exactly one canary wave"

        canary = canary_waves[0]
        # Build a lookup of device criticality
        criticality_map = {d["thing_name"]: d["criticality"] for d in devices}

        for thing_name in canary.thing_names:
            assert criticality_map[thing_name] == "LOW", (
                f"Canary wave contains non-LOW device: {thing_name} (criticality={criticality_map[thing_name]})"
            )

    @settings(max_examples=100)
    @given(num_devices=integers(min_value=1, max_value=10000))
    def test_canary_wave_size_bounds(self, num_devices):
        """Canary wave has at least 1 and at most ceil(5% of total) devices.

        **Validates: Requirements 2.1, 2.7, 14.7**
        """
        devices = _generate_fleet(num_devices)
        waves = plan_waves(devices)

        canary_waves = [w for w in waves if w.wave_type == WaveType.CANARY]
        assert len(canary_waves) == 1

        canary = canary_waves[0]
        total = len(devices)
        max_canary = math.ceil(total * CANARY_PERCENTAGE)

        assert len(canary.thing_names) >= 1, "Canary wave must have at least 1 device"
        assert len(canary.thing_names) <= max_canary, (
            f"Canary wave size {len(canary.thing_names)} exceeds ceil(5% of {total}) = {max_canary}"
        )

    @settings(max_examples=100)
    @given(num_devices=integers(min_value=1, max_value=10000))
    def test_full_rollout_batch_size_limit(self, num_devices):
        """No full rollout wave exceeds 500 devices.

        **Validates: Requirements 2.4, 14.6**
        """
        devices = _generate_fleet(num_devices)
        waves = plan_waves(devices)

        full_rollout_waves = [w for w in waves if w.wave_type == WaveType.FULL_ROLLOUT]

        for wave in full_rollout_waves:
            assert len(wave.thing_names) <= MAX_WAVE_BATCH_SIZE, (
                f"Full rollout wave {wave.wave_number} has {len(wave.thing_names)} devices, "
                f"exceeding limit of {MAX_WAVE_BATCH_SIZE}"
            )
