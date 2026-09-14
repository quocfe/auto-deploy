import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_environment_configuration(monkeypatch):
    monkeypatch.setenv("APP_NAME", "Test Deploy")
    monkeypatch.setenv("DATABASE_HOST", "db.example")
    monkeypatch.setenv("DATABASE_PORT", "5433")
    monkeypatch.setenv("DATABASE_PASSWORD", "p@ss:/%word")
    settings = Settings(_env_file=None)
    assert settings.app_name == "Test Deploy"
    assert settings.database_url.host == "db.example"
    assert settings.database_url.port == 5433
    assert settings.database_url.password == "p@ss:/%word"
    assert "p@ss:/%word" not in repr(settings)
    assert "p@ss:/%word" not in str(settings.database_url)


@pytest.mark.parametrize("port", [0, 65536])
def test_invalid_database_port(port):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_port=port)
