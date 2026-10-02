"""
Chunking for the RAG knowledge base.

Two chunkers, because documentation and RTL have different natural boundaries:
  - Documentation (.md/.rst/.txt): split on headings, so a chunk is one section.
  - RTL (.v/.sv/.svh):            split on module boundaries, so a chunk is one
                                  module. Oversized modules are split further on
                                  always/function/task blocks.

Chunking on fixed character counts is the thing to avoid: it cuts a register
description in half and neither half retrieves well.

Every chunk carries its source path and line range so a retrieval result can be
traced back to the file it came from. That traceability is what makes the
retrieval report in Phase 1 checkable by a human.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator

DOC_EXTENSIONS = {".md", ".markdown", ".rst", ".txt"}
RTL_EXTENSIONS = {".v", ".sv", ".svh", ".vh"}

# A chunk larger than this is split further. Chosen so a chunk comfortably fits
# in an agent's context alongside several others, not because of a model limit.
MAX_CHUNK_CHARS = 4000
# Below this, a "section" is usually a stray heading with no body; merge it
# forward rather than indexing a chunk that is mostly whitespace.
MIN_CHUNK_CHARS = 40


@dataclass
class Chunk:
    """One retrievable unit of text, traceable to its source."""

    chunk_id: str
    text: str
    source_path: str
    kind: str  # "doc" | "rtl"
    label: str  # heading trail for docs, module name for RTL
    line_start: int
    line_end: int

    def to_dict(self) -> dict:
        return asdict(self)


def _clean(text: str) -> str:
    return text.strip("\n").rstrip()


def _mk_id(source_path: str, index: int) -> str:
    stem = Path(source_path).name.replace(".", "_")
    return f"{stem}:{index:03d}"


# --------------------------------------------------------------------------
# Documentation chunking
# --------------------------------------------------------------------------

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
# RST underline style: a line of === or --- directly under a title line.
_RST_UNDERLINE = re.compile(r"^([=\-~^\"'`#*+]){3,}\s*$")


def _doc_sections(lines: list[str]) -> Iterator[tuple[str, int, int]]:
    """Yield (heading_trail, start_line, end_line) for each section, 1-indexed.

    The heading trail keeps ancestry ("Registers > Control Register"), so a chunk
    about a control register still retrieves for a query naming the peripheral,
    even when the section body never repeats the peripheral's name.
    """
    boundaries: list[tuple[int, int, str]] = []  # (line_idx, level, title)

    for i, line in enumerate(lines):
        m = _MD_HEADING.match(line)
        if m:
            boundaries.append((i, len(m.group(1)), m.group(2)))
            continue
        # RST: a title line followed by an underline of punctuation
        if (
            i + 1 < len(lines)
            and _RST_UNDERLINE.match(lines[i + 1])
            and line.strip()
            and not _RST_UNDERLINE.match(line)
            and len(lines[i + 1].strip()) >= len(line.strip()) - 2
        ):
            boundaries.append((i, 2, line.strip()))

    if not boundaries:
        yield ("", 1, len(lines))
        return

    # Preamble before the first heading is its own chunk if substantial.
    if boundaries[0][0] > 0:
        yield ("", 1, boundaries[0][0])

    trail: list[tuple[int, str]] = []
    for idx, (line_idx, level, title) in enumerate(boundaries):
        while trail and trail[-1][0] >= level:
            trail.pop()
        trail.append((level, title))
        end = boundaries[idx + 1][0] if idx + 1 < len(boundaries) else len(lines)
        yield (" > ".join(t for _, t in trail), line_idx + 1, end)


def chunk_doc(path: Path, text: str) -> list[Chunk]:
    lines = text.splitlines()
    chunks: list[Chunk] = []
    pending: tuple[str, int, list[str]] | None = None

    for label, start, end in _doc_sections(lines):
        body = "\n".join(lines[start - 1 : end])
        if pending is not None:
            p_label, p_start, p_lines = pending
            body = "\n".join(p_lines) + "\n" + body
            label = p_label or label
            start = p_start
            pending = None
        if len(_clean(body)) < MIN_CHUNK_CHARS:
            # Too thin to stand alone — carry it into the next section.
            pending = (label, start, lines[start - 1 : end])
            continue
        for piece_text, piece_start, piece_end in _split_oversized(
            body, start, end
        ):
            chunks.append(
                Chunk(
                    chunk_id=_mk_id(str(path), len(chunks)),
                    text=_clean(piece_text),
                    source_path=str(path),
                    kind="doc",
                    label=label,
                    line_start=piece_start,
                    line_end=piece_end,
                )
            )

    if pending is not None:
        p_label, p_start, p_lines = pending
        body = _clean("\n".join(p_lines))
        if body:
            chunks.append(
                Chunk(
                    chunk_id=_mk_id(str(path), len(chunks)),
                    text=body,
                    source_path=str(path),
                    kind="doc",
                    label=p_label,
                    line_start=p_start,
                    line_end=p_start + len(p_lines) - 1,
                )
            )
    return chunks


def _split_oversized(
    body: str, start: int, end: int
) -> Iterator[tuple[str, int, int]]:
    """Split a too-large body on blank lines, preserving line numbers."""
    if len(body) <= MAX_CHUNK_CHARS:
        yield (body, start, end)
        return

    lines = body.splitlines()
    buf: list[str] = []
    buf_start = start
    size = 0
    for offset, line in enumerate(lines):
        buf.append(line)
        size += len(line) + 1
        at_break = not line.strip()
        if size >= MAX_CHUNK_CHARS and at_break:
            yield ("\n".join(buf), buf_start, start + offset)
            buf, size, buf_start = [], 0, start + offset + 1
    if buf:
        yield ("\n".join(buf), buf_start, end)


# --------------------------------------------------------------------------
# RTL chunking
# --------------------------------------------------------------------------

_MODULE_START = re.compile(r"^\s*(module|package|interface|program)\s+(\w+)")
_MODULE_END = re.compile(r"^\s*end(module|package|interface|program)\b")
# Secondary split points inside a very large module.
_INNER_BLOCK = re.compile(
    r"^\s*(always(_ff|_comb|_latch)?\b|initial\b|task\b|function\b|generate\b)"
)


def _strip_block_comments(text: str) -> str:
    """Blank out /* */ comments so they cannot hide a module boundary.

    Replaced with spaces rather than deleted, so line numbers stay correct.
    """
    out = []
    i, n, in_block = 0, len(text), False
    while i < n:
        if not in_block and text.startswith("/*", i):
            in_block = True
            out.append("  ")
            i += 2
        elif in_block and text.startswith("*/", i):
            in_block = False
            out.append("  ")
            i += 2
        elif in_block:
            out.append("\n" if text[i] == "\n" else " ")
            i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def chunk_rtl(path: Path, text: str) -> list[Chunk]:
    scan_lines = _strip_block_comments(text).splitlines()
    real_lines = text.splitlines()
    chunks: list[Chunk] = []

    spans: list[tuple[str, int, int]] = []  # (module_name, start_idx, end_idx)
    current: tuple[str, int] | None = None
    depth = 0

    for i, line in enumerate(scan_lines):
        code = line.split("//", 1)[0]
        m = _MODULE_START.match(code)
        if m and current is None:
            current = (m.group(2), i)
            depth = 1
            continue
        if current is not None:
            if _MODULE_START.match(code):
                depth += 1
            elif _MODULE_END.match(code):
                depth -= 1
                if depth == 0:
                    spans.append((current[0], current[1], i))
                    current = None
    if current is not None:  # unterminated module — take the rest of the file
        spans.append((current[0], current[1], len(scan_lines) - 1))

    # Header material before the first module (includes, defines, comments).
    if spans and spans[0][1] > 0:
        head = _clean("\n".join(real_lines[: spans[0][1]]))
        if len(head) >= MIN_CHUNK_CHARS:
            chunks.append(
                Chunk(
                    chunk_id=_mk_id(str(path), len(chunks)),
                    text=head,
                    source_path=str(path),
                    kind="rtl",
                    label="(file header)",
                    line_start=1,
                    line_end=spans[0][1],
                )
            )

    if not spans:  # no modules at all — treat the file as one chunk
        body = _clean(text)
        if body:
            chunks.append(
                Chunk(
                    chunk_id=_mk_id(str(path), len(chunks)),
                    text=body,
                    source_path=str(path),
                    kind="rtl",
                    label=path.stem,
                    line_start=1,
                    line_end=len(real_lines),
                )
            )
        return chunks

    for name, s, e in spans:
        body = "\n".join(real_lines[s : e + 1])
        if len(body) <= MAX_CHUNK_CHARS:
            chunks.append(
                Chunk(
                    chunk_id=_mk_id(str(path), len(chunks)),
                    text=_clean(body),
                    source_path=str(path),
                    kind="rtl",
                    label=name,
                    line_start=s + 1,
                    line_end=e + 1,
                )
            )
            continue

        # Large module: split on inner block boundaries, keeping the port list
        # (everything before the first inner block) as its own chunk, since
        # that is what an interface question needs to retrieve.
        cut_points = [
            i
            for i in range(s, e + 1)
            if _INNER_BLOCK.match(scan_lines[i].split("//", 1)[0])
        ]
        bounds = [s] + cut_points + [e + 1]
        bounds = sorted(set(bounds))
        for bi in range(len(bounds) - 1):
            a, b = bounds[bi], bounds[bi + 1]
            piece = _clean("\n".join(real_lines[a:b]))
            if len(piece) < MIN_CHUNK_CHARS:
                continue
            part = "ports/declarations" if bi == 0 else f"body part {bi}"
            if bi > 0 and len(piece) > MAX_CHUNK_CHARS:
                # One always block larger than the limit — in register-file RTL
                # this is the address-decode case statement. Split it on case
                # arms so a question about one register retrieves that register's
                # arm, not a 12k-character block the embedder truncates.
                for sa, sb, arms in _split_case_arms(scan_lines, a, b):
                    sub = _clean("\n".join(real_lines[sa:sb]))
                    if len(sub) < MIN_CHUNK_CHARS:
                        continue
                    what = f": {', '.join(arms)}" if arms else ""
                    chunks.append(
                        Chunk(
                            chunk_id=_mk_id(str(path), len(chunks)),
                            text=sub,
                            source_path=str(path),
                            kind="rtl",
                            label=f"{name} ({part}, lines {sa + 1}-{sb}{what})",
                            line_start=sa + 1,
                            line_end=sb,
                        )
                    )
                continue
            chunks.append(
                Chunk(
                    chunk_id=_mk_id(str(path), len(chunks)),
                    text=piece,
                    source_path=str(path),
                    kind="rtl",
                    label=f"{name} ({part})",
                    line_start=a + 1,
                    line_end=b,
                )
            )
    return chunks


# A case-arm label alone on its line: `REG_FOO:   FOO:   3'b010:   (optionally + begin)
_CASE_ARM = re.compile(r"^\s*(`?\w+|\d+'[bhdo][0-9a-fA-F_xz?]+)\s*:\s*(begin\b.*)?$")
# Statements after the case that should not be glued onto its last arm.
_TAIL_STMT = re.compile(r"^\s*assign\b")
_NOT_ARMS = {"begin", "end", "default", "else"}

RTL_SPLIT_TARGET = 1200  # roughly the embedder's input window for RTL text


def _split_case_arms(
    scan_lines: list[str], a: int, b: int
) -> list[tuple[int, int, list[str]]]:
    """Split lines [a, b) on case-arm boundaries into pieces of ~RTL_SPLIT_TARGET.

    Returns (start, end, arm_names) per piece. An arm is never cut in half,
    and trailing `assign` statements after the case form their own piece
    rather than being glued onto the last arm. One piece if there are no arms.
    """
    # Segment starts: (line, arm name) for arms, (line, None) for tail assigns.
    starts: list[tuple[int, str | None]] = []
    for i in range(a, b):
        code = scan_lines[i].split("//", 1)[0]
        m = _CASE_ARM.match(code)
        if m and m.group(1) not in _NOT_ARMS:
            starts.append((i, m.group(1).lstrip("`")))
        elif _TAIL_STMT.match(code) and (not starts or starts[-1][1] is not None):
            starts.append((i, None))
    if not starts:
        return [(a, b, [])]

    # Segments: the block header (defaults, `case (...)`) then one per start.
    segs = [(a, starts[0][0], "")] + [
        (ln, starts[k + 1][0] if k + 1 < len(starts) else b, arm)
        for k, (ln, arm) in enumerate(starts)
    ]

    size = lambda s, e: sum(len(scan_lines[j]) + 1 for j in range(s, e))
    pieces: list[tuple[int, int, list[str]]] = []
    cur_s, cur_e, cur_arms, cur_tail = a, a, [], False
    for s, e, arm in segs:
        is_tail = arm is None
        switching = cur_e > cur_s and is_tail != cur_tail
        full = cur_e > cur_s and size(cur_s, e) > RTL_SPLIT_TARGET
        if switching or full:
            pieces.append((cur_s, cur_e, cur_arms))
            cur_s, cur_arms = s, []
        cur_e, cur_tail = e, is_tail
        if arm:
            cur_arms.append(arm)
    if cur_e > cur_s:
        pieces.append((cur_s, cur_e, cur_arms))
    return pieces


# --------------------------------------------------------------------------

def chunk_file(path: Path) -> list[Chunk]:
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if not text.strip():
        return []
    if suffix in RTL_EXTENSIONS:
        return chunk_rtl(path, text)
    if suffix in DOC_EXTENSIONS:
        return chunk_doc(path, text)
    return []
