import pytest


def test_ui_starts(qtbot, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from app.ui.main_window import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.windowTitle() == "Happiptv"
    assert window.pages.count() >= 4
