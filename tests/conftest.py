import os
from pathlib import Path

import pytest

from tests.fixtures.synth import SyntheticDoc, build_all, documents

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def tessdata() -> Path:
    path = Path(os.environ.get("BRIEF_DIGEST_TESSDATA", REPO_ROOT / ".tessdata"))
    if not (path / "por.traineddata").is_file():
        message = "Portuguese tessdata missing; run: uv run python scripts/fetch_tessdata.py"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    return path


@pytest.fixture(scope="session")
def synthetic_docs() -> dict[str, SyntheticDoc]:
    return documents()


@pytest.fixture(scope="session")
def synthetic_pdfs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return build_all(tmp_path_factory.mktemp("synthetic"))
