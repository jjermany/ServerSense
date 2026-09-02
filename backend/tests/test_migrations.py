import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def _alembic(backend: Path, config_dir: Path, target: str) -> None:
    environment = os.environ | {
        "SERVERSENSE_CONFIG_DIR": str(config_dir),
        "SERVERSENSE_SECRET_KEY": "migration-test-secret-key",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=backend,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_media_schedule_migration_upgrades_existing_database(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[1]
    _alembic(backend, tmp_path, "a2c91d84e630")
    database = tmp_path / "serversense.db"
    now = "2026-08-18 12:00:00"
    with sqlite3.connect(database) as connection:
        cursor = connection.execute(
            "INSERT INTO integrations (provider, name, enabled, config, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "sonarr",
                "TV",
                1,
                json.dumps({"url": "http://sonarr:8989", "last_collected_at": now}),
                now,
                now,
            ),
        )
        integration_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO media_activities "
            "(integration_id, external_id, occurred_at, provider, instance_name, event_type, "
            "media_type, title, parent_title, season_number, episode_number, quality, bytes, "
            "is_upgrade) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                integration_id,
                "old-path",
                now,
                "sonarr",
                "TV",
                "file_deleted",
                "episode",
                "/media/tv/show/old.mkv",
                "Show",
                None,
                None,
                "HDTV-720p",
                100,
                1,
            ),
        )
        connection.commit()

    _alembic(backend, tmp_path, "head")

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "media_schedules" in tables
        config = json.loads(connection.execute("SELECT config FROM integrations").fetchone()[0])
        assert "last_collected_at" not in config
        title = connection.execute("SELECT title FROM media_activities").fetchone()[0]
        assert title == "Unknown title"


def test_durable_sense_migration_preserves_and_labels_existing_messages(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[1]
    _alembic(backend, tmp_path, "3d4e5f607182")
    database = tmp_path / "serversense.db"
    now = "2026-09-02 12:00:00"
    with sqlite3.connect(database) as connection:
        cursor = connection.execute(
            "INSERT INTO ai_conversations (title, created_at, updated_at) VALUES (?, ?, ?)",
            ("Existing conversation", now, now),
        )
        conversation_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO ai_messages (conversation_id, timestamp, role, content) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, now, "assistant", "Existing answer"),
        )
        connection.commit()

    _alembic(backend, tmp_path, "head")

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"ai_jobs", "in_app_notifications"}.issubset(tables)
        job_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('ai_jobs')").fetchall()
        }
        assert {
            "notify_on_completion",
            "completion_notification_sent",
            "notification_id",
            "queued_at",
            "first_token_at",
            "cancelled_at",
            "timed_out_at",
            "interrupted_at",
            "generated_tokens",
        }.issubset(job_columns)
        source = connection.execute("SELECT source FROM ai_messages").fetchone()[0]
        assert source == "sense_ai"
