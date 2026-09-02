from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)
    server_name: str = Field(default="My Server", min_length=1, max_length=120)
    demo_mode: bool = False


class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    is_admin: bool


class StoragePoint(BaseModel):
    timestamp: datetime
    total_bytes: int
    used_bytes: int
    free_bytes: int
    projected: bool = False


class ForecastWindow(BaseModel):
    window_days: int
    bytes_per_day: float | None
    days_remaining: float | None
    exhaustion_date: datetime | None
    confidence: str
    sample_count: int


class ForecastResponse(BaseModel):
    sampled_at: datetime
    current_total_bytes: int
    current_used_bytes: int
    current_free_bytes: int
    forecasts: list[ForecastWindow]
    recommended_window_days: int | None


class AISettings(BaseModel):
    provider: str = Field(default="disabled", pattern="^(disabled|ollama|openai_compatible)$")
    model: str = Field(default="", max_length=200)
    endpoint: str = Field(default="", max_length=2000)
    api_key: str | None = Field(default=None, max_length=500)
    context_window: int = Field(default=4096, ge=1024, le=262144)
    temperature: float = Field(default=0.2, ge=0, le=2)
    timeout_seconds: int = Field(default=120, ge=5, le=600)
    max_tool_calls: int = Field(default=3, ge=1, le=12)
    max_output_tokens: int = Field(default=512, ge=64, le=4096)
    proactive_insights: bool = False
    dashboard_summaries: bool = False


class GeneralSettingsUpdate(BaseModel):
    timezone: str = Field(min_length=1, max_length=100)


class AlertSettings(BaseModel):
    free_percent_threshold: float = Field(default=10, ge=1, le=50)
    forecast_days_threshold: int = Field(default=90, ge=1, le=3650)
    temperature_c_threshold: float = Field(default=50, ge=30, le=90)
    notify_storage_low: bool = True
    notify_forecast_low: bool = True
    notify_disk_smart: bool = True
    notify_disk_temperature: bool = True
    notify_container_stopped: bool = True
    webhook_enabled: bool = False
    webhook_url: str | None = Field(default=None, max_length=2000)
    discord_enabled: bool = False
    discord_webhook_url: str | None = Field(default=None, max_length=2000)
    pushover_enabled: bool = False
    pushover_user_key: str | None = Field(default=None, max_length=200)
    pushover_app_token: str | None = Field(default=None, max_length=200)
    email_enabled: bool = False
    smtp_host: str = Field(default="", max_length=253)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: str = Field(default="starttls", pattern="^(starttls|tls|none)$")
    smtp_username: str | None = Field(default=None, max_length=500)
    smtp_password: str | None = Field(default=None, max_length=500)
    email_from: str = Field(default="", max_length=320)
    email_to: str = Field(default="", max_length=320)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: int | None = None


class ChatResponse(BaseModel):
    conversation_id: int
    message: str
    tools_used: list[str]
    model: str


class MediaIntegrationRequest(BaseModel):
    provider: str = Field(pattern="^(sonarr|radarr)$")
    name: str = Field(min_length=1, max_length=160)
    url: str = Field(min_length=1, max_length=2000)
    api_key: str | None = Field(default=None, max_length=500)
    enabled: bool = True


class DashboardResponse(BaseModel):
    updated_at: datetime | None
    timezone: str
    server: dict[str, Any]
    storage: dict[str, Any]
    system: dict[str, Any]
    disks: list[dict[str, Any]]
    containers: list[dict[str, Any]]
    alerts: list[dict[str, Any]]
    insights: list[dict[str, Any]]
    demo_mode: bool
