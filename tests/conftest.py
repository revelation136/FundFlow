import pytest

from fundflow import create_app, db

P = 100  # centavos per peso


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "test.db"))


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def conn(app):
    c = db.connect(app.config["DATABASE"])
    yield c
    c.close()


class Book:
    """Small helper to set up accounts and funds directly."""

    def __init__(self, conn):
        self.conn = conn
        self.sys = db.system_ids(conn)

    def account(self, name, kind, subtype=""):
        with self.conn:
            return self.conn.execute(
                "INSERT INTO accounts (name, kind, subtype) VALUES (?, ?, ?)", (name, kind, subtype)
            ).lastrowid

    def income(self, name):
        return self.conn.execute(
            "SELECT id FROM accounts WHERE name = ? AND kind = 'income'", (name,)
        ).fetchone()["id"]

    def fund(self, name, **extra):
        cols = ["name", *extra]
        with self.conn:
            return self.conn.execute(
                f"INSERT INTO funds ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                (name, *extra.values()),
            ).lastrowid


@pytest.fixture
def book(conn):
    return Book(conn)
