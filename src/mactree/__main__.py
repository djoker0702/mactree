"""Command-line entry point: ``python -m mactree``.

Two frontends share the same multi-threaded scanner:

* **TUI** (default): an interactive terminal browser (``ncdu``-style).
* **Web**: a FastAPI server rendering a D3 treemap / sunburst in the browser.

Examples
--------
    python -m mactree                 # TUI, scans your home directory
    python -m mactree ~/Downloads     # TUI, scans a specific path
    python -m mactree --web           # launch the browser UI instead
    python -m mactree --web --port 8010
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mactree",
        description="Disk usage analyzer with multi-threaded scanning "
        "(terminal UI by default, or a browser UI with --web).",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Directory to scan (TUI mode). Defaults to your home directory.",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--tui",
        dest="mode",
        action="store_const",
        const="tui",
        help="Interactive terminal UI (default).",
    )
    mode.add_argument(
        "--web",
        dest="mode",
        action="store_const",
        const="web",
        help="Browser-based UI (starts a local web server).",
    )
    parser.set_defaults(mode="tui")

    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Scanner thread-pool size (default: based on CPU count).",
    )
    # Web-only options
    parser.add_argument("--host", default="127.0.0.1", help="Web mode: bind host.")
    parser.add_argument("--port", type=int, default=8000, help="Web mode: bind port.")

    args = parser.parse_args()

    if args.mode == "web":
        _run_web(args)
    else:
        _run_tui(args)


def _run_tui(args: argparse.Namespace) -> None:
    from .tui import run_tui

    path = args.path or os.path.expanduser("~")
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        sys.exit(f"error: not a directory: {path}")
    run_tui(path, workers=args.workers)


def _run_web(args: argparse.Namespace) -> None:
    import uvicorn

    from .app import create_app

    app = create_app(default_workers=args.workers)
    url = f"http://{args.host}:{args.port}"
    print(f"MacTree (web) running at {url}  (Ctrl-C to stop)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
