"""Verification stage: consistency checks of summary.md against full.md.

Checks every item's page references against the page markers, every item of a
verbatim section against the text of its cited pages, and every legal reference
(article, law, súmula, precedent, tema) inside any item against its cited pages.

A passing check only means the text was found in the extraction. It does not
establish legal accuracy, correct attribution, or extraction fidelity, so passing
items get no mark; failing items are flagged in summary.md for human review.
Logs and verify.json carry counts and flag types only (hard rule 4).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from brief_digest.extract import FULL_MD, split_pages
from brief_digest.summarize import SUMMARY_JSON, SUMMARY_MD
from brief_digest.template import load_template

log = logging.getLogger(__name__)

VERIFY_JSON = "verify.json"

Flag = Literal[
    "invalid_page",
    "not_found",
    "not_on_cited_page",
    "reference_not_found",
    "reference_not_on_cited_page",
]
FLAG_ORDER: tuple[Flag, ...] = (
    "invalid_page",
    "not_found",
    "reference_not_found",
    "not_on_cited_page",
    "reference_not_on_cited_page",
)
FLAG_TEXT: dict[Flag, str] = {
    "invalid_page": "página citada não existe no texto extraído",
    "not_found": "trecho não localizado no texto extraído",
    "not_on_cited_page": "trecho não localizado na(s) página(s) citada(s)",
    "reference_not_found": "referência legal não localizada no texto extraído",
    "reference_not_on_cited_page": "referência legal não localizada na(s) página(s) citada(s)",
}
FLAG_MARK = " ⚠ Conferir:"
NOTICE_PREFIX = "> Verificação automática:"

_ITEM = re.compile(
    r"^- (?P<text>.+) \((?P<label>pp?\.) (?P<pages>\d+(?:, \d+)*)\)(?:"
    + re.escape(FLAG_MARK)
    + r".*)?$"
)
_SECTION = re.compile(r"^## (?P<title>.+)$")

_NUMBER = r"\d+(?:\.\d{3})*"
_NO = r"(?:n[o.]*\s*)?"
_REFERENCE = re.compile(
    rf"\b(?P<art>art(?:igo)?s?\.?)\s*(?P<art_n>{_NUMBER})"
    rf"|\b(?P<sumula>s[uú]mula)(?:\s+vinculante)?\s*{_NO}(?P<sumula_n>{_NUMBER})"
    rf"|\b(?P<lei>lei)\s*(?:complementar\s*)?{_NO}(?P<lei_n>{_NUMBER})"
    rf"|\b(?P<prec>resp|aresp|agrg|agint|edcl|rhc|hc|rms|are|re|adi|adc|adpf|rcl)"
    rf"\s*{_NO}(?P<prec_n>{_NUMBER})"
    rf"|\b(?P<tema>tema)\s*{_NO}(?P<tema_n>{_NUMBER})"
)


# Degree sign, curly quotes, en and em dashes, by code point.
_TYPOGRAPHY = str.maketrans(
    {0xB0: "o", 0x201C: '"', 0x201D: '"', 0x2018: "'", 0x2019: "'", 0x2013: "-", 0x2014: "-"}
)


def normalize(text: str) -> str:
    """Fold the differences between native text, OCR, and model transcription."""
    text = unicodedata.normalize("NFKC", text)  # also folds º and ª into o and a
    text = re.sub(r"-\n(?=\w)", "", text)  # words hyphenated across lines
    text = text.translate(_TYPOGRAPHY)
    text = re.sub(r"[*_#]", "", text)  # markdown emphasis and headings
    return " ".join(text.split()).casefold()


def references(text: str) -> set[tuple[str, str]]:
    """Legal references as (kind, number without separators), e.g. ("art", "1022")."""
    found = set()
    for match in _REFERENCE.finditer(normalize(text)):
        kind = next(k for k in ("art", "sumula", "lei", "prec", "tema") if match.group(k))
        label = "art" if kind == "art" else match.group(kind).replace("ú", "u")
        found.add((label, match.group(f"{kind}_n").replace(".", "")))
    return found


@dataclass(frozen=True)
class SummaryItem:
    section: str
    text: str
    pages: tuple[int, ...]


def parse_summary(summary_md: str) -> list[SummaryItem]:
    items = []
    section = ""
    for line in summary_md.splitlines():
        if heading := _SECTION.match(line):
            section = heading["title"]
        elif item := _ITEM.match(line):
            pages = tuple(int(p) for p in item["pages"].split(", "))
            items.append(SummaryItem(section, item["text"], pages))
    return items


def check_item(item: SummaryItem, pages: dict[int, str], verbatim: bool) -> list[Flag]:
    if not set(item.pages) <= set(pages):
        return ["invalid_page"]
    cited = normalize("\n".join(pages[p] for p in item.pages))
    whole = normalize("\n".join(pages[p] for p in sorted(pages)))
    if verbatim:  # the whole transcription must be there; it covers its references
        needle = normalize(item.text).rstrip(".;, ")
        if needle in cited:
            return []
        return ["not_on_cited_page" if needle in whole else "not_found"]
    refs = references(item.text)
    if not refs or refs <= references(cited):
        return []
    return ["reference_not_on_cited_page" if refs <= references(whole) else "reference_not_found"]


def _strip_previous_run(summary_md: str) -> list[str]:
    lines = []
    for line in summary_md.splitlines():
        if line.startswith(NOTICE_PREFIX):
            continue
        lines.append(line.split(FLAG_MARK, 1)[0] if _ITEM.match(line) else line)
    while len(lines) > 2 and lines[1] == "" and lines[2] == "":
        del lines[1]
    return lines


def _notice(flagged: int) -> str:
    if flagged:
        head = f"{flagged} item(ns) sinalizado(s) com ⚠ para conferência."
    else:
        head = "nenhum item sinalizado."
    return (
        f"{NOTICE_PREFIX} {head} Itens sem sinal apenas foram encontrados no texto "
        "extraído; isso não confirma que o resumo esteja correto."
    )


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


@dataclass(frozen=True)
class VerifyResult:
    status: Literal["ok", "skipped"]
    items: int = 0
    flagged: int = 0


def verify(work_dir: Path, template_path: Path) -> VerifyResult:
    """Flag summary.md items that fail the checks; write counts to verify.json."""
    summary_status = json.loads((work_dir / SUMMARY_JSON).read_text(encoding="utf-8"))
    if summary_status["status"] != "ok":
        _write_atomic(work_dir / VERIFY_JSON, json.dumps({"status": "skipped"}) + "\n")
        log.info("verification skipped summary_status=%s", summary_status["status"])
        return VerifyResult("skipped")

    verbatim_titles = {s.title for s in load_template(template_path).sections if s.verbatim}
    pages = split_pages((work_dir / FULL_MD).read_text(encoding="utf-8"))
    lines = _strip_previous_run((work_dir / SUMMARY_MD).read_text(encoding="utf-8"))

    counts: Counter[str] = Counter()
    per_item = []
    out = []
    section = ""
    for line in lines:
        if heading := _SECTION.match(line):
            section = heading["title"]
        elif match := _ITEM.match(line):
            item = SummaryItem(
                section, match["text"], tuple(int(p) for p in match["pages"].split(", "))
            )
            flags = check_item(item, pages, section in verbatim_titles)
            per_item.append({"section": section, "flags": flags})
            counts.update(flags)
            if flags:
                line += f"{FLAG_MARK} " + "; ".join(FLAG_TEXT[f] for f in flags) + "."
        out.append(line)

    flagged = sum(1 for entry in per_item if entry["flags"])
    out[1:1] = ["", _notice(flagged)]
    _write_atomic(work_dir / SUMMARY_MD, "\n".join(out).rstrip("\n") + "\n")
    report = {
        "status": "ok",
        "items": len(per_item),
        "flagged": flagged,
        "flags": dict(sorted(counts.items())),
        "by_item": per_item,
    }
    _write_atomic(work_dir / VERIFY_JSON, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    log.info(
        "verified items=%d flagged=%d %s",
        len(per_item),
        flagged,
        " ".join(f"{k}={v}" for k, v in sorted(counts.items())),
    )
    return VerifyResult("ok", len(per_item), flagged)
