import logging
from logging.handlers import RotatingFileHandler

from serversense.config import get_settings


class SuccessfulAccessFilter(logging.Filter):
    """Hide routine successful HTTP requests while retaining client/server errors."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.args, tuple) or len(record.args) < 5:
            return True
        status_value = record.args[4]
        if isinstance(status_value, int):
            status_code = status_value
        elif isinstance(status_value, (str, bytes, bytearray)):
            try:
                status_code = int(status_value)
            except ValueError:
                return True
        else:
            return True
        return status_code >= 400


def configure_logging() -> None:
    settings = get_settings()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler = RotatingFileHandler(
        settings.config_dir / "logs" / "serversense.log", maxBytes=5_000_000, backupCount=5
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    root.addHandler(handler)

    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, SuccessfulAccessFilter) for item in access_logger.filters):
        access_logger.addFilter(SuccessfulAccessFilter())
