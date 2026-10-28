import random
import re
from pathlib import Path

from reportlab.pdfbase.ttfonts import TTFont

from tests.fixtures.synth import (
    SCAN_FONT,
    SyntheticDoc,
    build_all,
    cnj_check_digits,
    fake_case_number,
)

PAGE_OBJECT = re.compile(rb"/Type /Page\b(?!s)")
CNJ_SHAPE = re.compile(r"(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})")


def test_every_document_is_a_pdf_with_expected_page_count(
    synthetic_docs: dict[str, SyntheticDoc], synthetic_pdfs: dict[str, Path]
) -> None:
    assert synthetic_docs.keys() == synthetic_pdfs.keys()
    for name, path in synthetic_pdfs.items():
        data = path.read_bytes()
        assert data.startswith(b"%PDF-")
        assert len(PAGE_OBJECT.findall(data)) == len(synthetic_docs[name].pages)


def test_scanned_pages_have_no_native_text(synthetic_pdfs: dict[str, Path]) -> None:
    native = synthetic_pdfs["appeal_native"].read_bytes()
    mixed = synthetic_pdfs["appeal_mixed_scan"].read_bytes()
    scanned = synthetic_pdfs["appeal_scanned"].read_bytes()
    page1_marker, page2_marker = b"Recorrente", b"deixou de cumprir"
    assert page1_marker in native and page2_marker in native
    assert page1_marker in mixed and page2_marker not in mixed
    assert page1_marker not in scanned
    assert b"/Subtype /Image" in scanned


def test_scan_font_covers_every_character_used(
    synthetic_docs: dict[str, SyntheticDoc],
) -> None:
    font = TTFont("scan", str(SCAN_FONT))
    used = {
        char
        for doc in synthetic_docs.values()
        for text in (doc.header or "", doc.footer or "", *(ln for p in doc.pages for ln in p.lines))
        for char in text
    }
    assert {c for c in used if ord(c) not in font.face.charToGlyph} == set()


def test_output_is_deterministic(tmp_path: Path) -> None:
    first = build_all(tmp_path / "a", seed=7)
    second = build_all(tmp_path / "b", seed=7)
    for name in first:
        assert first[name].read_bytes() == second[name].read_bytes()


def test_fake_case_numbers_never_have_valid_check_digits() -> None:
    rng = random.Random(0)
    for _ in range(1000):
        match = CNJ_SHAPE.fullmatch(fake_case_number(rng))
        assert match
        number, digits, year, segment, tribunal, origin = map(int, match.groups())
        assert digits != cnj_check_digits(number, year, segment, tribunal, origin)


def test_ground_truth_metadata(synthetic_docs: dict[str, SyntheticDoc]) -> None:
    for doc in synthetic_docs.values():
        for citation, page in doc.citations.items():
            assert citation in "\n".join(doc.pages[page - 1].lines)
    sparse = synthetic_docs["appeal_sparse_page"].pages[1]
    assert sum(not c.isspace() for c in "".join(sparse.lines)) < 40
    assert synthetic_docs["appeal_prompt_injection"].injection_pages == (2,)
