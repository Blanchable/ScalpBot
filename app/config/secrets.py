from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet


class SecretStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.key_path = base_dir / "credentials.key"
        self.secret_path = base_dir / "credentials.enc"

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            return self.key_path.read_bytes()
        key = Fernet.generate_key()
        self.key_path.write_bytes(key)
        return key

    def save_credentials(self, api_key: str, api_secret: str) -> None:
        key = self._load_or_create_key()
        payload = json.dumps({"api_key": api_key, "api_secret": api_secret}).encode()
        token = Fernet(key).encrypt(payload)
        self.secret_path.write_bytes(token)

    def load_credentials(self) -> tuple[str, str]:
        if not self.secret_path.exists():
            return "", ""
        key = self._load_or_create_key()
        payload = Fernet(key).decrypt(self.secret_path.read_bytes())
        data = json.loads(payload.decode())
        return data.get("api_key", ""), data.get("api_secret", "")
