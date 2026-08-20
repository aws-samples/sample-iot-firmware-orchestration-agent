"""Shared helper for constructing canonical IoT Job IDs.

Centralizes job ID construction to prevent mismatches between job creation
and rollback paths. IoT Job IDs only allow alphanumeric characters, hyphens,
and underscores.
"""

import re

# Characters NOT allowed in IoT Job IDs
_INVALID_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def build_job_id(deployment_id: str, wave_number: int) -> str:
    """Construct a canonical, sanitized IoT Job ID.

    Replaces all characters that are invalid in IoT Job IDs (anything other
    than alphanumeric, hyphens, and underscores) with a hyphen.

    Args:
        deployment_id: The deployment identifier (may contain dots, slashes,
            or other special characters).
        wave_number: The wave sequence number.

    Returns:
        A sanitized job ID string in the format: fw-deploy-{sanitized_id}-wave-{wave_number}

    """
    sanitized = _INVALID_CHARS.sub("-", deployment_id)
    return f"fw-deploy-{sanitized}-wave-{wave_number}"
