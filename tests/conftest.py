from pathlib import Path

import pytest

from tests.fixtures.synth import SyntheticDoc, build_all, documents


@pytest.fixture(scope="session")
def synthetic_docs() -> dict[str, SyntheticDoc]:
    return documents()


@pytest.fixture(scope="session")
def synthetic_pdfs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return build_all(tmp_path_factory.mktemp("synthetic"))
