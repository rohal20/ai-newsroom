from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def rss_bytes() -> bytes:
    return (FIXTURES / "rss_sample.xml").read_bytes()


@pytest.fixture
def atom_bytes() -> bytes:
    return (FIXTURES / "atom_sample.xml").read_bytes()
