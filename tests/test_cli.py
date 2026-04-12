"""
Tests for cli.py — prompt helpers, pickers, and full UX flows.

Flow tests use test_mode=True (injects _TEST_SHOWS/_TEST_SEASONS, skips real
hardware) and patch _scan / _encode so tests are fast and deterministic.
"""

from unittest.mock import patch, MagicMock
import pytest

from dvd_ripper import cli
from dvd_ripper.cli import (
    prompt, prompt_choice, prompt_int,
    _pick_show, _pick_season, _pick_movie_title, _pick_episode_titles,
    run_movie, run_tv,
    _TEST_SHOWS, _TEST_SEASONS,
)
from .conftest import MOVIE_TITLES, TV_TITLES


# ── prompt ────────────────────────────────────────────────────────────────────

def test_prompt_returns_entered_value(fake_inputs):
    fake_inputs("Inception")
    assert prompt("Movie name") == "Inception"

def test_prompt_uses_default_on_empty(fake_inputs):
    fake_inputs("")
    assert prompt("Label", default="foo") == "foo"

def test_prompt_loops_until_value_given(fake_inputs):
    fake_inputs("", "", "hello")
    assert prompt("Label") == "hello"


# ── prompt_choice ─────────────────────────────────────────────────────────────

def test_prompt_choice_accepts_valid(fake_inputs):
    fake_inputs("m")
    assert prompt_choice("Mode", ["m", "t"]) == "m"

def test_prompt_choice_rejects_invalid_then_accepts(fake_inputs):
    fake_inputs("x", "t")
    assert prompt_choice("Mode", ["m", "t"]) == "t"

def test_prompt_choice_case_insensitive(fake_inputs):
    fake_inputs("M")
    assert prompt_choice("Mode", ["m", "t"]) == "m"


# ── prompt_int ────────────────────────────────────────────────────────────────

def test_prompt_int_returns_integer(fake_inputs):
    fake_inputs("5")
    assert prompt_int("Number") == 5

def test_prompt_int_uses_default_on_empty(fake_inputs):
    fake_inputs("")
    assert prompt_int("Number", default=1) == 1

def test_prompt_int_rejects_below_min(fake_inputs):
    fake_inputs("0", "1")
    assert prompt_int("Number", min_val=1) == 1

def test_prompt_int_rejects_above_max(fake_inputs):
    fake_inputs("100", "5")
    assert prompt_int("Number", max_val=99) == 5

def test_prompt_int_rejects_non_numeric(fake_inputs):
    fake_inputs("abc", "3")
    assert prompt_int("Number") == 3


# ── _pick_show ────────────────────────────────────────────────────────────────

def test_pick_show_select_by_number(fake_inputs):
    fake_inputs("1")
    assert _pick_show("/tv", test_shows=_TEST_SHOWS) == _TEST_SHOWS[0]

def test_pick_show_select_last_entry(fake_inputs):
    fake_inputs(str(len(_TEST_SHOWS)))
    assert _pick_show("/tv", test_shows=_TEST_SHOWS) == _TEST_SHOWS[-1]

def test_pick_show_enter_new_name(fake_inputs):
    fake_inputs("My New Show")
    assert _pick_show("/tv", test_shows=_TEST_SHOWS) == "My New Show"

def test_pick_show_invalid_number_then_valid(fake_inputs):
    fake_inputs("99", "2")
    assert _pick_show("/tv", test_shows=_TEST_SHOWS) == _TEST_SHOWS[1]

def test_pick_show_no_existing_shows_prompts_for_name(fake_inputs):
    fake_inputs("Fresh Show")
    assert _pick_show("/tv", test_shows=[]) == "Fresh Show"


# ── _pick_season ──────────────────────────────────────────────────────────────

def test_pick_season_select_first_by_list_index(fake_inputs):
    seasons = _TEST_SEASONS["Breaking Bad"]  # [1, 2, 3]
    fake_inputs("1")
    assert _pick_season("/tv", "Breaking Bad", test_seasons=seasons) == 1

def test_pick_season_select_last_by_list_index(fake_inputs):
    seasons = _TEST_SEASONS["Breaking Bad"]  # [1, 2, 3]
    fake_inputs("3")
    assert _pick_season("/tv", "Breaking Bad", test_seasons=seasons) == 3

def test_pick_season_add_new_season_number(fake_inputs):
    # 4 is beyond the list length of 3, so treated as new season number 4
    seasons = _TEST_SEASONS["Breaking Bad"]  # [1, 2, 3]
    fake_inputs("4")
    assert _pick_season("/tv", "Breaking Bad", test_seasons=seasons) == 4

