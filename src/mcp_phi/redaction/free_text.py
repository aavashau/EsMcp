import logging
from typing import Optional

logger = logging.getLogger(__name__)

ENTITIES = [
    "PERSON",
    "PHONE_NUMBER",
    "US_SSN",
    "EMAIL_ADDRESS",
    "DATE_TIME",
    "LOCATION",
    "US_DRIVER_LICENSE",
]

_analyzer = None
_anonymizer = None
_operators = None


def _get_engines():
    global _analyzer, _anonymizer, _operators
    if _analyzer is None:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine
        from presidio_anonymizer.entities import OperatorConfig

        try:
            _analyzer = AnalyzerEngine()
            logger.info("Presidio loaded with en_core_web_lg")
        except Exception:
            logger.warning("en_core_web_lg not found, falling back to en_core_web_sm")
            from presidio_analyzer.nlp_engine import SpacyNlpEngine
            nlp_engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": "en_core_web_sm"}])
            _analyzer = AnalyzerEngine(nlp_engine=nlp_engine)

        _anonymizer = AnonymizerEngine()
        _operators = {
            entity: OperatorConfig("replace", {"new_value": f"<{entity}>"})
            for entity in ENTITIES
        }
    return _analyzer, _anonymizer, _operators


def anonymize(text: str) -> tuple[str, dict[str, int]]:
    """Run Presidio on free text. Returns (anonymized_text, {entity_type: count})."""
    if not text:
        return text, {}
    try:
        analyzer, anonymizer, operators = _get_engines()
        results = analyzer.analyze(text=text, entities=ENTITIES, language="en")
        if not results:
            return text, {}
        entity_counts: dict[str, int] = {}
        for r in results:
            entity_counts[r.entity_type] = entity_counts.get(r.entity_type, 0) + 1
        anonymized = anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators=operators,
        )
        return anonymized.text, entity_counts
    except Exception:
        logger.exception("Presidio anonymization failed; returning original text")
        return text, {}
