"""Pure wave planning logic for deployment wave generation.

This module contains the core wave planning algorithm extracted as a pure
function with no AWS dependencies, enabling property-based testing with
Hypothesis.
"""

import math

from deployment_agent.models.wave import Wave, WaveType
from shared.constants import CANARY_PERCENTAGE, EARLY_ADOPTER_PERCENTAGE, MAX_WAVE_BATCH_SIZE


def plan_waves(eligible_devices: list[dict]) -> list[Wave]:
    """Generate deployment waves from a list of eligible devices.

    Implements a tiered deployment strategy:
    1. Canary wave: 5% of total (min 1, rounded up), LOW criticality only,
       sorted by last_update_result (SUCCESS first).
    2. Early adopter wave: 20% of remaining devices, from LOW + MEDIUM.
    3. Full rollout waves: remaining devices in batches of up to 500.

    Args:
        eligible_devices: List of device dicts with 'thing_name', 'criticality',
            and optionally 'last_update_result' keys.

    Returns:
        Ordered list of Wave objects from canary through full rollout.

    Raises:
        ValueError: If no LOW criticality devices are available for canary.

    """
    if not eligible_devices:
        return []

    # Separate devices by criticality
    low_devices = [d for d in eligible_devices if d["criticality"] == "LOW"]
    medium_devices = [d for d in eligible_devices if d["criticality"] == "MEDIUM"]
    high_devices = [d for d in eligible_devices if d["criticality"] == "HIGH"]

    if not low_devices:
        raise ValueError("No LOW criticality devices available for canary wave")

    total = len(eligible_devices)
    waves: list[Wave] = []
    wave_number = 1

    # --- Canary wave: 5% of total, min 1, ceil, LOW only ---
    canary_size = max(1, math.ceil(total * CANARY_PERCENTAGE))
    # Cannot exceed the number of available LOW devices
    canary_size = min(canary_size, len(low_devices))

    # Sort LOW devices: SUCCESS first, then others
    def _canary_sort_key(device: dict) -> int:
        return 0 if device.get("last_update_result") == "SUCCESS" else 1

    sorted_low = sorted(low_devices, key=_canary_sort_key)
    canary_devices = sorted_low[:canary_size]
    remaining_low = sorted_low[canary_size:]

    waves.append(
        Wave(
            wave_number=wave_number,
            wave_type=WaveType.CANARY,
            thing_names=[d["thing_name"] for d in canary_devices],
            target_count=len(canary_devices),
        )
    )
    wave_number += 1

    # --- Early adopter wave: 20% of remaining, LOW + MEDIUM ---
    remaining_after_canary = remaining_low + medium_devices + high_devices
    early_adopter_pool = remaining_low + medium_devices
    early_adopter_size = math.ceil(len(remaining_after_canary) * EARLY_ADOPTER_PERCENTAGE)
    early_adopter_size = min(early_adopter_size, len(early_adopter_pool))

    if early_adopter_size > 0:
        early_adopter_devices = early_adopter_pool[:early_adopter_size]

        # Split early adopter into batches of MAX_WAVE_BATCH_SIZE
        for i in range(0, len(early_adopter_devices), MAX_WAVE_BATCH_SIZE):
            batch = early_adopter_devices[i : i + MAX_WAVE_BATCH_SIZE]
            waves.append(
                Wave(
                    wave_number=wave_number,
                    wave_type=WaveType.EARLY_ADOPTER,
                    thing_names=[d["thing_name"] for d in batch],
                    target_count=len(batch),
                )
            )
            wave_number += 1

        # Remaining devices for full rollout
        used_in_early = set(d["thing_name"] for d in early_adopter_devices)
        full_rollout_devices = [d for d in remaining_after_canary if d["thing_name"] not in used_in_early]
    else:
        full_rollout_devices = remaining_after_canary

    # --- Full rollout waves: batches of up to MAX_WAVE_BATCH_SIZE ---
    for i in range(0, len(full_rollout_devices), MAX_WAVE_BATCH_SIZE):
        batch = full_rollout_devices[i : i + MAX_WAVE_BATCH_SIZE]
        waves.append(
            Wave(
                wave_number=wave_number,
                wave_type=WaveType.FULL_ROLLOUT,
                thing_names=[d["thing_name"] for d in batch],
                target_count=len(batch),
            )
        )
        wave_number += 1

    return waves
