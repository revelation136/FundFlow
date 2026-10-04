"""FundFlow - a personal fund ledger that knows where every peso came from and went."""
import os
import sqlite3

from flask import Flask, g

from . import db

__version__ = "0.1.0"

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "fundflow.db")


def create_app(database: str | None = None) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["DATABASE"] = database or os.environ.get("FUNDFLOW_DB") or DEFAULT_DB
    app.json.ensure_ascii = False
    app.json.sort_keys = False

    path = app.config["DATABASE"]
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = db.connect(path)
    db.init_db(conn)
    if path == ":memory:":
        # Keep the in-memory database alive for the lifetime of the app (tests).
        app.config["SHARED_CONN"] = conn
    else:
        conn.close()

    def get_conn() -> sqlite3.Connection:
        if "conn" not in g:
            g.conn = app.config.get("SHARED_CONN") or db.connect(path)
        return g.conn

    app.get_conn = get_conn

    @app.teardown_appcontext
    def close_conn(exc):
        conn = g.pop("conn", None)
        if conn is not None and conn is not app.config.get("SHARED_CONN"):
            conn.close()

    from .api import bp
    app.register_blueprint(bp)

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    return app
