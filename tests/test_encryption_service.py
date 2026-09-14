import pytest
from cryptography.fernet import Fernet

from app.models import EnvironmentVariable
from app.services.deployment_service import DeploymentService
from app.services.encryption_service import EncryptionService, EncryptionServiceError


def test_secret_values_round_trip_without_storing_plaintext():
    service = EncryptionService(Fernet.generate_key().decode())
    encrypted = service.encrypt("database-password")
    assert encrypted != "database-password"
    assert service.decrypt(encrypted) == "database-password"


def test_invalid_key_and_invalid_ciphertext_are_rejected():
    with pytest.raises(EncryptionServiceError):
        EncryptionService("invalid")
    service = EncryptionService(Fernet.generate_key().decode())
    with pytest.raises(EncryptionServiceError):
        service.decrypt("not encrypted")


def test_deployment_resolves_secret_only_immediately_before_container_start():
    encryption = EncryptionService(Fernet.generate_key().decode())
    environment = type(
        "Environment",
        (),
        {
            "variables": [
                EnvironmentVariable(key="APP_ENV", value="production", is_secret=False),
                EnvironmentVariable(
                    key="DATABASE_PASSWORD",
                    value=encryption.encrypt("private"),
                    is_secret=True,
                ),
            ]
        },
    )()
    assert DeploymentService.environment_values(environment, encryption) == {
        "APP_ENV": "production",
        "DATABASE_PASSWORD": "private",
    }
