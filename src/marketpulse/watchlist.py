"""Independent, cwd-safe watchlist loading compatible with HeadlineLens YAML."""
from dataclasses import dataclass
from importlib.resources import files
import os
from pathlib import Path
import re
import yaml
from .storage import PROJECT_ROOT, atomic_text


@dataclass(frozen=True)
class Watchlist:
    market: tuple[str, ...]
    tickers: tuple[str, ...]

    @property
    def symbols(self):
        return tuple(dict.fromkeys(self.market + self.tickers))


def symbols(values) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise ValueError("Symbols must be a list of strings")
    result = []
    for value in values:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=\-]{0,19}", value.strip().upper()):
            raise ValueError(f"Invalid ticker: {value!r}")
        result.append(value.strip().upper())
    return tuple(dict.fromkeys(result))


def validate(data):
    if not isinstance(data, dict) or set(data) - {"market", "tickers"}:
        raise ValueError("Watchlist requires only market and tickers lists")
    result = Watchlist(symbols(data.get("market", [])), symbols(data.get("tickers", [])))
    if not result.symbols:
        raise ValueError("Watchlist cannot be empty")
    return result


def load_watchlist(path: Path | None = None):
    override = os.environ.get("MARKETPULSE_WATCHLIST")
    if path is None and override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("MARKETPULSE_WATCHLIST must be an absolute path")
    if path is None:
        local = PROJECT_ROOT / "watchlist.yml"
        text = (local.read_text(encoding="utf-8") if local.exists() else
                files("marketpulse").joinpath("default_watchlist.yml").read_text(encoding="utf-8"))
    else:
        text = path.read_text(encoding="utf-8")
    return validate(yaml.safe_load(text))


def save_watchlist(watchlist: Watchlist, path: Path):
    data = {"market": list(watchlist.market), "tickers": list(watchlist.tickers)}
    validate(data)
    atomic_text(path, yaml.safe_dump(data, sort_keys=False))
