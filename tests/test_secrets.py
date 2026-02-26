from pathlib import Path

import pytest

from app.config.secrets import SecretStore


def test_secret_store_uses_key_files(tmp_path: Path):
    store = SecretStore(tmp_path)
    store.save_credentials("k", "s")

    assert store.key_path.suffix == ".key"
    assert store.secret_path.suffix == ".key"
    assert store.key_path.exists()
    assert store.secret_path.exists()

    api_key, api_secret = store.load_credentials()
    assert api_key == "k"
    assert api_secret == "s"


def test_secret_store_allows_browsed_key_file(tmp_path: Path):
    store = SecretStore(tmp_path)
    custom_key = tmp_path / "nested" / "my_custom.key"
    store.set_key_file(custom_key)
    store.save_credentials("ak", "as")

    assert store.key_path == custom_key.resolve()
    assert store.secret_path.name == "my_custom.secret.key"
    assert store.key_path.exists()
    assert store.secret_path.exists()


def test_secret_store_rejects_non_key_extension(tmp_path: Path):
    store = SecretStore(tmp_path)
    with pytest.raises(ValueError):
        store.set_key_file(tmp_path / "bad.txt")
