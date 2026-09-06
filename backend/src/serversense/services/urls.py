from urllib.parse import urlsplit


def validate_http_url(value: str) -> str:
    """Validate administrator-configured LAN or internet HTTP endpoints."""
    value = value.strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("HTTP URL must not contain control characters")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and (port is None or 1 <= port <= 65535)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            "URL must be HTTP(S), with a valid host/port and no embedded credentials or fragment"
        )
    return value
