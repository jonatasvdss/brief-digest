import json
import logging
from pathlib import Path

import pytest

from brief_digest.extract import ExtractConfig, extract
from brief_digest.summarize import BANNER, SUMMARY_JSON, SUMMARY_MD, Item, render_summary
from brief_digest.template import Section, Template, load_template
from brief_digest.verify import (
    FLAG_MARK,
    NOTICE_PREFIX,
    VERIFY_JSON,
    normalize,
    parse_summary,
    references,
    verify,
)
from tests.fixtures.eval_cases import CASES, Case

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = REPO_ROOT / "templates" / "default.yaml"
TEMPLATE = load_template(TEMPLATE_PATH)
TITLE_TO_ID = {s.title: s.id for s in TEMPLATE.sections}


def write_summary(work: Path, items: list[tuple[str, str, tuple[int, ...]]]) -> None:
    sections: dict[str, list[Item]] = {}
    for title, text, pages in items:
        sections.setdefault(TITLE_TO_ID[title], []).append(Item(text, pages))
    (work / SUMMARY_MD).write_text(render_summary(TEMPLATE, sections), encoding="utf-8")
    (work / SUMMARY_JSON).write_text(json.dumps({"status": "ok"}), encoding="utf-8")


@pytest.fixture(scope="session")
def extractions(
    synthetic_pdfs: dict[str, Path], tessdata: Path, tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Path]:
    """The same filing extracted natively and via OCR."""
    root = tmp_path_factory.mktemp("verify")
    out = {}
    for name in ("appeal_native", "appeal_scanned"):
        out[name] = root / name
        extract(synthetic_pdfs[name], out[name], ExtractConfig(tessdata=tessdata))
    return out


def run_case(case: Case, extraction: Path, tmp_path: Path) -> list[str]:
    work = tmp_path / case.name
    work.mkdir()
    (work / "full.md").write_bytes((extraction / "full.md").read_bytes())
    write_summary(work, [(case.section, case.text, case.pages)])
    verify(work, TEMPLATE_PATH)
    (entry,) = json.loads((work / VERIFY_JSON).read_text(encoding="utf-8"))["by_item"]
    return entry["flags"]


@pytest.mark.parametrize("source", ["appeal_native", "appeal_scanned"])
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_evaluation_set(
    case: Case, source: str, extractions: dict[str, Path], tmp_path: Path
) -> None:
    assert run_case(case, extractions[source], tmp_path) == list(case.expected)


def test_flags_are_shown_inline_and_passing_items_get_no_mark(
    extractions: dict[str, Path], tmp_path: Path
) -> None:
    work = tmp_path / "w"
    work.mkdir()
    (work / "full.md").write_bytes((extractions["appeal_native"] / "full.md").read_bytes())
    write_summary(
        work,
        [
            ("Fundamentos citados", "art. 1.022 do CPC", (3,)),
            ("Fundamentos citados", "REsp 1.111.111/ZZ", (2,)),
        ],
    )
    result = verify(work, TEMPLATE_PATH)
    assert (result.items, result.flagged) == (2, 1)
    lines = (work / SUMMARY_MD).read_text(encoding="utf-8").splitlines()
    assert lines[0] == BANNER
    assert lines[2].startswith(NOTICE_PREFIX) and "1 item(ns)" in lines[2]
    assert "não confirma" in lines[2]
    assert "- art. 1.022 do CPC (p. 3)" in lines
    flagged = next(ln for ln in lines if "1.111.111" in ln)
    assert FLAG_MARK in flagged and "não localizado no texto extraído" in flagged


