import json
import time
from datetime import date, datetime
from typing import Optional

from mcp_phi.audit import write_event
from mcp_phi.config import settings
from mcp_phi.es_client import get_client
from mcp_phi.redaction import Redactor

ELIG = "elig"
CLAIMS = "claims"

_AGE_BUCKETS = [
    ("0-9",   0,   9),
    ("10-19", 10, 19),
    ("20-29", 20, 29),
    ("30-39", 30, 39),
    ("40-49", 40, 49),
    ("50-59", 50, 59),
    ("60-69", 60, 69),
    ("70-79", 70, 79),
    ("80+",   80, 999),
]


# ------------------------------------------------------------------ #
#  Module-level helpers                                                #
# ------------------------------------------------------------------ #

def _dob_cutoff(years: int) -> str:
    """ISO date for someone who turns `years` old today (handles Feb-29 edge case)."""
    today = datetime.today()
    try:
        return today.replace(year=today.year - years).strftime("%Y-%m-%d")
    except ValueError:
        return datetime(today.year - years, 3, 1).strftime("%Y-%m-%d")


def _age_filter(min_age: Optional[int], max_age: Optional[int]) -> Optional[dict]:
    """Return an ES range clause on `dob` for inclusive age bounds, or None if unset."""
    if min_age is None and max_age is None:
        return None
    r: dict = {}
    if max_age is not None:
        r["gte"] = _dob_cutoff(max_age + 1)
    if min_age is not None:
        r["lte"] = _dob_cutoff(min_age)
    return {"range": {"dob": r}}


def _redact_hits(hits: list[dict], drop_note: bool = False) -> tuple[list[dict], dict]:
    """Redact a list of ES source dicts. Returns (results, redaction_stats)."""
    results, dropped, entities = [], [], {}
    for hit in hits:
        if drop_note:
            hit.pop("note", None)
        redacted, stats = Redactor.redact(hit)
        results.append(redacted)
        dropped.extend(stats["fields_dropped"])
        entities.update(stats["entities_redacted"])
    return results, {"fields_dropped": dropped, "entities_redacted": entities}


def _age_ranges() -> list[dict]:
    today = date.today()

    def _shift(years: int) -> str:
        try:
            return today.replace(year=today.year - years).isoformat()
        except ValueError:
            return date(today.year - years, 3, 1).isoformat()

    ranges = []
    for label, lo, hi in _AGE_BUCKETS:
        entry: dict = {"key": label}
        if hi < 999:
            entry["from"] = _shift(hi + 1)
            entry["to"] = _shift(lo)
        else:
            entry["to"] = _shift(lo)
        ranges.append(entry)
    return ranges


def _strip_top_hits(agg_def: dict) -> dict:
    """Recursively remove top_hits sub-aggregations to prevent raw-document PHI leakage."""
    cleaned: dict = {}
    for k, v in agg_def.items():
        if isinstance(v, dict):
            if "top_hits" in v:
                v = {sk: sv for sk, sv in v.items() if sk != "top_hits"}
            cleaned[k] = _strip_top_hits(v)
        else:
            cleaned[k] = v
    return cleaned


# ------------------------------------------------------------------ #
#  Tool registration                                                   #
# ------------------------------------------------------------------ #

