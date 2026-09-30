from __future__ import annotations

from collections.abc import Sequence
from threading import Lock

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from pii_redaction.base import RedactionError


DEFAULT_ENTITIES = (
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "IP_ADDRESS",
    "US_SSN",
)


class PresidioRedactor:
    def __init__(
        self,
        *,
        entities: Sequence[str] = DEFAULT_ENTITIES,
        score_threshold: float = 0.4,
        spacy_model: str = "en_core_web_sm",
    ) -> None:
        self._entities = list(entities)
        self._score_threshold = score_threshold
        self._spacy_model = spacy_model
        self._engines: tuple[AnalyzerEngine, AnonymizerEngine] | None = None
        self._lock = Lock()

    def _get_engines(self) -> tuple[AnalyzerEngine, AnonymizerEngine]:
        with self._lock:
            if self._engines is None:
                nlp_engine = NlpEngineProvider(
                    nlp_configuration={
                        "nlp_engine_name": "spacy",
                        "models": [{"lang_code": "en", "model_name": self._spacy_model}],
                    }
                ).create_engine()
                analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
                self._engines = (analyzer, AnonymizerEngine())
            return self._engines

    def redact(self, text: str) -> str:
        if not text:
            return text
        try:
            analyzer, anonymizer = self._get_engines()
            findings = analyzer.analyze(
                text=text,
                entities=self._entities,
                language="en",
                score_threshold=self._score_threshold,
            )
            if not findings:
                return text
            return anonymizer.anonymize(
                text=text,
                analyzer_results=findings,
                operators={"DEFAULT": OperatorConfig("replace")},
            ).text
        except Exception as exc:  # noqa: BLE001
            raise RedactionError(f"presidio redaction failed: {type(exc).__name__}") from exc
