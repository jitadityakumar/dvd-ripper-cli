import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.toml"
_EXAMPLE_PATH = Path(__file__).parent.parent / "config.toml.example"


@dataclass
class Config:
    movies_dir: str
    tv_dir: str
    device: str
    video_encoder: str
    audio_encoder: str
    rf: int
    output_format: str
    min_episode_duration: int
    min_feature_duration: int

    @classmethod
    def test_default(cls) -> "Config":
        return cls(
            movies_dir="/mnt/media/Movies",
            tv_dir="/mnt/media/TV Shows",
            device="/dev/sr0",
            video_encoder="qsv_h265",
            audio_encoder="copy:ac3",
            rf=20,
            output_format="mp4",
            min_episode_duration=900,
            min_feature_duration=3600,
        )


def load() -> Config:
    if not CONFIG_PATH.exists():
        print(f"\n  Config file not found: {CONFIG_PATH}")
        print(f"  Create it with the following content:\n")
        if _EXAMPLE_PATH.exists():
            for line in _EXAMPLE_PATH.read_text().splitlines():
                print(f"    {line}")
        else:
            print(f"    (see config.toml.example)")
        print()
        raise SystemExit(1)

    with open(CONFIG_PATH, "rb") as f:
        data = tomllib.load(f)

    return Config(
        movies_dir=data["paths"]["movies_dir"],
        tv_dir=data["paths"]["tv_dir"],
        device=data["paths"]["device"],
        video_encoder=data["encoding"]["video_encoder"],
        audio_encoder=data["encoding"]["audio_encoder"],
        rf=int(data["encoding"]["rf"]),
        output_format=data["encoding"]["output_format"],
        min_episode_duration=int(data["tv"]["min_episode_duration_seconds"]),
        min_feature_duration=int(data["movie"]["min_feature_duration_seconds"]),
    )
