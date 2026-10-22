"""Complete provider inventories and deterministic, path-free file change evidence."""

import json
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete
from sqlalchemy.orm import Session

from serversense.models import Integration, MediaInventoryChange, MediaInventorySnapshot

MAX_FILES = 50_000
MAX_SERIES = 2_000
MAX_RECORDS = 200_000
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_COLLECTION_SECONDS = 120


class InventoryFile(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    file_id: int = Field(gt=0)
    media_ids: list[int] = Field(min_length=1, max_length=100)
    media_type: str = Field(pattern="^(movie|episode)$")
    title: str = Field(max_length=300)
    series: str | None = Field(default=None, max_length=300)
    episodes: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    quality: str | None = Field(default=None, max_length=100)
    bytes: int | None = Field(default=None, ge=0, le=10_000_000_000_000_000)


def fetch_inventory(integration: Integration) -> dict[str, dict[str, Any]]:
    # The collector remains the only network boundary. No paths/raw payloads survive.
    from serversense.services.integrations import _integer, _mapping, _request, _text

    started = time.monotonic()
    files: dict[str, dict[str, Any]] = {}
    normalized_bytes = 0
    scanned_records = 0

    def request(path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        nonlocal scanned_records
        if time.monotonic() - started >= MAX_COLLECTION_SECONDS:
            raise ValueError("inventory_runtime_limit")
        payload = _request(integration, path, params)
        if not isinstance(payload, list) or len(payload) > MAX_FILES:
            raise ValueError("incomplete_inventory")
        if any(not isinstance(row, dict) for row in payload):
            raise ValueError("invalid_inventory")
        scanned_records += len(payload)
        if scanned_records > MAX_RECORDS:
            raise ValueError("inventory_record_limit")
        return payload

    def add(raw_file: dict[str, Any], media: dict[str, Any]) -> None:
        nonlocal normalized_bytes
        file_id = _integer(raw_file.get("id"))
        media_id = _integer(media.get("id"))
        if not file_id or not media_id:
            raise ValueError("missing_inventory_identity")
        quality = _mapping(_mapping(raw_file.get("quality")).get("quality"))
        key = str(file_id)
        if key in files:
            # A multi-episode file counts once, with all episode identities retained.
            current = files[key]
            previous_bytes = len(json.dumps(current).encode("utf-8"))
            if integration.provider != "sonarr" or current["series"] != media["series_title"]:
                raise ValueError("duplicate_inventory_identity")
            if media_id in current["media_ids"]:
                raise ValueError("duplicate_inventory_identity")
            if current["bytes"] != _integer(raw_file.get("size")) or current["quality"] != (
                _text(quality.get("name"), 100) or None
            ):
                raise ValueError("inconsistent_episode_file")
            current["media_ids"].append(media_id)
            current["episodes"].append(media["episode_identity"])
            InventoryFile.model_validate(current)
            normalized_bytes += len(json.dumps(current).encode("utf-8")) - previous_bytes
            if normalized_bytes > MAX_SNAPSHOT_BYTES:
                raise ValueError("inventory_size_limit")
            return
        item = InventoryFile(
            file_id=file_id,
            media_ids=[media_id],
            media_type="movie" if integration.provider == "radarr" else "episode",
            title=_text(media.get("title"), 300) or "Unknown title",
            series=media.get("series_title"),
            episodes=[media["episode_identity"]] if "episode_identity" in media else [],
            quality=_text(quality.get("name"), 100) or None,
            bytes=_integer(raw_file.get("size")),
        )
        files[key] = item.model_dump()
        normalized_bytes += len(json.dumps(files[key]).encode("utf-8")) + len(key) + 8
        if normalized_bytes > MAX_SNAPSHOT_BYTES:
            raise ValueError("inventory_size_limit")
        if len(files) > MAX_FILES:
            raise ValueError("inventory_file_limit")

    if integration.provider == "radarr":
        seen_movies: set[int] = set()
        for movie in request("movie"):
            movie_id = _integer(movie.get("id"))
            if (
                not movie_id
                or movie_id in seen_movies
                or not isinstance(movie.get("hasFile"), bool)
            ):
                raise ValueError("invalid_movie_inventory")
            seen_movies.add(movie_id)
            if movie["hasFile"]:
                add(_mapping(movie.get("movieFile")), movie)
    else:
        series = request("series")
        if len(series) > MAX_SERIES:
            raise ValueError("inventory_series_limit")
        seen_series: set[int] = set()
        seen_episodes: set[int] = set()
        for show in series:
            series_id = _integer(show.get("id"))
            if not series_id or series_id in seen_series:
                raise ValueError("invalid_series_inventory")
            seen_series.add(series_id)
            referenced_files: set[int] = set()
            for episode in request("episode", {"seriesId": series_id, "includeEpisodeFile": True}):
                episode_id = _integer(episode.get("id"))
                if not episode_id or episode_id in seen_episodes:
                    raise ValueError("duplicate_episode_identity")
                seen_episodes.add(episode_id)
                if not isinstance(episode.get("hasFile"), bool):
                    raise ValueError("invalid_episode_inventory")
                if episode["hasFile"]:
                    episode_file = _mapping(episode.get("episodeFile"))
                    if episode.get("episodeFileId") != episode_file.get("id"):
                        raise ValueError("inconsistent_episode_inventory")
                    referenced_files.add(episode_file["id"])
                    add(
                        episode_file,
                        episode
                        | {
                            "series_title": _text(show.get("title"), 300) or "Unknown series",
                            "episode_identity": {
                                "id": _integer(episode.get("id")),
                                "season": _integer(episode.get("seasonNumber")),
                                "episode": _integer(episode.get("episodeNumber")),
                                "title": _text(episode.get("title"), 300) or "Unknown title",
                            },
                        },
                    )
            manifest = request("episodefile", {"seriesId": series_id})
            manifest_ids = {_integer(item.get("id")) for item in manifest}
            if manifest_ids != referenced_files or len(manifest_ids) != len(manifest):
                raise ValueError("incomplete_episode_inventory")
        # Detect additions/removals of series during a multi-request collection.
        if {_integer(row.get("id")) for row in request("series")} != seen_series:
            raise ValueError("inventory_changed_during_collection")
    for item in files.values():
        item["media_ids"].sort()
        item["episodes"].sort(key=lambda episode: episode["id"])
    if len(json.dumps(files).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError("inventory_size_limit")
    if time.monotonic() - started > MAX_COLLECTION_SECONDS:
        raise ValueError("inventory_runtime_limit")
    return files


def clear_inventory(db: Session, integration_id: int) -> None:
    """A changed source gets a fresh baseline, never a cross-server diff."""
    db.execute(
        delete(MediaInventoryChange).where(MediaInventoryChange.integration_id == integration_id)
    )
    db.execute(
        delete(MediaInventorySnapshot).where(
            MediaInventorySnapshot.integration_id == integration_id
        )
    )


def persist_inventory(
    db: Session, integration_id: int, files: dict[str, dict[str, Any]], captured_at: datetime
) -> None:
    snapshot = db.get(MediaInventorySnapshot, integration_id)
    if snapshot is None:
        db.add(
            MediaInventorySnapshot(
                integration_id=integration_id,
                baseline_at=captured_at,
                captured_at=captured_at,
                files=files,
            )
        )
        return
    previous_at = snapshot.captured_at
    aware_previous = previous_at.replace(tzinfo=UTC) if previous_at.tzinfo is None else previous_at
    if captured_at <= aware_previous:
        return
    old = snapshot.files
    removed = {key: item for key, item in old.items() if key not in files}
    added = {key: item for key, item in files.items() if key not in old}

    def change(kind: str, before: dict | None, after: dict | None) -> None:
        previous_size = before["bytes"] if before else 0
        current_size = after["bytes"] if after else 0
        db.add(
            MediaInventoryChange(
                integration_id=integration_id,
                previous_at=previous_at,
                observed_at=captured_at,
                change_type=kind,
                before=before,
                after=after,
                net_bytes=current_size - previous_size
                if previous_size is not None and current_size is not None
                else None,
            )
        )

    replacements: dict[tuple[int, ...], list[str]] = defaultdict(list)
    for key, item in added.items():
        replacements[tuple(item["media_ids"])].append(key)
    for before in removed.values():
        candidates = replacements[tuple(before["media_ids"])]
        if len(candidates) == 1:
            change("replaced", before, added.pop(candidates.pop()))
        else:
            change("removed", before, None)
    for after in added.values():
        change("added", None, after)
    for key in old.keys() & files.keys():
        if old[key]["media_ids"] != files[key]["media_ids"]:
            change("removed", old[key], None)
            change("added", None, files[key])
        elif old[key]["bytes"] != files[key]["bytes"]:
            change("file_changed", old[key], files[key])
    snapshot.files = files
    snapshot.captured_at = captured_at
