from datetime import date
from typing import Any, Optional


def _dob_to_age_bucket(dob_str: str) -> str:
    try:
        dob = date.fromisoformat(str(dob_str)[:10])
        age = (date.today() - dob).days // 365
        low = (age // 10) * 10
        return f"{low}-{low + 9}"
    except Exception:
        return "unknown"


def _mask_phone(phone: str) -> str:
    digits = "".join(c for c in str(phone) if c.isdigit())
    return f"***-***-{digits[-4:]}" if len(digits) >= 4 else "***"


def _mask_email(email: str) -> str:
    if "@" in str(email):
        domain = str(email).split("@", 1)[1]
        return f"***@{domain}"
    return "***"


def apply(
    doc: dict[str, Any],
    allowed: Optional[set[str]] = None,
) -> tuple[dict[str, Any], list[str]]:
    """Apply field-level PHI rules. Returns (redacted_doc, dropped_fields_log)."""
    out = dict(doc)
    dropped: list[str] = []

    # SSN — always drop
    if "ssn" in out:
        del out["ssn"]
        dropped.append("ssn")

    # DOB → 10-year age bucket
    if "dob" in out:
        out["age_bucket"] = _dob_to_age_bucket(str(out["dob"]))
        del out["dob"]
        dropped.append("dob->age_bucket")

    # Names — drop
    for field in ("first_name", "last_name"):
        if field in out:
            del out[field]
            dropped.append(field)

    # Phone → last-4 mask
    if "phone" in out:
        out["phone"] = _mask_phone(str(out["phone"]))

    # Email → keep domain only
    if "email" in out:
        out["email"] = _mask_email(str(out["email"]))

    # Address → city + state only
    if "address" in out and isinstance(out["address"], dict):
        addr = out["address"]
        out["city"] = addr.get("city", "")
        out["state"] = addr.get("state", "")
        del out["address"]
        dropped.append("address->city+state")

    # Role-based field filter (no-op in POC)
    if allowed is not None:
        for key in list(out.keys()):
            if key not in allowed:
                del out[key]
                dropped.append(f"{key}(role-filtered)")

    return out, dropped
