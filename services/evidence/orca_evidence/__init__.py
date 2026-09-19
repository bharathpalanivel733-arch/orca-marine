"""ORCA evidence layer (PLAN.md Phase 3).

Structured evidence with ``as_of`` semantics, a capability registry the planner selects
from, and a RAG corpus whose retrieval is gated on issue time and region so an outdated
or geographically irrelevant advisory cannot be returned at all.
"""

from orca_evidence.corpus import Chunk, CorpusDocument, CorpusIngestor, chunk_document
from orca_evidence.embeddings import (
    EMBEDDING_DIMENSIONS,
    BgeM3Embedder,
    DeterministicEmbedder,
    Embedder,
)
from orca_evidence.registry import (
    DEFAULT_CAPABILITIES,
    DatasetRegistry,
    Rejection,
    RejectionReason,
    Selection,
)
from orca_evidence.retrieval import (
    HybridRetriever,
    RetrievalFilters,
    reciprocal_rank_fusion,
)
from orca_evidence.structured import StructuredEvidence, StructuredEvidenceStore

SERVICE_NAME = "orca-evidence"
__version__ = "0.1.0"

__all__ = [
    "DEFAULT_CAPABILITIES",
    "EMBEDDING_DIMENSIONS",
    "SERVICE_NAME",
    "BgeM3Embedder",
    "Chunk",
    "CorpusDocument",
    "CorpusIngestor",
    "DatasetRegistry",
    "DeterministicEmbedder",
    "Embedder",
    "HybridRetriever",
    "Rejection",
    "RejectionReason",
    "RetrievalFilters",
    "Selection",
    "StructuredEvidence",
    "StructuredEvidenceStore",
    "__version__",
    "chunk_document",
    "reciprocal_rank_fusion",
]
