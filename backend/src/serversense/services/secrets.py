import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from serversense.config import get_settings


def _cipher() -> Fernet:
    digest = hashlib.sha256(get_settings().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _cipher().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return _cipher().decrypt(value.encode()).decode()
    except InvalidToken:
        return ""
