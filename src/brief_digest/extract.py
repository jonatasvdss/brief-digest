"""Extraction stage: PDF -> full.md with page markers. Deterministic, no LLM.

Native text (via pymupdf4llm) is used when a page has enough of it; otherwise the
page is OCR'd with PyMuPDF's built-in Tesseract. A page whose OCR fails is marked
unreadable and the document incomplete. Logs carry only counts, page numbers,
durations, and error types (hard rule 4).
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import math
import os
import re
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import pymupdf
import pymupdf4llm

log = logging.getLogger(__name__)

FULL_MD = "full.md"
EXTRACT_JSON = "extract.json"
UNREADABLE = "<!-- unreadable page -->"

PageMethod = Literal["native", "ocr", "unreadable"]


@dataclass(frozen=True)
class ExtractConfig:
    min_native_chars: int = 40
    # OCR a page whose images cover at least this share of it, even when it has
    # native text: e-filing systems stamp native signature lines onto scans.
    # None disables the rule.
    ocr_image_coverage: float | None = 0.5
    ocr_language: str = "por"
    ocr_dpi: int = 300
    tessdata: Path | None = None  # None: TESSDATA_PREFIX; never auto-discovered
    edge_lines: int = 2  # lines at each end of a page checked for headers/footers
    repeat_ratio: float = 0.5  # share of pages an edge line must repeat on


@dataclass(frozen=True)
class PageResult:
    page: int
    method: PageMethod
    chars: int
    error_type: str | None = None


@dataclass(frozen=True)
class ExtractResult:
    pages: tuple[PageResult, ...]

    @property
    def complete(self) -> bool:
        return all(p.method != "unreadable" for p in self.pages)


def non_space_chars(text: str) -> int:
    return sum(not c.isspace() for c in text)


def page_marker(page: int) -> str:
    return f"<!-- page {page} -->"


def escape_markers(text: str) -> str:
    """Keep document text from forging page markers or other HTML comments."""
    return text.replace("<!--", "&lt;!--")


PAGE_MARKER = re.compile(r"^<!-- page (\d+) -->$", re.MULTILINE)


def split_pages(full_md: str) -> dict[int, str]:
    """Page number -> page text, from full.md."""
    parts = PAGE_MARKER.split(full_md)
    return {int(n): body.strip() for n, body in zip(parts[1::2], parts[2::2], strict=True)}


_DIGITS = re.compile(r"\d+")


def _normalize(line: str) -> str:
    return _DIGITS.sub("#", " ".join(line.strip().strip("#*_ ").split())).casefold()


def strip_repeated_edges(
    pages: list[str], edge_lines: int = 2, repeat_ratio: float = 0.5
) -> list[str]:
    """Drop header/footer lines: same position from the page top or bottom, same text
    (digits ignored), on at least repeat_ratio of the pages. Zones never overlap, so
    short pages keep their body."""
    if len(pages) < 2:
        return pages
    split: list[tuple[list[str], dict[int, tuple[str, int, str]]]] = []
    counts: Counter[tuple[str, int, str]] = Counter()
    for text in pages:
        lines = text.split("\n")
        filled = [i for i, line in enumerate(lines) if line.strip()]
        k = min(edge_lines, (len(filled) - 1) // 2)
        keys = {i: ("top", n, _normalize(lines[i])) for n, i in enumerate(filled[:k])}
        keys |= {i: ("bottom", n, _normalize(lines[i])) for n, i in enumerate(filled[::-1][:k])}
        split.append((lines, keys))
        counts.update(set(keys.values()))
    needed = max(2, math.ceil(repeat_ratio * len(pages)))
    repeated = {key for key, n in counts.items() if n >= needed}
    return [
        "\n".join(
            line for i, line in enumerate(lines) if not (i in keys and keys[i] in repeated)
        ).strip()
        for lines, keys in split
    ]


class TessdataError(Exception):
    """OCR language data is not configured or not installed."""


def _tessdata_dir(config: ExtractConfig) -> Path:
    """Explicit location only: PyMuPDF's fallback discovery runs shell commands."""
    configured = config.tessdata or os.environ.get("TESSDATA_PREFIX")
    if not configured:
        raise TessdataError
    path = Path(configured)
    if not (path / f"{config.ocr_language}.traineddata").is_file():
        raise TessdataError
    return path


