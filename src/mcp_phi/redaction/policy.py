from typing import Optional


def allowed_fields(role: Optional[str] = None) -> Optional[set[str]]:
    """Return allowed field names for a role, or None to allow all (post-structured-rules) fields.

    POC: single "default" policy — every caller gets all fields after structured redaction.
    Production: add a role -> frozenset[str] map here; thread `role` through every tool call.
    """
    return None
