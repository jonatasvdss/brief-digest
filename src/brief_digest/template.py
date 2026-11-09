"""Summary templates: declarative YAML files listing the output sections in order."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_ID = re.compile(r"[a-z][a-z0-9_]*")
_SECTION_KEYS = {"id", "title", "description", "omittable"}


class TemplateError(ValueError):
    """The template file is missing, malformed, or inconsistent."""


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    description: str
    omittable: bool


@dataclass(frozen=True)
class Template:
    name: str
    sections: tuple[Section, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.sections)


def _section(raw: object, index: int) -> Section:
    if not isinstance(raw, dict):
        raise TemplateError(f"section {index} is not a mapping")
    if set(raw) != _SECTION_KEYS:
        raise TemplateError(f"section {index} must have exactly the keys {sorted(_SECTION_KEYS)}")
    sid, title, description, omittable = (
        raw[k] for k in ("id", "title", "description", "omittable")
    )
    if not (isinstance(sid, str) and _ID.fullmatch(sid)):
        raise TemplateError(f"section {index}: id must be lowercase ASCII (a-z, 0-9, _)")
    for key, value in (("title", title), ("description", description)):
        if not (isinstance(value, str) and value.strip()):
            raise TemplateError(f"section {index}: {key} must be a non-empty string")
    if "\n" in description.strip():
        raise TemplateError(f"section {index}: description must be one line")
    if not isinstance(omittable, bool):
        raise TemplateError(f"section {index}: omittable must be true or false")
    return Section(sid, title.strip(), description.strip(), omittable)


def load_template(path: Path) -> Template:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TemplateError(f"cannot read template: {type(exc).__name__}") from exc
    if not isinstance(raw, dict) or set(raw) != {"name", "sections"}:
        raise TemplateError("template must have exactly the keys 'name' and 'sections'")
    if not (isinstance(raw["name"], str) and raw["name"].strip()):
        raise TemplateError("template name must be a non-empty string")
    if not (isinstance(raw["sections"], list) and raw["sections"]):
        raise TemplateError("template needs at least one section")
    sections = tuple(_section(s, i) for i, s in enumerate(raw["sections"], start=1))
    ids = [s.id for s in sections]
    if len(set(ids)) != len(ids):
        raise TemplateError("section ids must be unique")
    return Template(raw["name"].strip(), sections)
