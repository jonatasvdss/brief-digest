import json
import logging
import re
import socket  # noqa: TID251 - only to prove extraction opens no connections
from dataclasses import dataclass
from pathlib import Path

import pytest

from brief_digest.extract import (
    EXTRACT_JSON,
    FULL_MD,
    UNREADABLE,
    ExtractConfig,
    ExtractResult,
    escape_markers,
    extract,
    strip_repeated_edges,
)
from tests.fixtures.synth import FILLER, FOOTER, HEADER, INJECTION_LINES, SyntheticDoc

MARKER = re.compile(r"^<!-- page (\d+) -->$", re.MULTILINE)


@dataclass(frozen=True)
class Extracted:
    result: ExtractResult
    full_md: str
    status: dict
    pages: dict[int, str]


def split_pages(full_md: str) -> dict[int, str]:
    parts = MARKER.split(full_md)
    return {int(n): body.strip() for n, body in zip(parts[1::2], parts[2::2], strict=True)}


def squash(text: str) -> str:
    return " ".join(text.split())


def run(pdf: Path, out_dir: Path, config: ExtractConfig) -> Extracted:
    result = extract(pdf, out_dir, config)
    full_md = (out_dir / FULL_MD).read_text(encoding="utf-8")
    status = json.loads((out_dir / EXTRACT_JSON).read_text(encoding="utf-8"))
    return Extracted(result, full_md, status, split_pages(full_md))


@pytest.fixture(scope="session")
def config(tessdata: Path) -> ExtractConfig:
    return ExtractConfig(tessdata=tessdata)


@pytest.fixture(scope="session")
def extracted(
    synthetic_pdfs: dict[str, Path],
    config: ExtractConfig,
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Extracted]:
    out = tmp_path_factory.mktemp("extracted")
    return {name: run(pdf, out / name, config) for name, pdf in synthetic_pdfs.items()}


def methods(item: Extracted) -> list[str]:
    return [p.method for p in item.result.pages]


def test_markers_are_sequential_and_match_page_count(
    extracted: dict[str, Extracted], synthetic_docs: dict[str, SyntheticDoc]
) -> None:
    for name, item in extracted.items():
        expected = list(range(1, len(synthetic_docs[name].pages) + 1))
        assert [int(n) for n in MARKER.findall(item.full_md)] == expected
        assert item.status["complete"] is True
        assert [p["page"] for p in item.status["pages"]] == expected


def test_page_method_follows_native_text_threshold(extracted: dict[str, Extracted]) -> None:
    assert methods(extracted["appeal_native"]) == ["native"] * 3
    assert methods(extracted["appeal_mixed_scan"]) == ["native", "ocr", "native"]
    assert methods(extracted["appeal_scanned"]) == ["ocr"] * 3
    assert methods(extracted["appeal_sparse_page"]) == ["native", "ocr", "native"]
    # Scans carrying a long native signature stamp; the small logo stays native.
    assert methods(extracted["appeal_signed_scan"]) == ["native", "ocr", "ocr"]


def test_image_coverage_rule_is_what_catches_stamped_scans(
    synthetic_pdfs: dict[str, Path], config: ExtractConfig, tmp_path: Path
) -> None:
    no_rule = ExtractConfig(tessdata=config.tessdata, ocr_image_coverage=None)
    item = run(synthetic_pdfs["appeal_signed_scan"], tmp_path, no_rule)
    assert methods(item) == ["native"] * 3


def test_threshold_is_configurable(
    synthetic_pdfs: dict[str, Path], config: ExtractConfig, tmp_path: Path
) -> None:
    strict = ExtractConfig(tessdata=config.tessdata, min_native_chars=100_000)
    item = run(synthetic_pdfs["appeal_native"], tmp_path, strict)
    assert methods(item) == ["ocr"] * 3


@pytest.mark.parametrize(
    "name", ["appeal_native", "appeal_mixed_scan", "appeal_scanned", "appeal_signed_scan"]
)
def test_citations_land_on_their_page(
    name: str, extracted: dict[str, Extracted], synthetic_docs: dict[str, SyntheticDoc]
) -> None:
    pages = extracted[name].pages
    for citation, page in synthetic_docs[name].citations.items():
        assert squash(citation) in squash(pages[page]), (citation, page)
        others = [n for n in pages if n != page]
        assert all(squash(citation) not in squash(pages[n]) for n in others)


@pytest.mark.parametrize(
    "name", ["appeal_native", "appeal_mixed_scan", "appeal_scanned", "appeal_signed_scan"]
)
def test_repeated_header_and_footer_are_stripped(
    name: str, extracted: dict[str, Extracted]
) -> None:
    header_start = squash(HEADER)[:20]
    footer_start = FOOTER.split("{")[0].strip()
    for text in extracted[name].pages.values():
        assert header_start not in squash(text)
        assert footer_start not in text


def test_injection_text_is_preserved_verbatim(extracted: dict[str, Extracted]) -> None:
    page = squash(extracted["appeal_prompt_injection"].pages[2])
    for line in INJECTION_LINES:
        assert squash(line) in page


