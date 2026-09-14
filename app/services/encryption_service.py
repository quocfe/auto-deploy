from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class EncryptionServiceError(RuntimeError):
    pass


class EncryptionService:
    """Encrypt values at rest; plaintext exists only while a deployment starts."""

    def __init__(self, master_key: str | None = None):
        key = master_key
        if key is None:
            configured = get_settings().app_master_key
            key = configured.get_secret_value() if configured else None
        if not key:
            raise EncryptionServiceError("APP_MASTER_KEY is required for secret variables")
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (TypeError, ValueError):
            raise EncryptionServiceError("APP_MASTER_KEY must be a valid Fernet key") from None

    def encrypt(self, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Secret value must be text")
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError):
            raise EncryptionServiceError("Stored secret cannot be decrypted") from None
