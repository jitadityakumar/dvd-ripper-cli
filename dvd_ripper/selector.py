import statistics as _stats


def select_movie(titles: list[dict], min_duration: int) -> dict | None:
    """Return the longest title >= min_duration, falling back to longest overall."""
    eligible = [t for t in titles if _secs(t) >= min_duration]
    pool = eligible if eligible else titles
    return max(pool, key=_secs) if pool else None


def select_tv(
    titles: list[dict], min_dur: int, ep_count: int
) -> list[dict]:
    """Return ep_count titles matching the TV episode duration profile."""
    eligible = [t for t in titles if _secs(t) >= min_dur]
    if eligible:
        median = _stats.median(_secs(t) for t in eligible)
        # 20% window: episode titles on the same disc are within ~20% of each
        # other; bonus features and trailers are typically much shorter or longer.
        clustered = [
            t for t in eligible
            if abs(_secs(t) - median) / median <= 0.20
        ]
        clustered.sort(key=lambda t: t["number"])
        return clustered[:ep_count]
    return titles[:ep_count]


def _secs(t: dict) -> int:
    h, m, s = t["duration"]
    return h * 3600 + m * 60 + s
