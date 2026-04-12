import pytest
from dvd_ripper.config import Config

# ── Shared title data ──────────────────────────────────────────────────────────

MOVIE_TITLES = [
    {"number": 1, "duration": (2, 32,  4), "resolution": "1920x1080"},
    {"number": 2, "duration": (0,  5, 12), "resolution": "1920x1080"},
    {"number": 3, "duration": (0,  2, 44), "resolution": "1920x1080"},
]

TV_TITLES = [
    {"number": 1, "duration": (0, 47, 12), "resolution": "853x480"},
    {"number": 2, "duration": (0, 48,  3), "resolution": "853x480"},
    {"number": 3, "duration": (0, 47, 55), "resolution": "853x480"},
    {"number": 4, "duration": (0, 49,  1), "resolution": "853x480"},
]

# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def cfg():
    return Config.test_default()


@pytest.fixture
def fake_inputs(monkeypatch):
    """Queue a sequence of strings to be returned by successive input() calls."""
    def _set(*responses):
        it = iter(responses)
        monkeypatch.setattr("builtins.input", lambda _="": next(it))
    return _set
