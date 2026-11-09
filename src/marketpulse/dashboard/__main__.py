"""Launch the dashboard on 127.0.0.1. Streamlit is imported only when this runs."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import MISSING_EXTRA


def launch_argv(port: str = "8501", *, demo: bool = False, root: str | None = None) -> list[str]:
    app = Path(__file__).with_name("app.py")
    argv = [
        sys.executable, "-m", "streamlit", "run", str(app),
        "--server.address", "127.0.0.1",
        "--server.port", str(port),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--",
    ]
    if demo:
        argv.append("--demo")
    if root:
        argv.extend(["--root", root])
    return argv


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        import streamlit  # noqa: F401
    except ImportError:
        print(MISSING_EXTRA, file=sys.stderr)
        return 2
    demo = "--demo" in argv or os.environ.get("MARKETPULSE_DASHBOARD_DEMO") == "1"
    root = os.environ.get("MARKETPULSE_DASHBOARD_ROOT")
    if "--root" in argv:
        root = argv[argv.index("--root") + 1]
    port = os.environ.get("MARKETPULSE_DASHBOARD_PORT", "8501")
    command = launch_argv(port, demo=demo, root=root)
    os.execv(command[0], command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
