"""Command-line entrypoint for the Vega Scene mirror.

uv run vega sync      # pull the full forward program into vega.db
uv run vega status    # show what's in the store and the last sync
uv run vega serve     # browse the program at http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import sys

from vega.api import VegaSceneClient
from vega.store import DEFAULT_DB_PATH, Store
from vega.sync import sync


def _cmd_sync(args: argparse.Namespace) -> int:
    with Store(args.db) as store, VegaSceneClient() as client:
        result = sync(
            store, client, on_progress=lambda msg: print(msg, file=sys.stderr)
        )
    print(
        f"synced {result.days} days: {result.movies_seen} movies, "
        f"{result.shows_seen} shows ({result.shows_pruned} pruned)"
    )
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    from pathlib import Path

    from vega.build import build

    pages = build(Path(args.out), base=args.base, on_progress=lambda msg: print(msg, file=sys.stderr))
    print(f"built {pages} pages to {args.out}/")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from vega.web import create_app

    uvicorn.run(create_app(args.db), host=args.host, port=args.port)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        counts = store.counts()
        print(f"movies:         {counts['movies']}")
        print(f"shows:          {counts['shows']}")
        print(f"upcoming shows: {counts['upcoming_shows']}")
        last = store.conn.execute(
            "SELECT started_at, finished_at, days, shows_seen, shows_pruned, ok, error "
            "FROM sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last is None:
            print("last sync:      never")
        else:
            status = "ok" if last["ok"] else f"FAILED ({last['error']})"
            print(
                f"last sync:      {last['started_at']} -> {last['finished_at']} [{status}]"
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="vega", description="Vega Scene program mirror"
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"path to the SQLite database (default: {DEFAULT_DB_PATH})",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="pull the full forward program into the store")
    sub.add_parser("status", help="show store counts and the last sync run")
    serve = sub.add_parser("serve", help="run the local web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8800)
    build = sub.add_parser("build", help="render the site to static files")
    build.add_argument("--out", default="dist", help="output directory (default: dist)")
    build.add_argument(
        "--base", default="", help="URL path prefix for internal links, e.g. /vega"
    )

    args = parser.parse_args()
    if args.command == "sync":
        return _cmd_sync(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "build":
        return _cmd_build(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
