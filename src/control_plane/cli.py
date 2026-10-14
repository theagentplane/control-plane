"""CLI entrypoints for the control plane API and Streamlit UI."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys


def _prog() -> str:
    """Name this invocation by how it was reached, so hints stay copy-pasteable."""
    if pathlib.Path(sys.argv[0]).name == "__main__.py":
        return "python -m control_plane"
    return "control-plane"


def main() -> None:
    prog = _prog()
    parser = argparse.ArgumentParser(prog=prog)
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="Run the FastAPI control plane (foreground)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8800)
    serve.add_argument("--db", default=None, help="SQLite path (default CONTROL_PLANE_DB)")
    serve.add_argument("--reload", action="store_true")

    start = sub.add_parser(
        "start",
        help="Start the control plane in the background (one instance; run again to check it)",
    )
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8800)
    start.add_argument("--db", default=None, help="SQLite path (default CONTROL_PLANE_DB)")

    sub.add_parser("stop", help="Stop the background control plane started with `start`")
    sub.add_parser("status", help="Show whether the background control plane is running")

    ui = sub.add_parser("ui", help="Run Admin + Dashboard (Streamlit)")
    ui.add_argument("--port", type=int, default=8501)

    args = parser.parse_args()
    if args.cmd == "serve":
        if args.db:
            os.environ["CONTROL_PLANE_DB"] = args.db
        import uvicorn

        uvicorn.run(
            "control_plane.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
    elif args.cmd == "start":
        from control_plane import servicectl

        try:
            servicectl.start(host=args.host, port=args.port, db=args.db)
        except servicectl.ServiceError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
    elif args.cmd == "stop":
        from control_plane import servicectl

        servicectl.stop()  # idempotent: "wasn't running" is not an error
    elif args.cmd == "status":
        from control_plane import servicectl

        info = servicectl.status()
        raise SystemExit(0 if info.get("running") else 1)
    elif args.cmd == "ui":
        print(f"UI is served with the API. Run: {prog} serve --port 8800")
        print("Then open http://127.0.0.1:8800/")
        raise SystemExit(0)


if __name__ == "__main__":
    main()