def register_tools(mcp) -> None:

    # ── Document-level lookups ──────────────────────────────────────

    @mcp.tool()
    def search_patients(query: str, limit: int = 10) -> list[dict]:
        """Search for patients (elig records) by name or MRN. Returns de-identified summaries."""
        start = time.time()
        es = get_client()
        resp = es.search(
            index=settings.es_index,
            query={
                "bool": {
                    "must": [
                        {"term": {"_docType": ELIG}},
                        {"multi_match": {"query": query, "fields": ["first_name", "last_name", "patient_id"]}},
                    ]
                }
            },
            size=limit,
        )
        hits = [h["_source"] for h in resp["hits"]["hits"]]
        results, redaction_stats = _redact_hits(hits)
        write_event(
            tool="search_patients",
            args={"query": query, "limit": limit},
            hit_ids=[h.get("patient_id", "") for h in hits],
            redaction_stats=redaction_stats,
            latency_ms=(time.time() - start) * 1000,
        )
        return results

    @mcp.tool()
    def get_patient(patient_id: str) -> dict:
        """Get a single elig record by MRN (e.g. MRN-000042). PHI fields are de-identified before return."""
        start = time.time()
        es = get_client()
        try:
            hit = es.get(index=settings.es_index, id=patient_id)["_source"]
        except Exception:
            return {"error": f"Patient {patient_id} not found"}
        redacted, stats = Redactor.redact(hit)
        write_event(
            tool="get_patient",
            args={"patient_id": patient_id},
            hit_ids=[patient_id],
            redaction_stats=stats,
            latency_ms=(time.time() - start) * 1000,
        )
        return redacted

    @mcp.tool()
    def search_encounters(
        patient_id: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        """Search claims records by patient MRN and/or free-text query against clinical notes.
        Notes are excluded from results; use get_encounter_note to retrieve a specific note."""
        start = time.time()
        es = get_client()
        must: list[dict] = [{"term": {"_docType": CLAIMS}}]
        if patient_id:
            must.append({"term": {"patient_id": patient_id}})
        if q:
            must.append({"match": {"note": q}})
        resp = es.search(index=settings.es_index, query={"bool": {"must": must}}, size=limit)
        hits = [h["_source"] for h in resp["hits"]["hits"]]
        results, redaction_stats = _redact_hits(hits, drop_note=True)
        write_event(
            tool="search_encounters",
            args={"patient_id": patient_id, "q": q, "limit": limit},
            hit_ids=[h.get("encounter_id", "") for h in hits],
            redaction_stats=redaction_stats,
            latency_ms=(time.time() - start) * 1000,
        )
        return results

    @mcp.tool()
    def get_encounter_note(encounter_id: str) -> dict:
        """Retrieve the clinical note for a claims record. Note text is Presidio-anonymized before return."""
        start = time.time()
        es = get_client()
        try:
            hit = es.get(index=settings.es_index, id=encounter_id)["_source"]
        except Exception:
            return {"error": f"Encounter {encounter_id} not found"}
        redacted, stats = Redactor.redact(hit)
        write_event(
            tool="get_encounter_note",
            args={"encounter_id": encounter_id},
            hit_ids=[encounter_id],
            redaction_stats=stats,
            latency_ms=(time.time() - start) * 1000,
        )
        return {"encounter_id": encounter_id, "note_redacted": redacted.get("note_redacted", "")}

    @mcp.tool()
    def search_encounters_by_diagnosis(
        diagnosis_code: Optional[str] = None,
        diagnosis_description: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        """Find encounters that include a specific diagnosis.
        diagnosis_code: exact ICD code (e.g. 'E11.9').
        diagnosis_description: keyword(s) in the diagnosis description text.
        At least one must be provided."""
        if not diagnosis_code and not diagnosis_description:
            return [{"error": "Provide diagnosis_code and/or diagnosis_description"}]
        start = time.time()
        es = get_client()
        nested_must: list[dict] = []
        if diagnosis_code:
            nested_must.append({"term": {"diagnoses.code": diagnosis_code}})
        if diagnosis_description:
            nested_must.append({"match": {"diagnoses.description": diagnosis_description}})
        resp = es.search(
            index=settings.es_index,
            query={
                "bool": {
                    "must": [
                        {"term": {"_docType": CLAIMS}},
                        {"nested": {"path": "diagnoses", "query": {"bool": {"must": nested_must}}}},
                    ]
                }
            },
            size=limit,
        )
        hits = [h["_source"] for h in resp["hits"]["hits"]]
        results, redaction_stats = _redact_hits(hits, drop_note=True)
        write_event(
            tool="search_encounters_by_diagnosis",
            args={"diagnosis_code": diagnosis_code, "diagnosis_description": diagnosis_description, "limit": limit},
            hit_ids=[h.get("encounter_id", "") for h in hits],
            redaction_stats=redaction_stats,
            latency_ms=(time.time() - start) * 1000,
        )
        return results

    @mcp.tool()
    def search_encounters_by_date_range(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        patient_id: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        """Find encounters within a date range. Dates in YYYY-MM-DD format.
        Optionally filter by patient MRN."""
        start = time.time()
        es = get_client()
        must: list[dict] = [{"term": {"_docType": CLAIMS}}]
        date_range: dict = {}
        if start_date:
            date_range["gte"] = start_date
        if end_date:
            date_range["lte"] = end_date
        if date_range:
            must.append({"range": {"encounter_date": date_range}})
        if patient_id:
            must.append({"term": {"patient_id": patient_id}})
        resp = es.search(index=settings.es_index, query={"bool": {"must": must}}, size=limit)
        hits = [h["_source"] for h in resp["hits"]["hits"]]
        results, redaction_stats = _redact_hits(hits, drop_note=True)
        write_event(
            tool="search_encounters_by_date_range",
            args={"start_date": start_date, "end_date": end_date, "patient_id": patient_id, "limit": limit},
            hit_ids=[h.get("encounter_id", "") for h in hits],
            redaction_stats=redaction_stats,
            latency_ms=(time.time() - start) * 1000,
        )
        return results

    @mcp.tool()
    def search_patients_by_demographics(
        state: Optional[str] = None,
        city: Optional[str] = None,
        min_age: Optional[int] = None,
        max_age: Optional[int] = None,
        insurance_id: Optional[str] = None,
        limit: int = 500,
    ) -> list[dict]:
        """Find patients by demographic criteria. All results are de-identified.
        state: 2-letter state code (e.g. 'CA'). city: city name.
        min_age / max_age: inclusive age bounds in years. insurance_id: exact match."""
        start = time.time()
        es = get_client()
        must: list[dict] = [{"term": {"_docType": ELIG}}]
        if state:
            must.append({"term": {"address.state": state}})
        if city:
            must.append({"term": {"address.city": city}})
        if insurance_id:
            must.append({"term": {"insurance_id": insurance_id}})
        age_clause = _age_filter(min_age, max_age)
        if age_clause:
            must.append(age_clause)
        resp = es.search(index=settings.es_index, query={"bool": {"must": must}}, size=limit)
        hits = [h["_source"] for h in resp["hits"]["hits"]]
        results, redaction_stats = _redact_hits(hits)
        write_event(
            tool="search_patients_by_demographics",
            args={"state": state, "city": city, "min_age": min_age, "max_age": max_age,
                  "insurance_id": insurance_id, "limit": limit},
            hit_ids=[h.get("patient_id", "") for h in hits],
            redaction_stats=redaction_stats,
            latency_ms=(time.time() - start) * 1000,
        )
        return results

    # ── Count ───────────────────────────────────────────────────────

    @mcp.tool()
    def count_records(
        doc_type: Optional[str] = None,
        state: Optional[str] = None,
        city: Optional[str] = None,
        min_age: Optional[int] = None,
        max_age: Optional[int] = None,
        diagnosis_code: Optional[str] = None,
        provider_name: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """Count records matching any combination of filters. Use this for ALL 'how many' questions.
        doc_type: 'elig' (patients) | 'claims' (encounters) | omit for all.
        state: 2-letter code. city: city name.
        min_age / max_age: inclusive age bounds in years (applies to elig records).
        diagnosis_code: ICD code prefix match (applies to claims records).
        provider_name: partial match on provider name (applies to claims records).
        start_date / end_date: encounter date range YYYY-MM-DD (applies to claims records).
        NEVER use search tools to answer count questions — search tools have a limit and will under-count."""
        start = time.time()
        es = get_client()
        must: list[dict] = []
        if doc_type:
            must.append({"term": {"_docType": doc_type}})
        if state:
            must.append({"term": {"address.state": state}})
        if city:
            must.append({"term": {"address.city": city}})
        age_clause = _age_filter(min_age, max_age)
        if age_clause:
            must.append(age_clause)
        if diagnosis_code:
            must.append({
                "nested": {"path": "diagnoses", "query": {"prefix": {"diagnoses.code": diagnosis_code}}}
            })
        if provider_name:
            must.append({"match": {"provider_name": provider_name}})
        date_range: dict = {}
        if start_date:
            date_range["gte"] = start_date
        if end_date:
            date_range["lte"] = end_date
        if date_range:
            must.append({"range": {"encounter_date": date_range}})
        query: dict = {"bool": {"must": must}} if must else {"match_all": {}}
        resp = es.count(index=settings.es_index, query=query)
        args = {"doc_type": doc_type, "state": state, "city": city, "min_age": min_age,
                "max_age": max_age, "diagnosis_code": diagnosis_code,
                "provider_name": provider_name, "start_date": start_date, "end_date": end_date}
        write_event(
            tool="count_records",
            args=args,
            hit_ids=[],
            redaction_stats={"fields_dropped": [], "entities_redacted": {}},
            latency_ms=(time.time() - start) * 1000,
        )
        return {
            "count": resp["count"],
            "filters_applied": {k: v for k, v in args.items() if v is not None},
        }

    # ── Aggregations (PHI-safe: counts / buckets only) ──────────────

    @mcp.tool()
    def aggregate_diagnoses(
        filter_code_prefix: Optional[str] = None,
        size: int = 200000,
    ) -> list[dict]:
        """Top diagnosis codes ranked by encounter frequency. Returns counts only — no PHI.
        filter_code_prefix: narrow to a specific ICD chapter/block (e.g. 'E11' for diabetes)."""
        start = time.time()
        es = get_client()
        nested_filter: dict = (
            {"prefix": {"diagnoses.code": filter_code_prefix}} if filter_code_prefix else {"match_all": {}}
        )
        resp = es.search(
            index=settings.es_index,
            query={"term": {"_docType": CLAIMS}},
            size=0,
            aggs={
                "by_diagnosis": {
                    "nested": {"path": "diagnoses"},
                    "aggs": {
                        "filtered": {
                            "filter": nested_filter,
                            "aggs": {"codes": {"terms": {"field": "diagnoses.code", "size": size}}},
                        }
                    },
                }
            },
        )
        buckets = (
            resp.get("aggregations", {})
            .get("by_diagnosis", {})
            .get("filtered", {})
            .get("codes", {})
            .get("buckets", [])
        )
        write_event(
            tool="aggregate_diagnoses",
            args={"filter_code_prefix": filter_code_prefix, "size": size},
            hit_ids=[],
            redaction_stats={"fields_dropped": [], "entities_redacted": {}},
            latency_ms=(time.time() - start) * 1000,
        )
        return [{"code": b["key"], "encounter_count": b["doc_count"]} for b in buckets]

    @mcp.tool()
    def aggregate_encounters_by_date(
        interval: str = "month",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        patient_id: Optional[str] = None,
    ) -> list[dict]:
        """Encounter volume over time grouped by calendar interval.
        interval: 'day' | 'week' | 'month' | 'quarter' | 'year'.
        Optionally narrow with start_date / end_date (YYYY-MM-DD) or a patient MRN."""
        start = time.time()
        es = get_client()
        must: list[dict] = [{"term": {"_docType": CLAIMS}}]
        if patient_id:
            must.append({"term": {"patient_id": patient_id}})
        date_range: dict = {}
        if start_date:
            date_range["gte"] = start_date
        if end_date:
            date_range["lte"] = end_date
        if date_range:
            must.append({"range": {"encounter_date": date_range}})
        valid_intervals = {"day", "week", "month", "quarter", "year"}
        resp = es.search(
            index=settings.es_index,
            query={"bool": {"must": must}},
            size=0,
            aggs={
                "by_date": {
                    "date_histogram": {
                        "field": "encounter_date",
                        "calendar_interval": interval if interval in valid_intervals else "month",
                        "format": "yyyy-MM-dd",
                        "min_doc_count": 1,
                    }
                }
            },
        )
        buckets = resp.get("aggregations", {}).get("by_date", {}).get("buckets", [])
        write_event(
            tool="aggregate_encounters_by_date",
            args={"interval": interval, "start_date": start_date, "end_date": end_date, "patient_id": patient_id},
            hit_ids=[],
            redaction_stats={"fields_dropped": [], "entities_redacted": {}},
            latency_ms=(time.time() - start) * 1000,
        )
        return [{"date": b["key_as_string"], "encounter_count": b["doc_count"]} for b in buckets]

    @mcp.tool()
    def aggregate_patients_by_age() -> list[dict]:
        """Patient count grouped into 10-year age buckets. Returns counts only — no PHI."""
        start = time.time()
        es = get_client()
        resp = es.search(
            index=settings.es_index,
            query={"term": {"_docType": ELIG}},
            size=0,
            aggs={"by_age": {"date_range": {"field": "dob", "format": "yyyy-MM-dd", "ranges": _age_ranges()}}},
        )
        buckets = resp.get("aggregations", {}).get("by_age", {}).get("buckets", [])
        write_event(
            tool="aggregate_patients_by_age",
            args={},
            hit_ids=[],
            redaction_stats={"fields_dropped": [], "entities_redacted": {}},
            latency_ms=(time.time() - start) * 1000,
        )
        return [{"age_bucket": b["key"], "patient_count": b["doc_count"]} for b in buckets]

    @mcp.tool()
    def aggregate_patients_by_location(group_by: str = "state") -> list[dict]:
        """Patient count grouped by geographic field. Returns counts only — no PHI.
        group_by: 'state' (default) | 'city' | 'zip'."""
        start = time.time()
        es = get_client()
        field = {"state": "address.state", "city": "address.city", "zip": "address.zip"}.get(
            group_by, "address.state"
        )
        resp = es.search(
            index=settings.es_index,
            query={"term": {"_docType": ELIG}},
            size=0,
            aggs={"by_location": {"terms": {"field": field, "size": 200000}}},
        )
        buckets = resp.get("aggregations", {}).get("by_location", {}).get("buckets", [])
        write_event(
            tool="aggregate_patients_by_location",
            args={"group_by": group_by},
            hit_ids=[],
            redaction_stats={"fields_dropped": [], "entities_redacted": {}},
            latency_ms=(time.time() - start) * 1000,
        )
        return [{"location": b["key"], "patient_count": b["doc_count"]} for b in buckets]

    @mcp.tool()
    def aggregate_encounters_by_provider(size: int = 200000) -> list[dict]:
        """Encounter count grouped by provider name. Returns counts only — no PHI."""
        start = time.time()
        es = get_client()
        resp = es.search(
            index=settings.es_index,
            query={"term": {"_docType": CLAIMS}},
            size=0,
            aggs={"by_provider": {"terms": {"field": "provider_name.keyword", "size": size}}},
        )
        buckets = resp.get("aggregations", {}).get("by_provider", {}).get("buckets", [])
        write_event(
            tool="aggregate_encounters_by_provider",
            args={"size": size},
            hit_ids=[],
            redaction_stats={"fields_dropped": [], "entities_redacted": {}},
            latency_ms=(time.time() - start) * 1000,
        )
        return [{"provider": b["key"], "encounter_count": b["doc_count"]} for b in buckets]

    @mcp.tool()
    def aggregate_diagnoses_by_cohort(
        state: Optional[str] = None,
        city: Optional[str] = None,
        min_age: Optional[int] = None,
        max_age: Optional[int] = None,
        size: int = 200000,
    ) -> list[dict]:
        """Top diagnoses for a filtered patient cohort (by location and/or age).
        Returns counts only — no PHI. Provide at least one filter."""
        if not any([state, city, min_age is not None, max_age is not None]):
            return [{"error": "Provide at least one cohort filter: state, city, min_age, or max_age"}]
        start = time.time()
        es = get_client()

        # Step 1: resolve matching patient IDs
        elig_must: list[dict] = [{"term": {"_docType": ELIG}}]
        if state:
            elig_must.append({"term": {"address.state": state}})
        if city:
            elig_must.append({"term": {"address.city": city}})
        age_clause = _age_filter(min_age, max_age)
        if age_clause:
            elig_must.append(age_clause)
        elig_resp = es.search(
            index=settings.es_index,
            query={"bool": {"must": elig_must}},
            _source=["patient_id"],
            size=500,
        )
        patient_ids = [h["_source"]["patient_id"] for h in elig_resp["hits"]["hits"]]
        if not patient_ids:
            return []

        # Step 2: aggregate diagnoses for those patients
        resp = es.search(
            index=settings.es_index,
            query={"bool": {"must": [
                {"term": {"_docType": CLAIMS}},
                {"terms": {"patient_id": patient_ids}},
            ]}},
            size=0,
            aggs={
                "by_diagnosis": {
                    "nested": {"path": "diagnoses"},
                    "aggs": {"codes": {"terms": {"field": "diagnoses.code", "size": size}}},
                }
            },
        )
        buckets = (
            resp.get("aggregations", {}).get("by_diagnosis", {}).get("codes", {}).get("buckets", [])
        )
        write_event(
            tool="aggregate_diagnoses_by_cohort",
            args={"state": state, "city": city, "min_age": min_age, "max_age": max_age, "size": size},
            hit_ids=[],
            redaction_stats={"fields_dropped": [], "entities_redacted": {}},
            latency_ms=(time.time() - start) * 1000,
        )
        return [{"code": b["key"], "encounter_count": b["doc_count"]} for b in buckets]

    @mcp.tool()
    def aggregate_encounters_per_patient(size: int = 200000) -> list[dict]:
        """Encounter count per patient, ranked highest first. Returns patient_id and count — no PHI."""
        start = time.time()
        es = get_client()
        resp = es.search(
            index=settings.es_index,
            query={"term": {"_docType": CLAIMS}},
            size=0,
            aggs={"by_patient": {"terms": {"field": "patient_id", "size": size, "order": {"_count": "desc"}}}},
        )
        buckets = resp.get("aggregations", {}).get("by_patient", {}).get("buckets", [])
        write_event(
            tool="aggregate_encounters_per_patient",
            args={"size": size},
            hit_ids=[],
            redaction_stats={"fields_dropped": [], "entities_redacted": {}},
            latency_ms=(time.time() - start) * 1000,
        )
        return [{"patient_id": b["key"], "encounter_count": b["doc_count"]} for b in buckets]

    # ── Catch-all: flexible custom aggregation ──────────────────────

    @mcp.tool()
    def run_aggregate(
        agg_json: str,
        filter_doc_type: Optional[str] = None,
        filter_patient_id: Optional[str] = None,
    ) -> dict:
        """Run a custom ES aggregation for any analytics not covered by other tools.
        agg_json: JSON string of the ES 'aggs' block (e.g. '{"my_agg": {"terms": {"field": "..."}}}').
        Always executes with size=0 — raw documents are never returned.
        top_hits sub-aggregations are stripped automatically to prevent PHI leakage.
        filter_doc_type: optionally restrict to 'elig' or 'claims'.
        filter_patient_id: optionally restrict to a specific patient MRN."""
        start = time.time()
        es = get_client()
        try:
            agg_def = _strip_top_hits(json.loads(agg_json))
        except json.JSONDecodeError as exc:
            return {"error": f"Invalid agg_json: {exc}"}
        must: list[dict] = []
        if filter_doc_type:
            must.append({"term": {"_docType": filter_doc_type}})
        if filter_patient_id:
            must.append({"term": {"patient_id": filter_patient_id}})
        query: dict = {"bool": {"must": must}} if must else {"match_all": {}}
        try:
            resp = es.search(index=settings.es_index, query=query, size=0, aggs=agg_def)
        except Exception as exc:
            return {"error": str(exc)}
        write_event(
            tool="run_aggregate",
            args={"filter_doc_type": filter_doc_type, "filter_patient_id": filter_patient_id},
            hit_ids=[],
            redaction_stats={"fields_dropped": [], "entities_redacted": {}},
            latency_ms=(time.time() - start) * 1000,
        )
        return resp.get("aggregations", {})
