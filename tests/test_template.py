from pathlib import Path

import pytest

from brief_digest.template import TemplateError, load_template

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT = REPO_ROOT / "templates" / "default.yaml"

VALID_SECTION = """\
  - id: a
    title: A
    description: Primeira.
    omittable: false
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "t.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_default_template_matches_documented_sections() -> None:
    template = load_template(DEFAULT)
    assert template.ids == (
        "identificacao",
        "tempestividade_preparo",
        "preliminares",
        "merito",
        "pedidos",
        "fundamentos_citados",
    )
    assert template.sections[0].title == "Identificação"
    assert [s.omittable for s in template.sections] == [False, *[True] * 5]


@pytest.mark.parametrize(
    "text",
    [
        "name: x\nsections: []\n",
        "name: x\n",
        "name: x\nsections:\n" + VALID_SECTION + VALID_SECTION,
        "name: x\nextra: 1\nsections:\n" + VALID_SECTION,
        "name: x\nsections:\n" + VALID_SECTION.replace("id: a", "id: Seção A"),
        "name: x\nsections:\n" + VALID_SECTION.replace("omittable: false", "omittable: maybe"),
        "name: x\nsections:\n" + VALID_SECTION.replace("description: Primeira.", "description: ''"),
        "name: x\nsections:\n" + VALID_SECTION + "    prompt: extra key\n",
        "name: x\nsections:\n" + VALID_SECTION.replace("Primeira.", '"linha 1\\nlinha 2"'),
        "name: [unclosed\n",
    ],
)
def test_invalid_templates_are_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(TemplateError):
        load_template(write(tmp_path, text))


def test_missing_template_file_is_a_template_error(tmp_path: Path) -> None:
    with pytest.raises(TemplateError):
        load_template(tmp_path / "missing.yaml")


def test_yaml_tags_are_not_executed(tmp_path: Path) -> None:
    with pytest.raises(TemplateError):
        load_template(write(tmp_path, "name: !!python/object/apply:os.getcwd []\nsections: []\n"))