def test_no_wording_claims_verification_confirms_correctness(
    extractions: dict[str, Path], tmp_path: Path
) -> None:
    work = tmp_path / "w"
    work.mkdir()
    (work / "full.md").write_bytes((extractions["appeal_native"] / "full.md").read_bytes())
    write_summary(work, [("Fundamentos citados", "art. 1.022 do CPC", (3,))])
    verify(work, TEMPLATE_PATH)
    text = (work / SUMMARY_MD).read_text(encoding="utf-8").casefold()
    for claim in ("verificado", "confirmado", "correto ✓", "✓", "validado"):
        assert claim not in text.replace("não confirma", "")
    assert "nenhum item sinalizado" in text


def test_verification_is_idempotent(extractions: dict[str, Path], tmp_path: Path) -> None:
    work = tmp_path / "w"
    work.mkdir()
    (work / "full.md").write_bytes((extractions["appeal_native"] / "full.md").read_bytes())
    write_summary(
        work,
        [("Fundamentos citados", "REsp 1.111.111/ZZ", (2,)), ("Pedidos", "Reforma.", (3,))],
    )
    verify(work, TEMPLATE_PATH)
    first = (work / SUMMARY_MD).read_text(encoding="utf-8")
    verify(work, TEMPLATE_PATH)
    assert (work / SUMMARY_MD).read_text(encoding="utf-8") == first
    assert first.count(NOTICE_PREFIX) == 1 and first.count(FLAG_MARK) == 1


@pytest.mark.parametrize("status", ["rejected", "failed", "incomplete"])
def test_skipped_unless_summary_is_ok(tmp_path: Path, status: str) -> None:
    (tmp_path / SUMMARY_JSON).write_text(json.dumps({"status": status}), encoding="utf-8")
    assert verify(tmp_path, TEMPLATE_PATH).status == "skipped"
    assert not (tmp_path / SUMMARY_MD).exists()


def test_rendered_items_round_trip_through_the_parser() -> None:
    template = Template("t", (Section("a", "Seção A", "x", True),))
    items = [Item("AUTOR FICTÍCIO, Manual, p. 123", (2,)), Item("Texto (com parênteses)", (1, 3))]
    parsed = parse_summary(render_summary(template, {"a": items}))
    assert [(i.section, i.text, i.pages) for i in parsed] == [
        ("Seção A", "AUTOR FICTÍCIO, Manual, p. 123", (2,)),
        ("Seção A", "Texto (com parênteses)", (1, 3)),
    ]


def test_normalize_folds_ocr_and_typography_variants() -> None:
    assert normalize("art. 5º") == normalize("Art. 5°") == normalize("ART.  5o")
    assert normalize("pro-\ncesso") == "processo"
    curly = f"{chr(0x201C)}aspas{chr(0x201D)} {chr(0x2013)} **negrito**"
    assert normalize(curly) == '"aspas" - negrito'


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("art. 1.022 do CPC", {("art", "1022")}),
        ("Art. 5º, LV, da CF", {("art", "5")}),
        ("Súmula 7 do STJ e Súmula Vinculante nº 10", {("sumula", "7"), ("sumula", "10")}),
        ("Lei nº 8.078/90", {("lei", "8078")}),
        ("REsp 9.999.901/ZZ e AgInt no AREsp n. 123", {("resp", "9999901"), ("aresp", "123")}),
        ("Tema 1.234 do STF", {("tema", "1234")}),
        ("na página 12 consta o valor de R$ 123,45", set()),
    ],
)
def test_reference_extraction(text: str, expected: set[tuple[str, str]]) -> None:
    assert references(text) == expected


def test_no_document_text_in_logs_or_report(
    extractions: dict[str, Path], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    work = tmp_path / "w"
    work.mkdir()
    (work / "full.md").write_bytes((extractions["appeal_native"] / "full.md").read_bytes())
    write_summary(work, [("Fundamentos citados", "REsp 1.111.111/ZZ", (2,))])
    verify(work, TEMPLATE_PATH)
    report = (work / VERIFY_JSON).read_text(encoding="utf-8")
    logged = "\n".join(r.getMessage() for r in caplog.records)
    for text in (report, logged):
        assert "1.111.111" not in text and "REsp" not in text
