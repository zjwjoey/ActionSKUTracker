"""Action Chinese Localization Intelligence V1.

The package is deliberately deterministic and side-effect free at its core:
Spanish source facts are parsed into semantic facts, planned into the seven
Chinese export fields, then validated.  Persistence, AI and production apply
are adapters around this engine.
"""
from .contracts import (
    POLICY_VERSION,
    SourceFacts,
    SemanticFact,
    LocalizationField,
    LocalizationPlan,
)
from .engine import LocalizationEngine
from .knowledge import KnowledgeContext

__all__ = [
    "POLICY_VERSION", "SourceFacts", "SemanticFact", "LocalizationField",
    "LocalizationPlan", "LocalizationEngine", "KnowledgeContext",
]
