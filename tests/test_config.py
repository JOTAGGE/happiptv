import json
from unittest.mock import patch

from app.config import AppConfig, ConfigStore


def test_password_is_not_written_to_config(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    config = AppConfig("https://example.test", "alice", str(tmp_path / "downloads"))
    with patch("app.config.keyring.set_password") as setter:
        store.save(config, "top-secret")
    assert "top-secret" not in store.path.read_text(encoding="utf-8")
    assert json.loads(store.path.read_text(encoding="utf-8"))["username"] == "alice"
    setter.assert_called_once()


def test_account_secrets_and_pin_are_not_written_to_config(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    config = AppConfig(download_dir=str(tmp_path / "downloads"))
    config.accounts = [{
        "id": "acc-1", "name": "Privada", "account_type": "xtream",
        "server_url": "https://example.test", "username": "alice", "password": "account-secret",
    }]
    config.parental_pin = "4321"
    with patch("app.config.keyring.set_password"):
        store.save(config)
    saved = store.path.read_text(encoding="utf-8")
    assert "account-secret" not in saved
    assert '"parental_pin": ""' in saved
    assert ConfigStore.verify_pin(config, "4321")
    assert not ConfigStore.verify_pin(config, "1234")


def test_import_rejects_oversized_backup(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    oversized = '{"padding":"' + ("x" * (2 * 1024 * 1024)) + '"}'
    try:
        store.import_backup(oversized)
    except ValueError as exc:
        assert "2 MB" in str(exc)
    else:
        raise AssertionError("oversized backup should be rejected")

