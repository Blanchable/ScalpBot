from pathlib import Path

import pytest

from app.config.secrets import SecretStore


def test_secret_store_uses_key_files(tmp_path: Path):
    store = SecretStore(tmp_path)
    store.save_credentials("k")

    assert store.app_key_path.suffix == ".key"
    assert store.secret_path.suffix == ".key"
    assert store.app_key_path.exists()
    assert store.secret_path.exists()

    api_key, api_secret = store.load_credentials()
    assert api_key == "k"
    assert api_secret == ""


def test_secret_store_allows_browsed_key_file(tmp_path: Path):
    store = SecretStore(tmp_path)
    custom_key = tmp_path / "nested" / "my_custom.key"
    custom_key.parent.mkdir(parents=True, exist_ok=True)
    custom_key.write_text("kalshi-secret", encoding="utf-8")

    store.set_key_file(custom_key)
    store.save_credentials("ak")

    assert store.key_path == custom_key.resolve()
    assert store.read_secret_key() == "kalshi-secret"


def test_secret_store_rejects_non_key_extension(tmp_path: Path):
    store = SecretStore(tmp_path)
    with pytest.raises(ValueError):
        store.set_key_file(tmp_path / "bad.txt")
