from pathlib import Path

from cheap_flights.config import load_config
from cheap_flights.places import PlaceIndex, resolve_routes

ROOT = Path(__file__).parent.parent


def test_example_config_is_valid():
    env = {}
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            env[key] = value
    config = load_config(ROOT / "config.example.yaml", env)
    assert resolve_routes(config.routes, PlaceIndex.bundled())