def test_pick_season_specials_is_zero(fake_inputs):
    seasons = _TEST_SEASONS["Breaking Bad"]
    fake_inputs("0")
    assert _pick_season("/tv", "Breaking Bad", test_seasons=seasons) == 0

def test_pick_season_rejects_non_numeric_then_accepts(fake_inputs):
    seasons = _TEST_SEASONS["Breaking Bad"]
    fake_inputs("abc", "1")
    assert _pick_season("/tv", "Breaking Bad", test_seasons=seasons) == 1

def test_pick_season_no_existing_seasons_prompts_for_number(fake_inputs):
    fake_inputs("2")
    assert _pick_season("/tv", "New Show", test_seasons=[]) == 2


# ── _pick_movie_title ─────────────────────────────────────────────────────────

def test_pick_movie_title_valid_number(fake_inputs):
    fake_inputs("1")
    result = _pick_movie_title(MOVIE_TITLES)
    assert result["number"] == 1

def test_pick_movie_title_rejects_unknown_number(fake_inputs):
    fake_inputs("99", "2")
    result = _pick_movie_title(MOVIE_TITLES)
    assert result["number"] == 2

def test_pick_movie_title_rejects_non_numeric(fake_inputs):
    fake_inputs("abc", "3")
    result = _pick_movie_title(MOVIE_TITLES)
    assert result["number"] == 3


# ── _pick_episode_titles ──────────────────────────────────────────────────────

def test_pick_episode_titles_valid(fake_inputs):
    fake_inputs("1,2")
    selected, episodes = _pick_episode_titles(TV_TITLES, season=1, start_ep=1, ep_count=2)
    assert [t["number"] for t in selected] == [1, 2]
    assert episodes == [1, 2]

def test_pick_episode_titles_wrong_count_then_valid(fake_inputs):
    fake_inputs("1", "1,2")  # first attempt has only 1 number
    selected, _ = _pick_episode_titles(TV_TITLES, season=1, start_ep=1, ep_count=2)
    assert len(selected) == 2

def test_pick_episode_titles_unknown_title_then_valid(fake_inputs):
    fake_inputs("1,99", "1,2")  # 99 doesn't exist
    selected, _ = _pick_episode_titles(TV_TITLES, season=1, start_ep=1, ep_count=2)
    assert [t["number"] for t in selected] == [1, 2]

def test_pick_episode_titles_start_ep_offset(fake_inputs):
    fake_inputs("3,4")
    _, episodes = _pick_episode_titles(TV_TITLES, season=1, start_ep=5, ep_count=2)
    assert episodes == [5, 6]


# ── run_movie flow ─────────────────────────────────────────────────────────────
#
# Input sequence for run_movie (test_mode=True):
#   1. Movie name
#   2. Proceed? [y/n/s]elect
#   3. (if y) Start ripping? [y/n]
#   3. (if s) Enter title number
#   4. (if s) Start ripping? [y/n]

@patch("dvd_ripper.cli._encode", return_value=True)
@patch("dvd_ripper.cli._scan", return_value=MOVIE_TITLES)
def test_run_movie_happy_path(mock_scan, mock_encode, cfg, fake_inputs):
    fake_inputs("Inception", "y", "y")
    run_movie(cfg, test_mode=True)
    mock_encode.assert_called_once()
    _, kwargs = mock_encode.call_args
    # Auto-selected title should be #1 (longest at 2h32m)
    assert mock_encode.call_args.args[3] == 1


@patch("dvd_ripper.cli._encode", return_value=True)
@patch("dvd_ripper.cli._scan", return_value=MOVIE_TITLES)
def test_run_movie_cancel_at_proceed(mock_scan, mock_encode, cfg, fake_inputs):
    fake_inputs("Inception", "n")
    run_movie(cfg, test_mode=True)
    mock_encode.assert_not_called()


@patch("dvd_ripper.cli._encode", return_value=True)
@patch("dvd_ripper.cli._scan", return_value=MOVIE_TITLES)
def test_run_movie_cancel_at_final_confirm(mock_scan, mock_encode, cfg, fake_inputs):
    fake_inputs("Inception", "y", "n")
    run_movie(cfg, test_mode=True)
    mock_encode.assert_not_called()


@patch("dvd_ripper.cli._encode", return_value=True)
@patch("dvd_ripper.cli._scan", return_value=MOVIE_TITLES)
def test_run_movie_invalid_proceed_then_accepts(mock_scan, mock_encode, cfg, fake_inputs):
    fake_inputs("Inception", "x", "y", "y")
    run_movie(cfg, test_mode=True)
    mock_encode.assert_called_once()


