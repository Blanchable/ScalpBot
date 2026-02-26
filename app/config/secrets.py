from __future__ import annotations

import base64
import json
from pathlib import Path

try:
    from cryptography.fernet import Fernet
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    Fernet = None


class SecretStore:
    VALID_ENVS = ("paper", "production")

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.app_key_path = base_dir / "credentials_store.key"
        self.secret_path = base_dir / "credentials.secret.key"
        self.current_environment = "paper"
        self._profiles = self._default_profiles()
        self.key_path = Path(self._profiles[self.current_environment]["secret_key_file"])

    def _default_profiles(self) -> dict[str, dict[str, str]]:
        return {
            "paper": {
                "api_key": "",
                "secret_key_file": str((self.base_dir / "paper_credentials.key").resolve()),
            },
            "production": {
                "api_key": "",
                "secret_key_file": str((self.base_dir / "production_credentials.key").resolve()),
            },
        }

    def set_environment(self, environment: str) -> None:
        if environment not in self.VALID_ENVS:
            raise ValueError(f"Unknown environment: {environment}")
        self.current_environment = environment
        self.key_path = Path(self._profiles[environment]["secret_key_file"]) 

    def set_key_file(self, key_file: Path) -> None:
        key_file = key_file.expanduser().resolve()
        if key_file.suffix.lower() != ".key":
            raise ValueError("Secret key path must use a .key file")
        self.key_path = key_file
        self._profiles[self.current_environment]["secret_key_file"] = str(key_file)

    def read_secret_key(self, environment: str | None = None) -> str:
        env = environment or self.current_environment
        self.set_environment(env)
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

    def save_credentials(self, api_key: str, environment: str | None = None) -> None:
        env = environment or self.current_environment
        self.set_environment(env)
        self._profiles[env]["api_key"] = api_key
        self._profiles[env]["secret_key_file"] = str(self.key_path)
        key = self._load_or_create_store_key()
        payload = json.dumps({"profiles": self._profiles}).encode()
        token = self._encrypt(key, payload)
        self.secret_path.write_bytes(token)

    def load_credentials(self, environment: str | None = None) -> tuple[str, str]:
        if not self.secret_path.exists():
            env = environment or self.current_environment
            self.set_environment(env)
            return "", ""
        key = self._load_or_create_store_key()
        payload = self._decrypt(key, self.secret_path.read_bytes())
        data = json.loads(payload.decode())

        profiles = data.get("profiles")
        if isinstance(profiles, dict):
            for env in self.VALID_ENVS:
                if env in profiles and isinstance(profiles[env], dict):
                    self._profiles[env].update(
                        {
                            "api_key": profiles[env].get("api_key", ""),
                            "secret_key_file": profiles[env].get("secret_key_file", self._profiles[env]["secret_key_file"]),
                        }
                    )
        else:
            # backward compatibility with previous single-profile payload
            self._profiles["paper"]["api_key"] = data.get("api_key", "")
            self._profiles["paper"]["secret_key_file"] = data.get("secret_key_file", self._profiles["paper"]["secret_key_file"])

        env = environment or self.current_environment
        self.set_environment(env)
        return self._profiles[env].get("api_key", ""), ""
