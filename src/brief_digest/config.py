"""Application config: one TOML file, validated strictly.

Everything the app creates lives under data_dir, in fixed places: the inbox the
user drops PDFs into, one output folder per document, and a hidden state folder
(database, logs). Retention only ever deletes inside the output folder.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from brief_digest.extract import ExtractConfig
from brief_digest.ollama import OllamaConfig, check_local_model_name, check_loopback

INBOX_NAME = "Entrada"
OUTPUT_NAME = "Resumos"
STATE_NAME = ".state"


class ConfigError(Exception):
    """The config file is missing, unreadable, or invalid. The message names keys only."""


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    template: Path
    tessdata: Path
    model: str = "gemma4:12b"
    ollama_host: str = "127.0.0.1"
    ollama_port: int = 11434
    poll_seconds: float = 5.0  # a PDF is picked up once unchanged between two scans
    max_attempts: int = 3  # per document, counting attempts cut short by a crash
    retry_minutes: float = 10.0
    min_native_chars: int = 40
    ocr_image_coverage: float = 0.5

    @property
    def inbox(self) -> Path:
        return self.data_dir / INBOX_NAME

    @property
    def output(self) -> Path:
        return self.data_dir / OUTPUT_NAME

    @property
    def state_dir(self) -> Path:
        return self.data_dir / STATE_NAME

    @property
    def db(self) -> Path:
        return self.state_dir / "state.db"

    @property
    def log_dir(self) -> Path:
        return self.state_dir / "logs"

    def extract_config(self) -> ExtractConfig:
        return ExtractConfig(
            min_native_chars=self.min_native_chars,
            ocr_image_coverage=self.ocr_image_coverage,
            tessdata=self.tessdata,
        )

    def ollama_config(self) -> OllamaConfig:
        return OllamaConfig(model=self.model, host=self.ollama_host, port=self.ollama_port)


_PATHS = ("data_dir", "template", "tessdata")
_TYPES: dict[str, tuple[type, ...]] = {
    "model": (str,),
    "ollama_host": (str,),
    "ollama_port": (int,),
    "poll_seconds": (int, float),
    "max_attempts": (int,),
    "retry_minutes": (int, float),
    "min_native_chars": (int,),
    "ocr_image_coverage": (int, float),
}


def parse_config(data: dict[str, object]) -> AppConfig:
    unknown = set(data) - set(_PATHS) - set(_TYPES)
    if unknown:
        raise ConfigError(f"unknown keys: {', '.join(sorted(unknown))}")
    values: dict[str, object] = {}
    for key in _PATHS:
        raw = data.get(key)
        if not isinstance(raw, str) or not raw:
            raise ConfigError(f"{key}: required, must be a path")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise ConfigError(f"{key}: must be an absolute path")
        values[key] = path
    for key, types in _TYPES.items():
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, types):
            raise ConfigError(f"{key}: wrong type")
        values[key] = value
    config = AppConfig(**values)  # type: ignore[arg-type]
    try:
        check_loopback(config.ollama_host)
        check_local_model_name(config.model)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    if not 0 < config.ollama_port < 65536:
        raise ConfigError("ollama_port: out of range")
    if config.poll_seconds <= 0 or config.retry_minutes < 0 or config.max_attempts < 1:
        raise ConfigError("poll_seconds, retry_minutes, max_attempts: out of range")
    if config.min_native_chars < 0 or not 0 < config.ocr_image_coverage <= 1:
        raise ConfigError("min_native_chars, ocr_image_coverage: out of range")
    return config


def load_config(path: Path) -> AppConfig:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise ConfigError("config file cannot be read") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        raise ConfigError("config file is not valid TOML") from None
    return parse_config(data)
