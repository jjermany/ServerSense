import logging
from logging.handlers import RotatingFileHandler

from serversense.config import get_settings


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
