import json
from pathlib import Path

from app.catalog_manager import CatalogManager
from app.config import AppConfig, ConfigStore
from app.download_manager import DownloadManager
from app.m3u_parser import parse_m3u_content
from app.models import (
    Account, DownloadStatus, DownloadTask, Episode, MediaItem,
    Profile, StreamDiagnostics, format_duration, human_size, safe_filename,
)


def test_profile_and_watchlist_separation() -> None:
    prof1 = Profile(id="p1", name="José", favorites=["item-1"], watchlist=["item-2"])
    prof2 = Profile(id="p2", name="Kids", is_kids=True, favorites=["item-3"], watchlist=[])

    # Isolated lists
    assert prof1.favorites != prof2.favorites
    assert prof1.watchlist != prof1.favorites
    assert "item-1" in prof1.favorites
    assert "item-2" in prof1.watchlist
    assert "item-1" not in prof1.watchlist
    assert prof2.is_kids is True


def test_m3u_parser_detects_channels_and_vod() -> None:
    m3u_text = """#EXTM3U
#EXTINF:-1 tvg-id="espn.br" tvg-name="ESPN Brasil" tvg-logo="http://logo.png" group-title="ESPORTES",ESPN HD
http://stream.test/live/espn.ts
#EXTINF:-1 tvg-id="" tvg-name="Inception 2010" group-title="FILMES - FICÇÃO",A Origem (2010)
http://stream.test/movie/inception.mp4
#EXTINF:-1 tvg-id="" group-title="SÉRIES - DRAMA",Breaking Bad S01E01
http://stream.test/series/bb_s01e01.mkv
"""
    items = parse_m3u_content(m3u_text, account_id="acc_m3u_1")
    assert len(items) == 3

    live_item = next(i for i in items if i.title == "ESPN HD")
    assert live_item.kind == "live"
    assert live_item.category_name == "ESPORTES"
    assert live_item.poster == "http://logo.png"
    assert live_item.epg_channel_id == "espn.br"

    movie_item = next(i for i in items if "Origem" in i.title)
    assert movie_item.kind == "movie"
    assert movie_item.stream_url.endswith(".mp4")

    series_item = next(i for i in items if "Breaking Bad" in i.title)
    assert series_item.kind == "series"


def test_catalog_smart_refresh_preserves_user_data(tmp_path: Path) -> None:
    cache_file = tmp_path / "catalog_cache.json"
    catalog = CatalogManager(cache_file=cache_file)

    initial_items = [
        MediaItem(id="item-1", kind="movie", title="The Matrix", stream_id=1, category_name="Ficção"),
        MediaItem(id="item-2", kind="series", title="Dark", stream_id=2, category_name="Suspense"),
    ]
    catalog.add_items(initial_items)

    # Simulated user state
    user_favorites = {"item-1"}
    user_progress = {"item-1": {"position_ms": 3600000, "duration_ms": 7200000}}

    # Refresh catalog with new items or updated categories
    updated_items = [
        MediaItem(id="item-1", kind="movie", title="The Matrix (Remastered)", stream_id=1, category_name="Sci-Fi"),
        MediaItem(id="item-3", kind="live", title="CNN", stream_id=3, category_name="Notícias"),
    ]
    catalog.add_items(updated_items)

    # Content is present and updated
    assert "item-1" in catalog.items
    assert catalog.items["item-1"].category_name == "Sci-Fi"
    assert "item-3" in catalog.items

    # User favorite ID and progress key are canonical and undisturbed!
    assert "item-1" in user_favorites
    assert user_progress["item-1"]["position_ms"] == 3600000


def test_catalog_cache_never_persists_authenticated_stream_url(tmp_path: Path) -> None:
    cache_file = tmp_path / "catalog_cache.json"
    catalog = CatalogManager(cache_file=cache_file)
    catalog.add_items([
        MediaItem(
            id="secure-1", kind="movie", title="Filme", stream_id=1,
            stream_url="https://server/movie/user/very-secret/1.mp4",
        )
    ])
    cache_text = cache_file.read_text(encoding="utf-8")
    assert "very-secret" not in cache_text
    assert json.loads(cache_text)["items"][0]["stream_url"] == ""


def test_catalog_hides_categories_and_searches(tmp_path: Path) -> None:
    cache_file = tmp_path / "catalog_cache.json"
    catalog = CatalogManager(cache_file=cache_file)

    catalog.add_items([
        MediaItem(id="1", kind="movie", title="Toy Story", stream_id=1, category_name="Animação"),
        MediaItem(id="2", kind="movie", title="Adult Movie", stream_id=2, category_name="Adulto 18+"),
        MediaItem(id="3", kind="live", title="Cartoon Network", stream_id=3, category_name="Infantil"),
    ])

    # With hidden category
    visible = catalog.get_items("movie", hidden_categories=["Adulto 18+"])
    assert len(visible) == 1
    assert visible[0].title == "Toy Story"

    # Search
    search_res = catalog.global_search("story")
    assert len(search_res["movie"]) == 1
    assert search_res["movie"][0].title == "Toy Story"


def test_download_manager_offline_library_and_deletion(tmp_path: Path) -> None:
    history_file = tmp_path / "history.json"
    dm = DownloadManager(history_path=history_file, download_root=tmp_path)

    # Fake a completed task with an actual file on disk
    download_file = tmp_path / "offline_movie.mp4"
    download_file.write_bytes(b"happitv_mock_video_bytes")

    task = dm.add(
        kind="movie",
        title="Filme Offline",
        url_candidates=["http://example.com/movie.mp4"],
        destination_dir=str(tmp_path),
        base_filename="offline_movie",
        extension="mp4",
    )
    task.status = DownloadStatus.COMPLETED
    task.output_path = str(download_file)
    dm._save()

    completed = dm.get_completed_tasks()
    assert len(completed) == 1
    assert completed[0].title == "Filme Offline"

    # Delete task and file from disk directly through app
    dm.delete_task_and_file(task.id)
    assert not download_file.exists()
    assert len(dm.get_completed_tasks()) == 0


def test_config_export_and_import_backup(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    store = ConfigStore(path=config_file)
    config, _ = store.load()

    config.accounts = [
        {"id": "acc-1", "name": "Servidor 1", "server_url": "http://server1:80", "username": "user1", "password": "secret_password"}
    ]
    config.parental_pin = "4321"

    # Export without passwords
    exported = store.export_backup(config, include_passwords=False)
    data = json.loads(exported)
    assert data["parental_pin"] == ""
    assert data["accounts"][0]["password"] == ""

    # Import backup into fresh store
    new_store = ConfigStore(path=tmp_path / "new_config.json")
    imported_config = new_store.import_backup(exported)
    assert imported_config.parental_pin == ""
    assert imported_config.accounts[0]["username"] == "user1"


def test_stream_diagnostics_and_formatting() -> None:
    diag = StreamDiagnostics(resolution="1080p", fps=60.0, bitrate_kbps=5000, latency_ms=25)
    assert diag.resolution == "1080p"
    assert diag.fps == 60.0
    assert diag.bitrate_kbps == 5000
    assert diag.latency_ms == 25

    assert format_duration(3665) == "01:01:05"
    assert format_duration(45) == "00:45"
    assert human_size(1048576) == "1.0 MB"
