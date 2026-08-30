"""Knowledge Production V1 contracts and deterministic offline helpers.

The package is deliberately separate from collection/lifecycle.  It can
prepare resolutions, queues and approval decisions without writing production
facts or calling a model provider.
"""

from .contracts import (
    KNOWLEDGE_FIELDS,
    KNOWLEDGE_STATES,
    source_hash,
    Resolution,
    ResolutionField,
)
from .storage import KnowledgeStore

__all__ = [
    "KNOWLEDGE_FIELDS",
    "KNOWLEDGE_STATES",
    "source_hash",
    "Resolution",
    "ResolutionField",
    "KnowledgeStore",
]