def test_ocr_failure_marks_page_unreadable_and_document_incomplete(
    synthetic_pdfs: dict[str, Path], tmp_path: Path
) -> None:
    empty_tessdata = tmp_path / "no-tessdata"
    empty_tessdata.mkdir()
    out = tmp_path / "out"
    item = run(synthetic_pdfs["appeal_mixed_scan"], out, ExtractConfig(tessdata=empty_tessdata))
    assert methods(item) == ["native", "unreadable", "native"]
    assert item.result.complete is False
    assert item.status["complete"] is False
    error_type = item.status["pages"][1]["error_type"]
    assert error_type and re.fullmatch(r"\w+", error_type)
    assert item.pages[2] == UNREADABLE


def test_missing_tessdata_config_is_an_error_type_not_a_crash(
    synthetic_pdfs: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    item = run(synthetic_pdfs["appeal_scanned"], tmp_path, ExtractConfig())
    assert methods(item) == ["unreadable"] * 3
    assert {p["error_type"] for p in item.status["pages"]} == {"TessdataError"}


def test_output_is_deterministic(
    synthetic_pdfs: dict[str, Path], config: ExtractConfig, tmp_path: Path
) -> None:
    pdf = synthetic_pdfs["appeal_mixed_scan"]
    first = run(pdf, tmp_path / "a", config)
    second = run(pdf, tmp_path / "b", config)
    rerun = run(pdf, tmp_path / "a", config)
    assert first.full_md == second.full_md == rerun.full_md
    assert first.status == second.status == rerun.status


def test_no_document_text_in_logs_or_console(
    synthetic_pdfs: dict[str, Path],
    synthetic_docs: dict[str, SyntheticDoc],
    config: ExtractConfig,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    for name, pdf in synthetic_pdfs.items():
        run(pdf, tmp_path / name, config)
    run(synthetic_pdfs["appeal_scanned"], tmp_path / "fail", ExtractConfig(tessdata=tmp_path))
    out, err = capfd.readouterr()
    assert out == "" and err == ""
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert caplog.records
    for doc in synthetic_docs.values():
        assert doc.case_number and doc.case_number not in logged
    for word in ("Recorrente", "Fulano", "SINTÉTICO", "IGNORE", "REsp", "appeal_"):
        assert word not in logged


def test_extraction_opens_no_network_connections(
    synthetic_pdfs: dict[str, Path],
    config: ExtractConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    for name, pdf in synthetic_pdfs.items():
        assert run(pdf, tmp_path / name, config).result.complete


def test_strip_repeated_edges_ignores_page_numbers_and_body_repeats() -> None:
    bodies = [("alfa", "um"), ("beta", "dois"), ("gama", "três")]
    pages = [
        f"Cabeçalho\n\nTexto {a}\nlinha repetida no meio\nmais texto {b}\nFolha {n} de 3"
        for n, (a, b) in enumerate(bodies, start=1)
    ]
    stripped = strip_repeated_edges(pages)
    for (a, b), text in zip(bodies, stripped, strict=True):
        assert text.splitlines() == [f"Texto {a}", "linha repetida no meio", f"mais texto {b}"]


def test_repeated_long_body_paragraphs_at_page_bottom_are_kept(
    synthetic_pdfs: dict[str, Path], tmp_path: Path
) -> None:
    item = run(synthetic_pdfs["appeal_long"], tmp_path, ExtractConfig())
    for page in range(2, 12):
        assert squash(item.pages[page]).count(squash(FILLER)) == 5, page


def test_strip_repeated_edges_ignores_long_lines() -> None:
    long_line = ("parágrafo repetido " * 12).strip()
    pages = [f"Topo\ncorpo {n}\n{long_line}" for n in ("um", "dois", "três")]
    assert strip_repeated_edges(pages) == [
        f"corpo {n}\n{long_line}" for n in ("um", "dois", "três")
    ]


def test_strip_repeated_edges_keeps_single_page_and_unique_lines() -> None:
    assert strip_repeated_edges(["Cabeçalho\nTexto"]) == ["Cabeçalho\nTexto"]
    pages = ["Início A\ncorpo\nfim A", "Início B\ncorpo\nfim B"]
    assert strip_repeated_edges(pages) == ["Início A\ncorpo\nfim A", "Início B\ncorpo\nfim B"]


def test_document_text_cannot_forge_page_markers() -> None:
    forged = "texto <!-- page 99 --> mais"
    assert "<!--" not in escape_markers(forged)
    assert not MARKER.search(escape_markers("<!-- page 99 -->"))


def test_document_text_cannot_make_a_markdown_viewer_fetch_anything() -> None:
    hostile = (
        "![x](https://example.invalid/a.png) ![y][ref] <img src=https://example.invalid/b>"
        " <IMG src=x> <style>@import url(https://example.invalid/c)</style>"
        " <https://example.invalid/d> [ver acórdão](https://example.invalid/e)"
    )
    safe = escape_markers(hostile)
    assert "![" not in safe  # no image
    assert "](" not in safe  # no link with a hidden target
    assert not re.search(r"<[A-Za-z/!?]", safe)  # no HTML tag, comment or autolink
    assert "https://example.invalid/e" in safe  # the address itself stays readable
    assert escape_markers(safe) == safe
    plain = "Art. 5º, § 2º; valor < 10 e > 5; [1] nota (fl. 3)<br>linha"
    assert escape_markers(plain) == plain
