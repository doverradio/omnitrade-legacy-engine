from __future__ import annotations

from pathlib import Path

from app.config import Settings


def test_settings_accepts_canonical_kraken_env_names() -> None:
    settings = Settings.model_validate(
        {
            "KRAKEN_API_KEY": "kraken-key",
            "KRAKEN_API_SECRET": "kraken-secret",
        }
    )

    assert settings.kraken_api_key is not None
    assert settings.kraken_api_key.get_secret_value() == "kraken-key"
    assert settings.kraken_api_secret is not None
    assert settings.kraken_api_secret.get_secret_value() == "kraken-secret"


def test_settings_accepts_legacy_kraken_env_aliases() -> None:
    settings = Settings.model_validate(
        {
            "OT_KRAKEN_API_KEY": "legacy-key",
            "OT_KRAKEN_API_SECRET": "legacy-secret",
        }
    )

    assert settings.kraken_api_key is not None
    assert settings.kraken_api_key.get_secret_value() == "legacy-key"
    assert settings.kraken_api_secret is not None
    assert settings.kraken_api_secret.get_secret_value() == "legacy-secret"


def test_gitignore_keeps_env_files_untracked_but_env_example_committed() -> None:
    root = Path(__file__).resolve().parents[4]
    gitignore_text = (root / ".gitignore").read_text()

    assert ".env" in gitignore_text
    assert ".env.*" in gitignore_text
    assert "!.env.example" in gitignore_text


def test_root_env_example_documents_canonical_kraken_names_only() -> None:
    root = Path(__file__).resolve().parents[4]
    env_example = (root / ".env.example").read_text()

    assert "KRAKEN_API_KEY=" in env_example
    assert "KRAKEN_API_SECRET=" in env_example
    assert "OT_KRAKEN_API_KEY" not in env_example
    assert "OT_KRAKEN_API_SECRET" not in env_example