from pathlib import Path

import pytest

from app.config.secrets import SecretStore


def test_secret_store_uses_key_files(tmp_path: Path):
    store = SecretStore(tmp_path)
    store.save_credentials("k", environment="paper")

    assert store.app_key_path.suffix == ".key"
    assert store.secret_path.suffix == ".key"
    assert store.app_key_path.exists()
    assert store.secret_path.exists()

    api_key, api_secret = store.load_credentials(environment="paper")
    assert api_key == "k"
    assert api_secret == ""


def test_secret_store_keeps_separate_env_profiles(tmp_path: Path):
    store = SecretStore(tmp_path)

    paper_key = tmp_path / "paper.key"
    prod_key = tmp_path / "prod.key"
    paper_key.write_text("paper-secret", encoding="utf-8")
    prod_key.write_text("prod-secret", encoding="utf-8")

    store.set_environment("paper")
    store.set_key_file(paper_key)
    store.save_credentials("paper-api", environment="paper")

    store.set_environment("production")
    store.set_key_file(prod_key)
    store.save_credentials("prod-api", environment="production")

    paper_api, _ = store.load_credentials(environment="paper")
    assert paper_api == "paper-api"
    assert store.read_secret_key(environment="paper") == "paper-secret"

    prod_api, _ = store.load_credentials(environment="production")
    assert prod_api == "prod-api"
    assert store.read_secret_key(environment="production") == "prod-secret"


def test_secret_store_rejects_non_key_extension(tmp_path: Path):
    store = SecretStore(tmp_path)
    with pytest.raises(ValueError):
        store.set_key_file(tmp_path / "bad.txt")
