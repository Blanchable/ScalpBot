from pathlib import Path

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
