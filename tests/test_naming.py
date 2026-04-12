from pathlib import Path
import pytest
from dvd_ripper.naming import sanitize, movie_path, tv_path


# ── sanitize ──────────────────────────────────────────────────────────────────

def test_sanitize_plain_name():
    assert sanitize("Breaking Bad") == "Breaking Bad"

def test_sanitize_removes_colon():
    assert sanitize("The Dark Knight: Rises") == "The Dark Knight Rises"

def test_sanitize_removes_all_forbidden_chars():
    assert sanitize(r'/\:*?"<>|') == ""

def test_sanitize_strips_leading_trailing_whitespace():
    assert sanitize("  Name  ") == "Name"

def test_sanitize_leaves_numbers_and_hyphens():
    assert sanitize("S.W.A.T - Season 1") == "S.W.A.T - Season 1"


# ── movie_path ────────────────────────────────────────────────────────────────

def test_movie_path_structure():
    p = movie_path("/media/Movies", "Inception", "mp4")
    assert p == Path("/media/Movies/Inception/Inception.mp4")

def test_movie_path_default_format_is_mp4():
    p = movie_path("/media/Movies", "Inception")
    assert p.suffix == ".mp4"

def test_movie_path_sanitizes_name():
    p = movie_path("/media/Movies", "The Dark Knight: Rises", "mp4")
    assert p == Path("/media/Movies/The Dark Knight Rises/The Dark Knight Rises.mp4")

def test_movie_path_dir_is_parent_of_file():
    p = movie_path("/media/Movies", "Inception", "mp4")
    assert p.parent == Path("/media/Movies/Inception")


# ── tv_path ───────────────────────────────────────────────────────────────────

def test_tv_path_structure():
    p = tv_path("/media/TV", "Breaking Bad", 1, 5, "mp4")
    assert p == Path("/media/TV/Breaking Bad/Season 01/Breaking Bad - S01E05.mp4")

def test_tv_path_zero_padded_season():
    p = tv_path("/media/TV", "Show", 4, 1, "mp4")
    assert "Season 04" in str(p)
    assert "S04E01" in p.name

def test_tv_path_specials_season_zero():
    p = tv_path("/media/TV", "Show", 0, 1, "mp4")
    assert "Season 00" in str(p)
    assert "S00E01" in p.name

def test_tv_path_sanitizes_show_name():
    p = tv_path("/media/TV", "Show: Name", 1, 1, "mp4")
    assert "Show Name" in str(p)
    assert "Show: Name" not in str(p)

def test_tv_path_default_format_is_mp4():
    p = tv_path("/media/TV", "Show", 1, 1)
    assert p.suffix == ".mp4"

def test_tv_path_episode_zero_padding():
    p = tv_path("/media/TV", "Show", 1, 9, "mp4")
    assert "S01E09" in p.name
