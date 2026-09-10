from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import keyring
from keyring.errors import KeyringError

from app.models import Account, Profile
from app.paths import app_data_dir, default_download_dir


SERVICE_NAME = "Happitv"
LEGACY_SERVICE_NAME = "IPTVOfflineFetcher"


@dataclass(slots=True)
class AppConfig:
    # Legacy fields maintained for backward compatibility
    server_url: str = ""
    username: str = ""
    download_dir: str = ""
    use_ffmpeg_fallback: bool = True
    max_concurrent_downloads: int = 2

    # Multi-account & Multi-profile
    accounts: list[dict[str, Any]] = field(default_factory=list)
    active_account_id: str = "all"
    profiles: list[dict[str, Any]] = field(default_factory=list)
    active_profile_id: str = "default"

    # Parental Control & Hidden Categories
    parental_pin: str = "0000"
    locked_categories: list[str] = field(default_factory=lambda: ["Adulto", "Adultos", "XXX", "18+", "For Adults", "Porn"])
    global_hidden_categories: list[str] = field(default_factory=list)

    # Player & UI Preferences
    auto_next_episode: bool = True
    remember_position: bool = True
    default_player_speed: float = 1.0
    cinema_mode: bool = False
    hardware_acceleration: bool = True

    def normalized_server(self) -> str:
        return self.server_url.strip().rstrip("/")


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_dir() / "config.json"
        self._session_password = ""

    def load(self) -> tuple[AppConfig, str]:
        config = AppConfig(download_dir=str(default_download_dir()))
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                allowed = set(AppConfig.__dataclass_fields__.keys())
                filtered = {k: v for k, v in raw.items() if k in allowed}
                config = AppConfig(**filtered)
            except (OSError, ValueError, TypeError):
                pass

        if not config.download_dir:
            config.download_dir = str(default_download_dir())

        # Retrieve session password for active account if stored in keyring
        password = self._session_password
        if config.username:
            try:
                password = keyring.get_password(SERVICE_NAME, config.username) or \
                           keyring.get_password(LEGACY_SERVICE_NAME, config.username) or password
            except KeyringError:
                pass

        # Ensure default profile exists
        if not config.profiles:
            default_prof = Profile(
                id="default",
                name="Principal",
                avatar="⚡",
                is_kids=False,
            )
            kids_prof = Profile(
                id="kids",
                name="Kids",
                avatar="🧸",
                is_kids=True,
            )
            config.profiles = [default_prof.to_dict(), kids_prof.to_dict()]
            config.active_profile_id = "default"

        # Migrate legacy single account if accounts list is empty
        if not config.accounts and (config.server_url or config.username):
            legacy_acc = Account(
                id=str(uuid.uuid4()),
                name="Conta Padrão",
                account_type="xtream",
                server_url=config.server_url,
                username=config.username,
                password=password,
                is_active=True,
            )
            config.accounts = [legacy_acc.to_dict()]
            config.active_account_id = legacy_acc.id

        return config, password

    def save(self, config: AppConfig, password: str = "") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(config), indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)
        self._session_password = password
        if config.username and password:
            try:
                keyring.set_password(SERVICE_NAME, config.username, password)
            except KeyringError:
                pass

    def export_backup(self, config: AppConfig, include_passwords: bool = False) -> str:
        data = asdict(config)
        if not include_passwords:
            # Strip passwords from accounts for safety
            for acc in data.get("accounts", []):
                acc["password"] = ""
        return json.dumps(data, indent=2, ensure_ascii=False)

    def import_backup(self, json_content: str) -> AppConfig:
        raw = json.loads(json_content)
        allowed = set(AppConfig.__dataclass_fields__.keys())
        filtered = {k: v for k, v in raw.items() if k in allowed}
        new_config = AppConfig(**filtered)
        self.save(new_config)
        return new_config
