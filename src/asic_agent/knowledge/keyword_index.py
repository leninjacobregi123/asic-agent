"""
Exact-identifier index — the keyword half of hybrid retrieval.

Why this exists: embedding similarity matches meaning, not spelling. A query
naming `gpio_ip_q` or `GPIO_IE` is asking about one specific object, and vector
search will happily return a chunk about a *different* register that discusses
similar concepts. Hardware design is unusually dense with exact identifiers, so
an exact-match path is not a nicety here.

The index is a plain inverted index: identifier -> [chunk_id, ...], plus a
normalised form so `GPIO_IE`, `gpio_ie` and `gpio_ie_q` can be related.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

# Declarations worth indexing as "this chunk defines X", not merely mentions it.
_DECL_PATTERNS = [
    re.compile(r"\bmodule\s+(\w+)"),
    re.compile(r"\bpackage\s+(\w+)"),
    re.compile(r"\binterface\s+(\w+)"),
    re.compile(r"\b(?:parameter|localparam)\s+(?:\w+\s+)*?(\w+)\s*="),
    re.compile(r"`define\s+(\w+)"),
    re.compile(r"\b(?:input|output|inout)\s+(?:\w+\s+)*?(\w+)\s*(?:,|\)|;|$)"),
    re.compile(r"\b(?:logic|wire|reg|bit)\s*(?:\[[^\]]*\]\s*)?(\w+)\s*(?:,|;|=)"),
    re.compile(r"\btypedef\s+.*?\b(\w+)\s*;"),
]

# Signals *driven* in a chunk: the left-hand side of assign, <= and = . For a
# behavioural question ("when is X cleared", "is X ever high") the chunk that
# drives X is the answer; the declaration only gives its type.
_DRIVER_PATTERNS = [
    re.compile(r"\bassign\s+(\w+)"),
    re.compile(r"(?m)^\s*(\w+)\s*(?:\[[^\]]*\]\s*)*<="),
    re.compile(r"(?m)^\s*(\w+)\s*(?:\[[^\]]*\]\s*)*=(?!=)"),
]

# Any identifier-shaped token, used for "mentions" and for parsing the query.
_TOKEN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")

# Register macros are spelled `REG_<name>` in PULP RTL and `<name>` in the spec.
_MACRO_PREFIXES = ("reg_",)

# Tokens too common to be useful as exact-match keys.
_STOPWORDS = {
    # HDL keywords
    "module", "endmodule", "input", "output", "inout", "logic", "wire", "reg",
    "bit", "always", "always_ff", "always_comb", "always_latch", "assign",
    "begin", "end", "if", "else", "case", "endcase", "default", "posedge",
    "negedge", "parameter", "localparam", "int", "unsigned", "signed", "for",
    "generate", "endgenerate", "initial", "task", "function", "endtask",
    "endfunction", "typedef", "struct", "enum", "package", "endpackage",
    "import", "interface", "endinterface", "return", "void", "static",
    # English filler that would otherwise match every chunk
    "the", "a", "an", "is", "are", "of", "to", "in", "on", "and", "or", "not",
    "this", "that", "it", "its", "be", "as", "by", "with", "for", "from",
    "what", "which", "when", "how", "does", "do", "can", "will", "shall",
    "value", "bit", "bits", "read", "write", "set", "clear", "state",
}

_MIN_IDENT_LEN = 3


def _normalise(ident: str) -> str:
    """Fold case and strip common RTL suffixes so related spellings match.

    `gpio_ie_q` (the register) and `GPIO_IE` (the spec's name for it) normalise
    to the same key, which is what lets a spec-worded query find the RTL.
    """
    s = ident.lower()
    for suffix in ("_q", "_d", "_n", "_i", "_o", "_reg", "_next", "_offset", "_pad"):
        if s.endswith(suffix) and len(s) - len(suffix) >= _MIN_IDENT_LEN:
            s = s[: -len(suffix)]
            break
    return s


def _is_indexable(tok: str) -> bool:
    return (
        len(tok) >= _MIN_IDENT_LEN
        and tok.lower() not in _STOPWORDS
        and not tok.isdigit()
    )


def _looks_like_identifier(tok: str) -> bool:
    """True for code-shaped tokens: `gpio_dir`, `PADDIR_00_31`, `PSLVERR`, `s0`.

    Prose is full of words that are not identifiers ("drive", "register",
    "configuration"). Treating them as exact-match keys lets a paraphrased
    question score a keyword hit on whichever doc chunk shares an English word,
    and per-path score normalisation then inflates that weak hit to 1.0.
    RTL tokens are indexed regardless; this filter applies to prose — doc
    text, doc headings, and the query.
    """
    return (
        _is_indexable(tok)
        and ("_" in tok or any(ch.isdigit() for ch in tok) or tok.isupper()
             or (tok[0].islower() and any(ch.isupper() for ch in tok)))
    )


def _aliases(tok: str) -> list[str]:
    """Normalised key(s) for a token: itself, plus the spec name of a macro."""
    key = _normalise(tok)
    out = [key]
    for p in _MACRO_PREFIXES:
        if key.startswith(p) and len(key) - len(p) >= _MIN_IDENT_LEN:
            out.append(key[len(p):])
    return out


def build_index(chunks: list) -> dict:
    """Build the inverted index from chunk objects (or dicts).

    Three tiers, strongest first: the chunk that *drives* a signal, the chunk
    that *declares* it (or, for docs, whose heading names it), and chunks that
    merely mention it.
    """
    defines: dict[str, set[str]] = defaultdict(set)
    drives: dict[str, set[str]] = defaultdict(set)
    mentions: dict[str, set[str]] = defaultdict(set)

    for c in chunks:
        cid = c.chunk_id if hasattr(c, "chunk_id") else c["chunk_id"]
        text = c.text if hasattr(c, "text") else c["text"]
        label = c.label if hasattr(c, "label") else c["label"]
        kind = c.kind if hasattr(c, "kind") else c["kind"]
        keep = _is_indexable if kind == "rtl" else _looks_like_identifier

        scan = text.replace("//", " ")
        if kind == "rtl":
            for pat in _DECL_PATTERNS:
                for m in pat.finditer(scan):
                    tok = m.group(1)
                    if _is_indexable(tok):
                        for key in _aliases(tok):
                            defines[key].add(cid)
            for pat in _DRIVER_PATTERNS:
                for m in pat.finditer(scan):
                    tok = m.group(1)
                    if _is_indexable(tok):
                        drives[_normalise(tok)].add(cid)

        # A doc heading naming a register is as strong as an RTL declaration —
        # "GPIO_IP (offset 0x10)" is where that register is defined in prose.
        # Only the last heading level names this chunk's subject; ancestors
        # ("Register map") are shared by every sibling and would tie them all.
        own_heading = label.split(">")[-1]
        for tok in _TOKEN.findall(own_heading):
            if _looks_like_identifier(tok):
                for key in _aliases(tok):
                    defines[key].add(cid)

        for tok in _TOKEN.findall(text):
            if keep(tok):
                for key in _aliases(tok):
                    mentions[key].add(cid)

    return {
        "defines": {k: sorted(v) for k, v in defines.items()},
        "drives": {k: sorted(v) for k, v in drives.items()},
        "mentions": {k: sorted(v) for k, v in mentions.items()},
    }


def save_index(index: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=1), encoding="utf-8")


def load_index(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def query_terms(query: str) -> list[str]:
    """Identifier-shaped tokens in a query, normalised, most specific first.

    Longer tokens are more discriminating (`gpio_ip` beats `gpio`), so they are
    returned first and weighted higher by the caller.
    """
    seen: dict[str, int] = {}
    for tok in _TOKEN.findall(query):
        if _looks_like_identifier(tok):
            seen[_normalise(tok)] = max(seen.get(_normalise(tok), 0), len(tok))
    return [t for t, _ in sorted(seen.items(), key=lambda kv: -kv[1])]


def search(index: dict, query: str, limit: int = 20) -> list[tuple[str, float]]:
    """Score chunks by exact identifier overlap with the query.

    A definition hit is worth more than a mention; rarer identifiers are worth
    more than ones appearing all over the corpus.
    """
    terms = query_terms(query)
    if not terms:
        return []

    defines, mentions = index["defines"], index["mentions"]
    drives = index.get("drives", {})          # absent in indexes built before it existed
    n_chunks = len({c for ids in mentions.values() for c in ids}) or 1
    scores: dict[str, float] = defaultdict(float)

    for term in terms:
        d_hits = defines.get(term, [])
        v_hits = drives.get(term, [])
        m_hits = mentions.get(term, [])
        if not d_hits and not m_hits and not v_hits:
            continue
        # Inverse document frequency: a term in most chunks discriminates little.
        df = max(len(m_hits), 1)
        idf = max(0.25, 1.0 - (df / n_chunks))
        for cid in v_hits:
            scores[cid] += 4.0 * idf
        for cid in d_hits:
            scores[cid] += 3.0 * idf
        for cid in m_hits:
            scores[cid] += 1.0 * idf

    return sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
