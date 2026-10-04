"""Run FundFlow locally:  python -m fundflow  [--port 5050] [--db path] [--demo] [--open]"""
import argparse
import os
import threading
import webbrowser

from . import DEFAULT_DB, create_app, db, demo


def main() -> None:
    parser = argparse.ArgumentParser(prog="fundflow", description="FundFlow personal fund ledger")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default: localhost only)")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--db", default=None, help=f"SQLite file (default: {DEFAULT_DB})")
    parser.add_argument("--demo", action="store_true",
                        help="use a separate demo database filled with sample data")
    parser.add_argument("--open", action="store_true", help="open the app in your browser")
    args = parser.parse_args()

    path = args.db
    if args.demo:
        path = path or os.path.join(os.path.dirname(DEFAULT_DB), "demo.db")
    app = create_app(path)
    if args.demo:
        conn = db.connect(app.config["DATABASE"])
        demo.seed(conn)
        conn.close()

    url = f"http://{args.host}:{args.port}/"
    print(f"FundFlow running at {url}  (database: {app.config['DATABASE']})")
    if args.open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
