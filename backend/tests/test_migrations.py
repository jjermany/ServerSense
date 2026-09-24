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


def test_mfa_migration_preserves_existing_account_and_defaults_off(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[1]
    _alembic(backend, tmp_path, "c7e4b1a9d2f0")
    database = tmp_path / "serversense.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at, updated_at) VALUES (?, ?, 1, ?, ?)",
            (
                "ExistingAdmin",
                "existing-password-hash",
                "2026-09-06 12:00:00",
                "2026-09-06 12:00:00",
            ),
        )
        connection.commit()
    _alembic(backend, tmp_path, "head")
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT username, password_hash, mfa_secret, mfa_recovery_hashes FROM users"
        ).fetchone()
        assert row == ("ExistingAdmin", "existing-password-hash", None, None)
        connection.execute(
            "UPDATE users SET mfa_secret = 'encrypted-placeholder', mfa_last_step = 1234"
        )
        connection.commit()
    _alembic(backend, tmp_path, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT mfa_secret, mfa_last_step FROM users").fetchone() == (
            "encrypted-placeholder",
            1234,
        )


def test_mfa_migration_empty_database_and_round_trip(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[1]
    _alembic(backend, tmp_path, "head")
    environment = os.environ | {
        "SERVERSENSE_CONFIG_DIR": str(tmp_path),
        "SERVERSENSE_SECRET_KEY": "migration-test-secret-key",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "c7e4b1a9d2f0"],
        cwd=backend,
        env=environment,
        check=True,
        capture_output=True,
    )
    _alembic(backend, tmp_path, "head")
    with sqlite3.connect(tmp_path / "serversense.db") as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info('users')")}
    assert {
        "mfa_secret",
        "mfa_pending_secret",
        "mfa_pending_expires_at",
        "mfa_last_step",
        "mfa_recovery_hashes",
    }.issubset(columns)


def test_media_correlation_migration_preserves_history_and_resets_cursor(
    tmp_path: Path,
) -> None:
    backend = Path(__file__).resolve().parents[1]
    _alembic(backend, tmp_path, "e8a6f20b91c3")
    database = tmp_path / "serversense.db"
    now = "2026-09-24 12:00:00"
    with sqlite3.connect(database) as connection:
        cursor = connection.execute(
            "INSERT INTO integrations (provider, name, enabled, config, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "radarr",
                "Movies",
                1,
                json.dumps({"url": "http://radarr:7878", "last_collected_at": now}),
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO media_activities "
            "(integration_id, external_id, occurred_at, provider, instance_name, event_type, "
            "media_type, title, parent_title, season_number, episode_number, quality, bytes, "
            "is_upgrade) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(cursor.lastrowid),
                "existing-import",
                now,
                "radarr",
                "Movies",
                "imported",
                "movie",
                "Existing Movie",
                None,
                None,
                None,
                "WEBDL-2160p",
                2_000,
                0,
            ),
        )
        connection.commit()

    _alembic(backend, tmp_path, "head")

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info('media_activities')")}
        row = connection.execute(
            "SELECT title, provider_media_id, download_id_hash FROM media_activities"
        ).fetchone()
        config = json.loads(connection.execute("SELECT config FROM integrations").fetchone()[0])
    assert {"provider_media_id", "download_id_hash"}.issubset(columns)
    assert row == ("Existing Movie", None, None)
    assert "last_collected_at" not in config


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


def test_docker_state_change_migration_seeds_only_the_current_snapshot(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[1]
    _alembic(backend, tmp_path, "d4a91c28f6b2")
    database = tmp_path / "serversense.db"
    old_time = "2026-09-04 11:59:45"
    current_time = "2026-09-04 12:00:00"
    started_at = "2026-09-01 08:00:00"
    with sqlite3.connect(database) as connection:
        for timestamp in (old_time, current_time):
            connection.execute(
                "INSERT INTO docker_samples "
                "(timestamp, container_id, name, image, status, health, started_at, "
                "cpu_percent, memory_bytes, restart_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    timestamp,
                    "plex",
                    "Plex",
                    "plexinc/pms-docker",
                    "running",
                    "healthy",
                    started_at,
                    1.0,
                    1024,
                    0,
                ),
            )
        connection.commit()

    _alembic(backend, tmp_path, "head")

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info('docker_samples')")}
        values = connection.execute(
            "SELECT timestamp, state_changed_at FROM docker_samples ORDER BY timestamp"
        ).fetchall()
    assert "state_changed_at" in columns
    assert values == [(old_time, None), (current_time, started_at)]
