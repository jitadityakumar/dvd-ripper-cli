from pathlib import Path


def sanitize(name: str) -> str:
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, "")
    return name.strip()


def movie_path(movies_dir: str, name: str, output_format: str = "mp4") -> Path:
    safe = sanitize(name)
    return Path(movies_dir) / safe / f"{safe}.{output_format}"


def tv_path(tv_dir: str, show: str, season: int, episode: int, output_format: str = "mp4") -> Path:
    safe_show = sanitize(show)
    ep_str = f"S{season:02d}E{episode:02d}"
    filename = f"{safe_show} - {ep_str}.{output_format}"
    return Path(tv_dir) / safe_show / f"Season {season:02d}" / filename
