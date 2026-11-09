"""Atomic local writes and conservative, non-overlapping snapshot selection."""
import json
import math
import os
from pathlib import Path
import tempfile
from datetime import timedelta
from .normalize import parse_time

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".marketpulse-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_json(path: Path, value) -> None:
    atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def load_history(directory: Path, now, recent_hours: float):
    """Never compare overlapping recent windows; corrupt snapshots are visible."""
    warnings, valid = [], []
    for path in sorted(directory.glob("*.json")):
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            end = parse_time(snapshot["generated_at"])
            if (snapshot.get("schema_version") == 1
                    and snapshot.get("classifier_version") == "lexicon-1"
                    and snapshot.get("recent_hours") == recent_hours
                    and now - timedelta(days=7) <= end <= now - timedelta(hours=recent_hours)):
                if not isinstance(snapshot["tickers"], dict):
                    raise ValueError("invalid ticker mapping")
                for ticker in snapshot["tickers"].values():
                    for stats in ticker["recent"]["sources"].values():
                        if not (isinstance(stats["volume"], int) and stats["volume"] >= 0
                                and isinstance(stats["score_sum"], (int, float))
                                and math.isfinite(stats["score_sum"])):
                            raise ValueError("invalid source statistics")
                valid.append((end, snapshot))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            warnings.append(f"Ignored snapshot {path.name}: {type(exc).__name__}")
    return (max(valid, key=lambda pair: pair[0])[1] if valid else None), warnings
