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

