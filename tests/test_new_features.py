from pathlib import Path
import json
import pytest
from PySide6.QtCore import Qt

from app.catalog_manager import CatalogManager
from app.models import CustomPlaylist, MediaItem, Profile
from app.player import VideoPlayerWidget
from app.ui.epg_widget import EPGWidget


def test_custom_playlist_model_and_profile_isolation() -> None:
    pl1 = CustomPlaylist(
        id="pl-1",
        name="Ação Explosiva",
        emoji="💥",
        color="#e74c3c",
        item_ids=["movie-1", "movie-2", "live-1"],
    )
    raw = pl1.to_dict()
    assert raw["id"] == "pl-1"
    assert raw["name"] == "Ação Explosiva"
    assert raw["emoji"] == "💥"
    assert raw["color"] == "#e74c3c"
    assert len(raw["item_ids"]) == 3

    restored = CustomPlaylist.from_dict(raw)
    assert restored.name == pl1.name
    assert restored.color == pl1.color
    assert restored.item_ids == ["movie-1", "movie-2", "live-1"]

    # Profile custom_playlists isolation
    prof1 = Profile(id="prof-1", name="User 1", custom_playlists=[pl1.to_dict()])
    prof2 = Profile(id="prof-2", name="User 2", custom_playlists=[])

    assert len(prof1.custom_playlists) == 1
    assert len(prof2.custom_playlists) == 0
    assert prof1.custom_playlists[0]["name"] == "Ação Explosiva"


def test_epg_search_does_not_autoplay_or_select_first_channel(qtbot) -> None:
    widget = EPGWidget()
    qtbot.addWidget(widget)

    channels = [
        MediaItem(id="c1", kind="live", title="ESPN Brasil", stream_id=1, category_name="Esportes"),
        MediaItem(id="c2", kind="live", title="SporTV", stream_id=2, category_name="Esportes"),
        MediaItem(id="c3", kind="live", title="CNN Brasil", stream_id=3, category_name="Notícias"),
    ]
    widget.set_channels(channels)

    # Track if channel_selected was emitted
    played_channels = []
    widget.channel_selected.connect(played_channels.append)

    # Typing into search input
    widget.search_input.setText("SporTV")

    # Assert: channel list filtered to SporTV
    assert widget.channels_list.count() == 1
    # Critical: typing should NOT automatically select/play the channel!
    assert len(played_channels) == 0

    # User explicitly clicks/activates
    item = widget.channels_list.item(0)
    widget._on_item_activated(item)
    assert len(played_channels) == 1
    assert played_channels[0].title == "SporTV"


def test_player_collapse_controls_and_download_signal(qtbot) -> None:
    player = VideoPlayerWidget()
    qtbot.addWidget(player)
    player.show()

    # Test collapse controls
    assert player.is_controls_collapsed is False
    assert player.controls_bar.isHidden() is False

    player.toggle_controls_bar()
    assert player.is_controls_collapsed is True
    assert player.controls_bar.isHidden() is True
    assert player.btn_expand.isHidden() is False

    player.toggle_controls_bar()
    assert player.is_controls_collapsed is False
    assert player.controls_bar.isHidden() is False
    assert player.btn_expand.isHidden() is True

    # Test download signal
    download_calls = []
    player.download_requested.connect(lambda cid, k: download_calls.append((cid, k)))

    player.load_media("http://test.mp4", content_id="movie-99", kind="movie")
    assert player.btn_download.isHidden() is False

    player._on_download_clicked()
    assert len(download_calls) == 1
    assert download_calls[0] == ("movie-99", "movie")



def test_cached_catalog_populated_immediately(tmp_path: Path) -> None:
    cache_file = tmp_path / "catalog_cache.json"
    catalog = CatalogManager(cache_file=cache_file)
    catalog.add_items([
        MediaItem(id="m1", kind="movie", title="Interestelar", stream_id=10, category_name="Ficção"),
        MediaItem(id="s1", kind="series", title="Succession", stream_id=20, category_name="Drama"),
        MediaItem(id="l1", kind="live", title="HBO", stream_id=30, category_name="Cinema"),
    ])

    # Restart simulated by creating new CatalogManager pointing to the same cache file
    fresh_catalog = CatalogManager(cache_file=cache_file)
    assert len(fresh_catalog.items) == 3
    assert "m1" in fresh_catalog.items
    assert "s1" in fresh_catalog.items
    assert "l1" in fresh_catalog.items
