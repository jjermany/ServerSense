from sqlalchemy import text

from serversense.db import engine


def test_sqlite_uses_wal_with_a_bounded_busy_timeout() -> None:
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
        busy_timeout = connection.scalar(text("PRAGMA busy_timeout"))

    assert isinstance(busy_timeout, int)
    assert busy_timeout == 15_000
