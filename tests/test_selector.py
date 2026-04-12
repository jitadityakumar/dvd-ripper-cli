import pytest
from dvd_ripper.selector import select_movie, select_tv


def t(number, h, m, s):
    return {"number": number, "duration": (h, m, s), "resolution": "853x480"}


# ── select_movie ──────────────────────────────────────────────────────────────

def test_select_movie_picks_longest_eligible():
    titles = [t(1, 0, 45, 0), t(2, 1, 30, 0), t(3, 0, 5, 0)]
    result = select_movie(titles, min_duration=2700)  # 45 min minimum
    assert result["number"] == 2

def test_select_movie_falls_back_to_longest_when_none_eligible():
    titles = [t(1, 0, 20, 0), t(2, 0, 30, 0), t(3, 0, 10, 0)]
    result = select_movie(titles, min_duration=7200)  # 2-hour minimum, none qualify
    assert result["number"] == 2

def test_select_movie_returns_none_for_empty_list():
    assert select_movie([], min_duration=3600) is None

def test_select_movie_single_eligible_title():
    titles = [t(1, 2, 0, 0)]
    result = select_movie(titles, min_duration=3600)
    assert result["number"] == 1

def test_select_movie_prefers_eligible_over_longer_ineligible():
    # Title 2 is longer but below min; title 1 is eligible
    titles = [t(1, 1, 5, 0), t(2, 0, 55, 0)]
    result = select_movie(titles, min_duration=3600)  # 1 hour
    assert result["number"] == 1


# ── select_tv ─────────────────────────────────────────────────────────────────

def test_select_tv_returns_up_to_ep_count():
    titles = [t(i, 0, 47, 0) for i in range(1, 7)]
    result = select_tv(titles, min_dur=900, ep_count=4)
    assert len(result) == 4

def test_select_tv_filters_titles_below_min_duration():
    titles = [t(1, 0, 47, 0), t(2, 0, 48, 0), t(3, 0, 3, 0), t(4, 0, 1, 0)]
    result = select_tv(titles, min_dur=900, ep_count=4)
    numbers = [r["number"] for r in result]
    assert 3 not in numbers
    assert 4 not in numbers

def test_select_tv_excludes_duration_outliers():
    # Title 5 is a bonus feature — much longer than the episode cluster
    titles = [
        t(1, 0, 47, 0), t(2, 0, 48, 0), t(3, 0, 47, 30),
        t(4, 0, 48, 30), t(5, 2, 30, 0),
    ]
    result = select_tv(titles, min_dur=900, ep_count=4)
    assert 5 not in [r["number"] for r in result]

def test_select_tv_results_sorted_by_title_number():
    titles = [t(4, 0, 47, 0), t(1, 0, 48, 0), t(3, 0, 47, 30), t(2, 0, 48, 30)]
    result = select_tv(titles, min_dur=900, ep_count=4)
    numbers = [r["number"] for r in result]
    assert numbers == sorted(numbers)

def test_select_tv_returns_fewer_when_not_enough_titles():
    titles = [t(1, 0, 47, 0), t(2, 0, 47, 30)]
    result = select_tv(titles, min_dur=900, ep_count=4)
    assert len(result) == 2

def test_select_tv_falls_back_when_no_titles_meet_min_dur():
    # All too short — falls back to titles[:ep_count]
    titles = [t(1, 0, 5, 0), t(2, 0, 4, 0), t(3, 0, 3, 0)]
    result = select_tv(titles, min_dur=2700, ep_count=2)
    assert len(result) == 2
    assert result[0]["number"] == 1
    assert result[1]["number"] == 2
