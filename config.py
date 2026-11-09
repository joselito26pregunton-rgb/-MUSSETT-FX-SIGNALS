"""Configuration is read from the environment; secret values are never logged."""
import re
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: SecretStr
    webhook_secret: SecretStr | None = None
    api_access_key: SecretStr | None = None
    recovery_operator_credentials: dict[str, SecretStr] = Field(default_factory=dict)
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    telegram_polling_enabled: bool = True
    worker_enabled: bool = True
    worker_interval: float = 1.0
    webhook_max_body_bytes: int = Field(default=16384, ge=1024, le=1048576)
    webhook_requests_per_minute: int = Field(default=120, ge=1, le=10000)
    webhook_max_age_seconds: int = Field(default=300, ge=220, le=3600)
    webhook_future_tolerance_seconds: int = Field(default=60, ge=0, le=300)

    @model_validator(mode="after")
    def validate_recovery_operator_credentials(self):
        for operator_id, credential in self.recovery_operator_credentials.items():
            if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,100}", operator_id):
                raise ValueError("Los identificadores de operadores de recuperación no son válidos")
            if len(credential.get_secret_value()) < 32:
                raise ValueError("Cada credencial de operador de recuperación debe tener al menos 32 caracteres")
        return self

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)
