from __future__ import annotations

import json
import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import keyring

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
    parental_pin_hash: str = ""
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

        # Hydrate secrets from the OS credential vault. Plaintext values are accepted
        # only for one-time migration from older releases and are removed on next save.
        for account in config.accounts:
            account_id = str(account.get("id", ""))
            if not account_id:
                continue
            try:
                account["password"] = keyring.get_password(SERVICE_NAME, f"account:{account_id}:password") or account.get("password", "")
                if account.get("account_type") == "m3u":
                    account["m3u_url"] = keyring.get_password(SERVICE_NAME, f"account:{account_id}:m3u_url") or account.get("m3u_url", "")
            except Exception:  # Keyring backends may raise platform-specific errors.
                pass

        # Retrieve session password for active account if stored in keyring
        password = self._session_password
        if config.username:
            try:
                password = keyring.get_password(SERVICE_NAME, config.username) or \
                           keyring.get_password(LEGACY_SERVICE_NAME, config.username) or password
            except Exception:  # Keyring backends may raise platform-specific errors.
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
        if config.parental_pin:
            config.parental_pin_hash = self.hash_pin(config.parental_pin)
            config.parental_pin = ""
        data = asdict(config)
        for account in data.get("accounts", []):
            account_id = str(account.get("id", ""))
            if not account_id:
                continue
            try:
                if account.get("password"):
                    keyring.set_password(SERVICE_NAME, f"account:{account_id}:password", account["password"])
                if account.get("account_type") == "m3u" and account.get("m3u_url"):
                    keyring.set_password(SERVICE_NAME, f"account:{account_id}:m3u_url", account["m3u_url"])
            except Exception:  # Keyring backends may raise platform-specific errors.
                pass
            account["password"] = ""
            if account.get("account_type") == "m3u":
                account["m3u_url"] = ""
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)
        self._session_password = password
        if config.username and password:
            try:
                keyring.set_password(SERVICE_NAME, config.username, password)
            except Exception:  # Keyring backends may raise platform-specific errors.
                pass

    def export_backup(self, config: AppConfig, include_passwords: bool = False) -> str:
        data = asdict(config)
        for acc in data.get("accounts", []):
            acc["password"] = ""
            if acc.get("account_type") == "m3u" and not include_passwords:
                acc["m3u_url"] = ""
        data["parental_pin"] = ""
        data["parental_pin_hash"] = ""
        return json.dumps(data, indent=2, ensure_ascii=False)

    def import_backup(self, json_content: str) -> AppConfig:
        if len(json_content.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("O backup excede o limite de 2 MB.")
        raw = json.loads(json_content)
        if not isinstance(raw, dict):
            raise ValueError("Formato de backup inválido.")
        allowed = set(AppConfig.__dataclass_fields__.keys())
        filtered = {k: v for k, v in raw.items() if k in allowed}
        if not isinstance(filtered.get("accounts", []), list) or not isinstance(filtered.get("profiles", []), list):
            raise ValueError("Contas ou perfis em formato inválido.")
        if len(filtered.get("accounts", [])) > 50 or len(filtered.get("profiles", [])) > 20:
            raise ValueError("O backup contém contas ou perfis demais.")
        for account in filtered.get("accounts", []):
            if not isinstance(account, dict):
                raise ValueError("Conta em formato inválido.")
            account["password"] = ""
        new_config = AppConfig(**filtered)
        self.save(new_config)
        return new_config

    def delete_account_secrets(self, account_id: str) -> None:
        for suffix in ("password", "m3u_url"):
            try:
                keyring.delete_password(SERVICE_NAME, f"account:{account_id}:{suffix}")
            except Exception:  # Keyring backends may raise platform-specific errors.
                pass

    @staticmethod
    def hash_pin(pin: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, 210_000)
        return f"pbkdf2_sha256$210000${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"

    @staticmethod
    def verify_pin(config: AppConfig, candidate: str) -> bool:
        if config.parental_pin_hash:
            try:
                algorithm, rounds, salt_text, digest_text = config.parental_pin_hash.split("$", 3)
                if algorithm != "pbkdf2_sha256":
                    return False
                actual = hashlib.pbkdf2_hmac("sha256", candidate.encode("utf-8"), base64.b64decode(salt_text), int(rounds))
                return hmac.compare_digest(actual, base64.b64decode(digest_text))
            except (ValueError, TypeError):
                return False
        return hmac.compare_digest(candidate, config.parental_pin or "0000")
