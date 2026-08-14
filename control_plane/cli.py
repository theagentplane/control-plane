"""CLI entrypoints for the control plane API and Streamlit UI."""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog="control-plane")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="Run the FastAPI control plane")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8800)
    serve.add_argument("--db", default=None, help="SQLite path (default CONTROL_PLANE_DB)")
    serve.add_argument("--reload", action="store_true")

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
    elif args.cmd == "ui":
        print("UI is served with the API. Run: control-plane serve --port 8800")
        print("Then open http://127.0.0.1:8800/")
        raise SystemExit(0)


if __name__ == "__main__":
    main()