@contextlib.contextmanager
def _quiet() -> Iterator[None]:
    """Swallow library chatter so document-derived text never reaches stdout or logs."""
    pymupdf.set_messages(path=os.devnull)
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        yield


def _ocr(page: pymupdf.Page, config: ExtractConfig) -> str:
    textpage = page.get_textpage_ocr(
        language=config.ocr_language,
        dpi=config.ocr_dpi,
        full=True,
        tessdata=str(_tessdata_dir(config)),
    )
    return page.get_text(textpage=textpage)


def image_coverage(page: pymupdf.Page) -> float:
    """Share of the page area covered by images (overlaps counted twice, capped at 1)."""
    page_area = page.rect.get_area()
    if not page_area:
        return 0.0
    covered = sum(
        pymupdf.Rect(info["bbox"]).intersect(page.rect).get_area() for info in page.get_image_info()
    )
    return min(1.0, covered / page_area)


def _use_native(page: pymupdf.Page, config: ExtractConfig) -> bool:
    if non_space_chars(page.get_text()) < config.min_native_chars:
        return False
    limit = config.ocr_image_coverage
    return limit is None or image_coverage(page) < limit


def _read_pages(pdf_path: Path, config: ExtractConfig) -> tuple[list[PageResult], list[str]]:
    with pymupdf.open(pdf_path) as doc:
        native = [i for i, page in enumerate(doc) if _use_native(page, config)]
        texts: dict[int, str] = {}
        if native:
            chunks = pymupdf4llm.to_markdown(
                doc, pages=native, page_chunks=True, use_ocr=False, show_progress=False
            )
            for chunk in chunks:
                texts[chunk["metadata"]["page_number"] - 1] = chunk["text"]
            if sorted(texts) != native:
                raise RuntimeError("markdown conversion returned unexpected pages")

        methods: list[tuple[PageMethod, str | None]] = []
        for i, page in enumerate(doc):
            if i in texts:
                methods.append(("native", None))
                continue
            try:
                texts[i] = _ocr(page, config)
                methods.append(("ocr", None))
            except Exception as exc:  # any OCR failure makes the page unreadable
                texts[i] = ""
                methods.append(("unreadable", type(exc).__name__))
                log.warning("page %d unreadable error_type=%s", i + 1, type(exc).__name__)

    cleaned = strip_repeated_edges(
        [texts[i] for i in range(len(methods))], config.edge_lines, config.repeat_ratio
    )
    results = [
        PageResult(i + 1, method, non_space_chars(cleaned[i]), error)
        for i, (method, error) in enumerate(methods)
    ]
    return results, cleaned


def render_full_md(results: list[PageResult], texts: list[str]) -> str:
    parts = []
    for result, text in zip(results, texts, strict=True):
        body = UNREADABLE if result.method == "unreadable" else escape_markers(text)
        parts.append(f"{page_marker(result.page)}\n{body}".rstrip() + "\n")
    return "\n".join(parts)


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def extract(pdf_path: Path, out_dir: Path, config: ExtractConfig | None = None) -> ExtractResult:
    """Write full.md and extract.json (page statuses only, no content) to out_dir."""
    config = config or ExtractConfig()
    start = time.monotonic()
    with _quiet():
        results, texts = _read_pages(pdf_path, config)

    out_dir.mkdir(parents=True, exist_ok=True)
    result = ExtractResult(tuple(results))
    _write_atomic(out_dir / FULL_MD, render_full_md(results, texts))
    status = {"complete": result.complete, "pages": [asdict(r) for r in results]}
    _write_atomic(out_dir / EXTRACT_JSON, json.dumps(status, indent=2) + "\n")

    by_method = Counter(r.method for r in results)
    log.info(
        "extracted pages=%d native=%d ocr=%d unreadable=%d duration_ms=%d",
        len(results),
        by_method["native"],
        by_method["ocr"],
        by_method["unreadable"],
        (time.monotonic() - start) * 1000,
    )
    return result
