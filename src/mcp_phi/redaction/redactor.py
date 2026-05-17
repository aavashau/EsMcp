from typing import Any, Optional

from .free_text import anonymize as anonymize_text
from .policy import allowed_fields
from .structured_rules import apply as apply_structured


class Redactor:
    @staticmethod
    def redact(doc: dict[str, Any], role: Optional[str] = None) -> tuple[dict[str, Any], dict]:
        """Redact PHI from a document. Returns (redacted_doc, stats)."""
        allowed = allowed_fields(role)

        out, dropped = apply_structured(doc, allowed)

        entities_redacted: dict[str, int] = {}
        if "note" in out and out["note"]:
            out["note_redacted"], entities_redacted = anonymize_text(str(out["note"]))
            del out["note"]

        return out, {
            "fields_dropped": dropped,
            "entities_redacted": entities_redacted,
        }
