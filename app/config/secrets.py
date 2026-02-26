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
        # Keep encryption key material in a dedicated .key file.
        self.key_path = base_dir / "credentials.key"
        # Store encrypted credential payload in a .key file as requested.
        self.secret_path = base_dir / "credentials.secret.key"

    def set_key_file(self, key_file: Path) -> None:
        key_file = key_file.expanduser().resolve()
        if key_file.suffix.lower() != ".key":
            raise ValueError("Secret key path must use a .key file")
        key_file.parent.mkdir(parents=True, exist_ok=True)
        self.key_path = key_file
        self.secret_path = key_file.with_name(f"{key_file.stem}.secret.key")

    def _migrate_legacy_paths(self) -> None:
        legacy_secret_path = self.base_dir / "credentials.enc"
        if legacy_secret_path.exists() and not self.secret_path.exists():
            legacy_secret_path.replace(self.secret_path)

    def _load_or_create_key(self) -> bytes:
        self._migrate_legacy_paths()
        if self.key_path.exists():
            return self.key_path.read_bytes()
        key = Fernet.generate_key() if Fernet else base64.urlsafe_b64encode(b"local-dev-key-material-32bytes!!")
        self.key_path.write_bytes(key)
        return key

    def _encrypt(self, key: bytes, payload: bytes) -> bytes:
        if Fernet:
            return Fernet(key).encrypt(payload)
        return base64.urlsafe_b64encode(payload)

    def _decrypt(self, key: bytes, payload: bytes) -> bytes:
        if Fernet:
            return Fernet(key).decrypt(payload)
        return base64.urlsafe_b64decode(payload)

    def save_credentials(self, api_key: str, api_secret: str) -> None:
        key = self._load_or_create_key()
        payload = json.dumps({"api_key": api_key, "api_secret": api_secret}).encode()
        token = self._encrypt(key, payload)
        self.secret_path.write_bytes(token)

    def load_credentials(self) -> tuple[str, str]:
        self._migrate_legacy_paths()
        if not self.secret_path.exists():
            return "", ""
        key = self._load_or_create_key()
        payload = self._decrypt(key, self.secret_path.read_bytes())
        data = json.loads(payload.decode())
        return data.get("api_key", ""), data.get("api_secret", "")
