from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SERVERSENSE_", env_file=".env")

    config_dir: Path = Path("/config")
    secret_key: str = Field(default="development-only-change-me", min_length=16)
    demo_mode: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    session_hours: int = Field(default=24, ge=1, le=720)
    metrics_interval_seconds: int = Field(default=30, ge=30)
    active_metrics_interval_seconds: int = Field(default=5, ge=2, le=30)
    docker_interval_seconds: int = Field(default=30, ge=15)
    active_docker_interval_seconds: int = Field(default=15, ge=5, le=30)
    disk_interval_seconds: int = Field(default=900, ge=300)
    storage_interval_seconds: int = Field(default=3600, ge=300)
    retention_days: int = Field(default=365, ge=7)
    array_path: Path = Path("/mnt/user")
    docker_socket: str = "unix:///var/run/docker.sock"
    secure_cookies: bool = False

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.config_dir / 'serversense.db'}"

    def ensure_directories(self) -> None:
        for name in ("", "logs", "models", "backups", "settings"):
            (self.config_dir / name).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
