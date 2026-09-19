"""Embeddings for the knowledge corpus (PLAN.md Phase 3.3).

**BGE-M3** (``BAAI/bge-m3``) is the production embedder. It was chosen for a specific
reason rather than popularity: the corpus and the queries are multilingual — a Tamil
voice query has to retrieve an English INCOIS advisory — and BGE-M3 is trained for
cross-lingual retrieval at 1024 dimensions, which is what the ``vector(1024)`` column is
pinned to.

Two implementations share one interface:

* :class:`BgeM3Embedder` — the real model, behind the optional ``embeddings`` extra. It
  downloads ~2 GB of weights on first use, so it is never imported at module load.
* :class:`DeterministicEmbedder` — **not semantic.** A seeded hash of token features,
  used so the retrieval pipeline, the SQL, the fusion and the filters can be tested
  without a 2 GB download or a GPU. It produces stable, normalised 1024-d vectors and
  nothing more. It must never be used to serve real retrieval, and
  ``is_semantic`` is False so callers can assert that.

Both normalise to unit length, so cosine distance and inner product agree and the HNSW
index behaves consistently whichever is in use.
"""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence

EMBEDDING_DIMENSIONS = 1024
BGE_M3_MODEL_ID = "BAAI/bge-m3"

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def normalise(vector: Sequence[float]) -> list[float]:
    """Scale a vector to unit length. A zero vector is returned unchanged."""
    magnitude = math.sqrt(sum(component * component for component in vector))
    if magnitude == 0.0:
        return list(vector)
    return [component / magnitude for component in vector]


class Embedder(ABC):
    """Turns text into vectors for the corpus and for queries."""

    dimensions: int = EMBEDDING_DIMENSIONS

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Identifier recorded alongside stored vectors."""

    @property
    @abstractmethod
    def is_semantic(self) -> bool:
        """Whether these vectors carry real meaning.

        False for the deterministic test embedder. Production code paths assert this is
        True, so a test double can never silently end up serving retrieval.
        """

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed corpus passages."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed a search query.

        Separate from ``embed_documents`` because asymmetric models instruct the two
        differently; keeping the split in the interface means adopting such a model later
        is not a breaking change.
        """


class DeterministicEmbedder(Embedder):
    """Stable, non-semantic vectors for testing the pipeline.

    Hashes each token into the vector space with a fixed seed, so the same text always
    produces the same vector and texts sharing vocabulary land closer together than texts
    that share none. That is enough to exercise ranking, fusion and filters — and it is
    emphatically not semantic similarity.
    """

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self.dimensions = dimensions

    @property
    def model_id(self) -> str:
        return f"deterministic-hash-{self.dimensions}"

    @property
    def is_semantic(self) -> bool:
        return False

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = _TOKEN_RE.findall(text.lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        return normalise(vector)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class BgeM3Embedder(Embedder):
    """BGE-M3 via sentence-transformers.

    Requires the ``embeddings`` extra. The model is loaded lazily on first use, because
    importing torch and pulling ~2 GB of weights is not something an import statement
    should do.
    """

    def __init__(self, model_id: str = BGE_M3_MODEL_ID, *, device: str | None = None) -> None:
        self._model_id = model_id
        self._device = device
        self._model: object | None = None

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def is_semantic(self) -> bool:
        return True

    @staticmethod
    def available() -> bool:
        """Whether sentence-transformers is installed."""
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def _use_os_trust_store() -> None:
        """Let the OS build the TLS chain before contacting the model hub.

        huggingface.co is served through a chain OpenSSL cannot complete on this
        platform, so the first download attempt fails with CERTIFICATE_VERIFY_FAILED and
        reads misleadingly like "no internet connection". ``truststore`` delegates chain
        building to the OS, exactly as the Phase 0.4 spikes do for the Indian government
        endpoints. Verification stays enabled; this never disables it.
        """
        try:
            import truststore
        except ImportError:
            return
        truststore.inject_into_ssl()

    def _load(self) -> object:
        if self._model is not None:
            return self._model
        self._use_os_trust_store()
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - dependency guidance
            msg = (
                "BGE-M3 needs the 'embeddings' extra: "
                "pip install -e 'services/evidence[embeddings]'"
            )
            raise RuntimeError(msg) from exc
        self._model = SentenceTransformer(self._model_id, device=self._device)
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._load()
        vectors = model.encode(  # type: ignore[attr-defined]
            list(texts), normalize_embeddings=True, show_progress_bar=False
        )
        return [list(map(float, vector)) for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        model = self._load()
        vector = model.encode(  # type: ignore[attr-defined]
            [text], normalize_embeddings=True, show_progress_bar=False
        )[0]
        return [float(component) for component in vector]
