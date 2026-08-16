"""Runtime configuration and lazy state-path creation."""

from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def read_env_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                quote = value[0]
                value = value[1:-1]
                if quote == '"':
                    # Inverse of connections._serialize_env_value: only
                    # backslash and double quote are escaped by Botter.
                    unescaped: list[str] = []
                    index = 0
                    while index < len(value):
                        if value[index] == "\\" and index + 1 < len(value) and value[index + 1] in {'\\', '"'}:
                            index += 1
                        unescaped.append(value[index])
                        index += 1
                    value = "".join(unescaped)
            return value
    return None


def read_default_model(config_path: Path) -> str:
    """Resolve Hermes' required explicit session model from model.default."""
    try:
        raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        model = raw.get("model", {}).get("default")
    except (OSError, yaml.YAMLError, AttributeError) as exc:
        raise RuntimeError(f"Unable to read Hermes model.default from {config_path}") from exc
    if not isinstance(model, str) or not model.strip():
        raise RuntimeError(f"Hermes model.default is missing in {config_path}")
    return model.strip()


@dataclass(slots=True)
class Settings:
    state_dir: Path
    hermes_home: Path
    hermes_bin: Path
    gateway_url: str = "http://127.0.0.1:8642"
    host: str = "127.0.0.1"
    port: int = 8674
    version: str = "0.1.0"
    token_override: str | None = None
    api_server_key_override: str | None = None
    docker_bin: Path = Path("docker")

    @classmethod
    def from_env(cls) -> "Settings":
        user_home = Path.home()
        return cls(
            state_dir=Path(os.environ.get("BOTTER_STATE_DIR", user_home / ".botter")),
            hermes_home=Path(os.environ.get("HERMES_HOME", user_home / ".hermes")),
            hermes_bin=Path(os.environ.get("HERMES_BIN", user_home / ".local/bin/hermes")),
            gateway_url=os.environ.get("HERMES_API_URL", "http://127.0.0.1:8642").rstrip("/"),
            host=os.environ.get("BOTTERD_HOST", "127.0.0.1"),
            port=int(os.environ.get("BOTTERD_PORT", "8674")),
            token_override=os.environ.get("BOTTERD_TOKEN"),
            api_server_key_override=os.environ.get("API_SERVER_KEY"),
            docker_bin=Path(os.environ.get("DOCKER_BIN", "/usr/local/bin/docker")),
        )

    @property
    def db_path(self) -> Path:
        return self.state_dir / "botter.db"

    @property
    def token_path(self) -> Path:
        return self.state_dir / "token"

    @property
    def hermes_config_path(self) -> Path:
        return self.hermes_home / "config.yaml"

    @property
    def profiles_dir(self) -> Path:
        return self.hermes_home / "profiles"

    @property
    def hermes_python(self) -> Path:
        return Path(os.environ.get("HERMES_PYTHON", self.hermes_home / "hermes-agent/venv/bin/python"))

    @property
    def google_setup_script(self) -> Path:
        return self.hermes_home / "hermes-agent/skills/productivity/google-workspace/scripts/setup.py"

    @property
    def google_client_secret_path(self) -> Path:
        return self.hermes_home / "google_client_secret.json"

    @property
    def wrapper_dir(self) -> Path:
        return self.hermes_bin.parent

    def prepare_state(self) -> None:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)

    def load_or_create_token(self) -> str:
        if self.token_override:
            return self.token_override
        self.prepare_state()
        if self.token_path.exists():
            metadata = self.token_path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError(f"Botter token path is not a regular file: {self.token_path}")
            os.chmod(self.token_path, 0o600)
            return self.token_path.read_text(encoding="utf-8").strip()
        token = secrets.token_urlsafe(32)
        descriptor = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
        os.chmod(self.token_path, 0o600)
        return token

    def load_api_server_key(self) -> str:
        key = self.api_server_key_override or read_env_value(self.hermes_home / ".env", "API_SERVER_KEY")
        if not key:
            raise RuntimeError("API_SERVER_KEY is missing from the Hermes environment")
        return key


def mock_token() -> str:
    return os.environ.get("BOTTER_MOCK_TOKEN", "mock-token")
