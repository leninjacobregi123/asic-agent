"""
Hybrid retrieval — the single interface every agent calls.

Combines two search paths and merges them:

  vector   embedding similarity. Good at meaning: a query about "how the
           interrupt clears" finds the right section even when it never uses
           the word "clear".
  keyword  exact identifier match. Good at spelling: a query naming
           `gpio_ip_q` returns the chunk that declares it, which vector
           similarity alone will not reliably do.

Merging: each path's scores are normalised to [0, 1] against that path's own
top hit, then combined as a weighted sum. Normalising per-path matters because
raw cosine similarity and raw identifier-overlap scores are not on comparable
scales, and summing them directly lets whichever path happens to produce larger
numbers dominate regardless of relevance.

A chunk found by both paths keeps both contributions, which is what makes
agreement between the two rank highest.

Without an embeddings provider (or with an index built by a different
embedding model) the vector path is skipped and `Retriever.mode` reports
"keyword-only".

    from asic_agent.knowledge.retriever import Retriever
    r = Retriever.load()
    for hit in r.retrieve("what clears GPIO_IP", k=5):
        print(hit.chunk_id, hit.score, hit.source_path, hit.line_start)
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

from .. import log, settings
from .bm25 import BM25
from .embeddings import get_embedder
from .keyword_index import load_index, search as kw_search
from .store import VectorStore

_log = log.get("knowledge")

DEFAULT_VECTOR_WEIGHT = 0.6
DEFAULT_KEYWORD_WEIGHT = 0.4
# Each path is asked for more candidates than the caller wants, so a chunk
# ranked mid-list by one path can still win on combined score.
CANDIDATE_MULTIPLIER = 4
# Keyword-only mode: BM25 text score vs identifier score. Mirrors the hybrid
# split validated in M2 (0.6/0.4); in that mode Hit.vector_score holds the
# BM25 text score.
KEYWORD_ONLY_TEXT_WEIGHT = 0.6


@dataclass
class Hit:
    chunk_id: str
    score: float
    vector_score: float
    keyword_score: float
    source_path: str
    label: str
    kind: str
    line_start: int
    line_end: int
    text: str

    def location(self) -> str:
        return f"{self.source_path}:{self.line_start}-{self.line_end}"

    def to_dict(self) -> dict:
        return asdict(self)


def _normalise(scored: list[tuple[str, float]]) -> dict[str, float]:
    """Scale a path's scores to [0, 1] against its own best hit."""
    if not scored:
        return {}
    top = max(s for _, s in scored)
    if top <= 0:
        return {cid: 0.0 for cid, _ in scored}
    return {cid: s / top for cid, s in scored}


class Retriever:
    def __init__(self, store: VectorStore, kw_index: dict, embedder):
        self.store = store
        self.kw_index = kw_index
        self.embedder = embedder
        # Word-level ranking for keyword-only mode (no vectors to rank by meaning).
        self.bm25 = BM25({c.chunk_id: f"{c.label}\n{c.text}" for c in store.chunks})

    @classmethod
    def load(cls, base: Path | None = None):
        """Load the built index. Queries must be embedded by the same model as
        the corpus; if the configured embedder differs (or none is set), search
        is keyword-only until the index is rebuilt, and `mode` says so."""
        base = base or settings.INDEX_DIR
        index_dir = base / "index"
        if not (index_dir / "chunks.jsonl").exists():
            raise FileNotFoundError(f"No index at {index_dir}. Build it: python -m asic_agent kb-reindex")
        store = VectorStore.load(index_dir)
        kw_index = load_index(base / "keyword_index" / "identifiers.json")
        built_with = store.meta.get("embedder", "none")
        emb = get_embedder() if built_with != "none" else None
        if emb is not None and emb.name != built_with:
            _log.warning("index built with %s but %s is configured: keyword-only until rebuilt",
                         built_with, emb.name)
            emb = None
        return cls(store, kw_index, emb)

    @property
    def mode(self) -> str:
        return "hybrid" if self.embedder is not None and self.store.vectors.size else "keyword-only"

    def retrieve(
        self,
        query: str,
        k: int = 5,
        vector_weight: float = DEFAULT_VECTOR_WEIGHT,
        keyword_weight: float = DEFAULT_KEYWORD_WEIGHT,
        keyword_only: bool = False,
    ) -> list[Hit]:
        if not query.strip():
            return []

        pool = max(k * CANDIDATE_MULTIPLIER, 10)

        if self.mode == "hybrid" and not keyword_only:
            qv = self.embedder.encode([query])[0]
            vec_raw = self.store.search(qv, limit=pool)
        else:
            # Keyword-only: BM25 over the text takes the place of the vector
            # path, so plain-language questions still rank (identifier index
            # alone ties every chunk sharing one identifier).
            vec_raw = self.bm25.search(query, limit=pool)
            vector_weight, keyword_weight = KEYWORD_ONLY_TEXT_WEIGHT, 1 - KEYWORD_ONLY_TEXT_WEIGHT
        kw_raw = kw_search(self.kw_index, query, limit=pool)

        vec = _normalise(vec_raw)
        kw = _normalise(kw_raw)

        combined: dict[str, float] = {}
        for cid in set(vec) | set(kw):
            combined[cid] = (
                vector_weight * vec.get(cid, 0.0)
                + keyword_weight * kw.get(cid, 0.0)
            )

        ranked = sorted(combined.items(), key=lambda kv: -kv[1])[:k]
        hits: list[Hit] = []
        for cid, score in ranked:
            c = self.store.get(cid)
            if c is None:
                continue
            hits.append(
                Hit(
                    chunk_id=cid,
                    score=round(score, 4),
                    vector_score=round(vec.get(cid, 0.0), 4),
                    keyword_score=round(kw.get(cid, 0.0), 4),
                    source_path=c.source_path,
                    label=c.label,
                    kind=c.kind,
                    line_start=c.line_start,
                    line_end=c.line_end,
                    text=c.text,
                )
            )
        return hits


