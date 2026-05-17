import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)


def write_event(
    tool: str,
    args: dict,
    hit_ids: list[str],
    redaction_stats: dict,
    latency_ms: float,
) -> None:
    try:
        audit_dir = Path(settings.audit_dir)
        audit_dir.mkdir(parents=True, exist_ok=True)
        log_file = audit_dir / f"{datetime.now(timezone.utc).date()}.jsonl"
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool,
            "args": args,
            "hit_ids": hit_ids,
            "redaction_stats": redaction_stats,
            "latency_ms": round(latency_ms, 2),
        }
        with log_file.open("a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        logger.exception("Failed to write audit event")
