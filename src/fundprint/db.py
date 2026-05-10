"""psycopg connection helpers. No ORM; just a connection and a transaction context."""

from contextlib import contextmanager
from typing import Generator

import psycopg

from fundprint.config import settings


def connect() -> psycopg.Connection:
    """Return an open psycopg connection using DATABASE_URL from settings."""
    return psycopg.connect(settings.database_url)


@contextmanager
def transaction() -> Generator[psycopg.Connection, None, None]:
    """Context manager that yields a connection inside a single transaction.

    Commits on clean exit, rolls back on any exception.
    """
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