@patch("dvd_ripper.cli._encode", return_value=True)
@patch("dvd_ripper.cli._scan", return_value=MOVIE_TITLES)
def test_run_movie_manual_select_flow(mock_scan, mock_encode, cfg, fake_inputs):
    # 's' → manually pick title 3 → confirm
    fake_inputs("Inception", "s", "3", "y")
    run_movie(cfg, test_mode=True)
    mock_encode.assert_called_once()
    assert mock_encode.call_args.args[3] == 3


@patch("dvd_ripper.cli._encode", return_value=True)
@patch("dvd_ripper.cli._scan", return_value=MOVIE_TITLES)
def test_run_movie_manual_select_invalid_then_valid(mock_scan, mock_encode, cfg, fake_inputs):
    # 's' → invalid title 99 → valid title 2 → confirm
    fake_inputs("Inception", "s", "99", "2", "y")
    run_movie(cfg, test_mode=True)
    assert mock_encode.call_args.args[3] == 2


# ── run_tv flow ───────────────────────────────────────────────────────────────
#
# Input sequence for run_tv (test_mode=True, show="Breaking Bad" = "1"):
#   1. Show selector       → "1"  (Breaking Bad)
#   2. Season selector     → "1"  (Season 1, list index 1 of [1,2,3])
#   3. Starting episode    → ""   (default 1)
#   4. Episodes on disc    → "2"
#   5. Proceed?            → "y" / "n" / "s"
#   6. (if s) Title nums   → "1,4"
#   7. Start ripping?      → "y" / "n"

@patch("dvd_ripper.cli._encode", return_value=True)
@patch("dvd_ripper.cli._scan", return_value=TV_TITLES)
def test_run_tv_happy_path(mock_scan, mock_encode, cfg, fake_inputs):
    fake_inputs("1", "1", "", "2", "y", "y")
    run_tv(cfg, test_mode=True)
    # 2 episodes auto-selected → encode called twice
    assert mock_encode.call_count == 2


@patch("dvd_ripper.cli._encode", return_value=True)
@patch("dvd_ripper.cli._scan", return_value=TV_TITLES)
def test_run_tv_cancel_at_proceed(mock_scan, mock_encode, cfg, fake_inputs):
    fake_inputs("1", "1", "", "2", "n")
    run_tv(cfg, test_mode=True)
    mock_encode.assert_not_called()


@patch("dvd_ripper.cli._encode", return_value=True)
@patch("dvd_ripper.cli._scan", return_value=TV_TITLES)
def test_run_tv_cancel_at_final_confirm(mock_scan, mock_encode, cfg, fake_inputs):
    fake_inputs("1", "1", "", "2", "y", "n")
    run_tv(cfg, test_mode=True)
    mock_encode.assert_not_called()


@patch("dvd_ripper.cli._encode", return_value=True)
@patch("dvd_ripper.cli._scan", return_value=TV_TITLES)
def test_run_tv_invalid_proceed_then_accepts(mock_scan, mock_encode, cfg, fake_inputs):
    fake_inputs("1", "1", "", "2", "x", "y", "y")
    run_tv(cfg, test_mode=True)
    assert mock_encode.call_count == 2


@patch("dvd_ripper.cli._encode", return_value=True)
@patch("dvd_ripper.cli._scan", return_value=TV_TITLES)
def test_run_tv_manual_select_flow(mock_scan, mock_encode, cfg, fake_inputs):
    # 's' → manually enter title numbers 1,4 → confirm
    fake_inputs("1", "1", "", "2", "s", "1,4", "y")
    run_tv(cfg, test_mode=True)
    assert mock_encode.call_count == 2
    # First encode should use title 1, second title 4
    calls = mock_encode.call_args_list
    assert calls[0].args[3] == 1
    assert calls[1].args[3] == 4


@patch("dvd_ripper.cli._encode", return_value=True)
@patch("dvd_ripper.cli._scan", return_value=TV_TITLES)
def test_run_tv_new_show_entry(mock_scan, mock_encode, cfg, fake_inputs):
    # Enter a new show name (text, not a list number)
    # → no existing seasons → prompt_int for season
    fake_inputs("My New Show", "1", "", "2", "y", "y")
    run_tv(cfg, test_mode=True)
    assert mock_encode.call_count == 2


@patch("dvd_ripper.cli._encode", return_value=True)
@patch("dvd_ripper.cli._scan", return_value=TV_TITLES)
def test_run_tv_encodes_correct_episode_numbers(mock_scan, mock_encode, cfg, fake_inputs):
    # Start at episode 5, 2 episodes → S01E05 and S01E06
    fake_inputs("1", "1", "5", "2", "y", "y")
    run_tv(cfg, test_mode=True)
    assert mock_encode.call_count == 2
    # Output paths should contain S01E05 and S01E06
    paths = [str(call.args[1]) for call in mock_encode.call_args_list]
    assert any("S01E05" in p for p in paths)
    assert any("S01E06" in p for p in paths)
