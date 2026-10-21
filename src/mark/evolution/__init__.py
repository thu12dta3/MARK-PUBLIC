"""Módulo de Evolução Contínua, Feedback e Decisão Baseada em Evidências do MARK."""

from .feedback import (
    FailureCategory,
    FeedbackCollector,
    FeedbackEntry,
    FeedbackRating,
)
from .dataset_prep import (
    DatasetSanitizer,
    PERMISSIVE_LICENSES,
    SanitizationReport,
)
from .evidence_decision import (
    DecisionAction,
    EvidenceDecisionEngine,
    EvidenceReport,
)

VERSION = "0.1"

__all__ = [
    "FeedbackCollector",
    "FeedbackEntry",
    "FeedbackRating",
    "FailureCategory",
    "DatasetSanitizer",
    "PERMISSIVE_LICENSES",
    "SanitizationReport",
    "DecisionAction",
    "EvidenceDecisionEngine",
    "EvidenceReport",
    "VERSION",
]
