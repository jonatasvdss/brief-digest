"""Evaluation set for the verifier: summaries with planted fake citations.

Each case is a summary item for a synthetic document plus the flags the verifier
must raise. Cases run on native and OCR'd extractions of the same filing, so OCR
noise that causes false flags shows up as a failing case.
"""

from __future__ import annotations

from dataclasses import dataclass

from tests.fixtures.synth import (
    CIT_CF_5_LV,
    CIT_CPC_1022,
    CIT_FAKE_DOCTRINE,
    CIT_FAKE_RESP,
)

FUNDAMENTOS = "Fundamentos citados"
MERITO = "Mérito"
PEDIDOS = "Pedidos"


@dataclass(frozen=True)
class Case:
    name: str
    section: str
    text: str
    pages: tuple[int, ...]
    expected: tuple[str, ...]  # flags the verifier must raise


CASES = (
    # Planted citations, correctly transcribed and paged: no flag.
    Case("cf_correct", FUNDAMENTOS, CIT_CF_5_LV, (2,), ()),
    Case("resp_correct", FUNDAMENTOS, CIT_FAKE_RESP, (2,), ()),
    Case("doctrine_correct", FUNDAMENTOS, CIT_FAKE_DOCTRINE, (2,), ()),
    Case("cpc_correct", FUNDAMENTOS, CIT_CPC_1022, (3,), ()),
    Case("cpc_with_extra_pages", FUNDAMENTOS, CIT_CPC_1022, (2, 3), ()),
    Case("trailing_period_is_tolerated", FUNDAMENTOS, CIT_CPC_1022 + ".", (3,), ()),
    # Wrong page.
    Case("cpc_wrong_page", FUNDAMENTOS, CIT_CPC_1022, (2,), ("not_on_cited_page",)),
    # Invented citations.
    Case("invented_precedent", FUNDAMENTOS, "REsp 1.111.111/ZZ", (2,), ("not_found",)),
    Case(
        "invented_article",
        FUNDAMENTOS,
        "art. 489, § 1º, do CPC",
        (2,),
        ("not_found",),
    ),
    # Page that does not exist.
    Case("page_out_of_document", FUNDAMENTOS, CIT_CPC_1022, (99,), ("invalid_page",)),
    # Paraphrased sections: only legal references inside them are checked.
    Case("paraphrase_without_references", MERITO, "Atraso na entrega contratual.", (2,), ()),
    Case(
        "paraphrase_real_reference",
        MERITO,
        "Alega cerceamento de defesa com base no art. 5º, LV, da CF.",
        (2,),
        (),
    ),
    Case(
        "paraphrase_invented_reference",
        MERITO,
        "Invoca a Súmula 7 do STJ para afastar o reexame de provas.",
        (2,),
        ("reference_not_found",),
    ),
    Case(
        "paraphrase_reference_wrong_page",
        PEDIDOS,
        "Prequestionamento nos termos do art. 1.022 do CPC.",
        (1,),
        ("reference_not_on_cited_page",),
    ),
)
