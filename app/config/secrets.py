from __future__ import annotations

import base64
import json
from pathlib import Path

try:
    from cryptography.fernet import Fernet
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    Fernet = None


class SecretStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        # Encryption key for locally stored UI settings/credentials metadata.
        self.app_key_path = base_dir / "credentials_store.key"
        self.secret_path = base_dir / "credentials.secret.key"
        # Kalshi secret key file path selected by the user.
        self.key_path = base_dir / "credentials.key"

    def set_key_file(self, key_file: Path) -> None:
        key_file = key_file.expanduser().resolve()
        if key_file.suffix.lower() != ".key":
            raise ValueError("Secret key path must use a .key file")
        self.key_path = key_file

    def read_secret_key(self) -> str:
        if not self.key_path.exists():
            raise FileNotFoundError(f"Secret key file not found: {self.key_path}")
        return self.key_path.read_text(encoding="utf-8").strip()

    def _load_or_create_store_key(self) -> bytes:
        if self.app_key_path.exists():
            return self.app_key_path.read_bytes()
        key = Fernet.generate_key() if Fernet else base64.urlsafe_b64encode(b"local-dev-key-material-32bytes!!")
        self.app_key_path.write_bytes(key)
        return key

    def _encrypt(self, key: bytes, payload: bytes) -> bytes:
        if Fernet:
            return Fernet(key).encrypt(payload)
        return base64.urlsafe_b64encode(payload)

    def _decrypt(self, key: bytes, payload: bytes) -> bytes:
        if Fernet:
            return Fernet(key).decrypt(payload)
        return base64.urlsafe_b64decode(payload)

    def save_credentials(self, api_key: str, api_secret: str = "") -> None:
        key = self._load_or_create_store_key()
        payload = json.dumps({"api_key": api_key, "secret_key_file": str(self.key_path)}).encode()
        token = self._encrypt(key, payload)
        self.secret_path.write_bytes(token)

    def load_credentials(self) -> tuple[str, str]:
        if not self.secret_path.exists():
            return "", ""
        key = self._load_or_create_store_key()
        payload = self._decrypt(key, self.secret_path.read_bytes())
        data = json.loads(payload.decode())
        configured = data.get("secret_key_file", "")
        if configured:
            configured_path = Path(configured)
            if configured_path.suffix.lower() == ".key":
                self.key_path = configured_path
        return data.get("api_key", ""), ""
