"""
Build the knowledge base: chunk the project's sources, embed them, index
identifiers. Called by the retrieval service (rebuild in place) and by
`python -m asic_agent kb-reindex`.

Sources are config/project.json knowledge_sources, plus the folder of
confirmed-fix patterns (kind "pattern"; only confirmed fixes are exported
there, README: design rules).

Writes <index dir>/index/ (vectors, chunk records, build metadata) and
<index dir>/keyword_index/ (exact-identifier inverted index). Without an
embeddings provider the vectors are empty and retrieval is keyword-only.
Re-running rebuilds both from scratch; at this corpus size incremental
updating would add complexity for no gain.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .chunker import DOC_EXTENSIONS, RTL_EXTENSIONS, chunk_file
from .keyword_index import build_index, save_index
from .store import StoredChunk, VectorStore

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "build", "dist",
    ".tox", ".mypy_cache", "runs", "index", "keyword_index",
}
INDEXABLE = DOC_EXTENSIONS | RTL_EXTENSIONS


def collect_files(repo: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(repo.rglob("*")):
        if not p.is_file():
            continue
        # Relative parts: an --extra directory may itself live under runs/.
        if any(part in SKIP_DIRS for part in p.relative_to(repo).parts):
            continue
        if p.suffix.lower() in INDEXABLE:
            out.append(p)
    return out


KIND_OF = {"spec": "doc", "doc": "doc", "rtl": "rtl", "testbench": "testbench"}


def build_sources(sources: list[tuple[Path, str]], extras: list[Path], emb, out: Path,
                  log=print) -> dict:
    """Index a list of (folder, kind) sources — the knowledge_sources of
    config/project.json — plus confirmed-fix pattern folders. kind overrides what the
    file extension would say, so a testbench .sv is indexed as 'testbench'."""
    chunks, counts = [], {}
    for path, kind in sources:
        if not path.exists():
            log(f"  skipped {path}: not found")
            continue
        files = collect_files(path) if path.is_dir() else [path]
        for f in files:
            for c in chunk_file(f):
                c.kind = KIND_OF.get(kind, kind) if kind != "auto" else c.kind
                chunks.append(c)
                counts[c.kind] = counts.get(c.kind, 0) + 1
        log(f"  {path}: {len(files)} files ({kind})")
    for x in extras:
        if x.is_dir():
            for f in collect_files(x):
                for c in chunk_file(f):
                    c.kind = "pattern"
                    chunks.append(c)
                    counts["pattern"] = counts.get("pattern", 0) + 1
    if not chunks:
        raise ValueError("the knowledge sources produced no passages")
    log(f"  {len(chunks)} chunks " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    texts = [_embed_text(c) for c in chunks]
    if emb is None:
        # No embeddings provider configured: keyword-only index (no vectors).
        vectors = np.zeros((len(chunks), 0), dtype=np.float32)
        log("  no embeddings provider: keyword-only index")
    else:
        vectors = emb.encode(texts)
    stored = [StoredChunk.from_dict(c.to_dict()) for c in chunks]
    meta = {
        "embedder": emb.name if emb else "none",
        "sources": [{"path": str(p), "kind": k} for p, k in sources],
        "n_chunks": len(stored),
        "n_doc": counts.get("doc", 0), "n_rtl": counts.get("rtl", 0),
        "n_testbench": counts.get("testbench", 0), "n_pattern": counts.get("pattern", 0),
        "dim": int(vectors.shape[1]),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    VectorStore(vectors, stored, meta).save(out / "index")
    kw = build_index(chunks)
    save_index(kw, out / "keyword_index" / "identifiers.json")
    log(f"  wrote index ({len(kw['defines'])} defined identifiers)")
    return meta


def _embed_text(chunk) -> str:
    """Prepend the label so heading ancestry and module name are embedded too.

    Without this, a chunk describing a register body never mentions the
    register's own name and will not retrieve for a query that names it.
    """
    return f"{chunk.label}\n{chunk.text}" if chunk.label else chunk.text

