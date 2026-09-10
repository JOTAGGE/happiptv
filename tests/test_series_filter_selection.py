import pytest
from PySide6.QtCore import Qt

from app.models import DownloadStatus, DownloadTask
from app.ui.main_window import (
    MainWindow, matches_search, normalize_text, STREAMING_KEYWORDS,
)


def test_normalize_text_removes_accents_and_case() -> None:
    assert normalize_text("Órfão") == "orfao"
    assert normalize_text("SÉRIE: Globoplay") == "serie: globoplay"
    assert normalize_text("  SHAMELESS  ") == "shameless"


def test_matches_search_typo_and_accent_tolerance() -> None:
    # Exact and case-insensitive
    assert matches_search("shameless", "Shameless")
    assert matches_search("SHAMELESS", "shameless (us)")

    # Typo: extra letter "shamelesse" finds "Shameless"
    assert matches_search("shamelesse", "Shameless")
    assert matches_search("shamelesse", "Shameless: Em Família")

    # Accents
    assert matches_search("orfaos da terra", "Órfãos da Terra")

    # Tokens / words out of order or multi-word
    assert matches_search("game thrones", "Game of Thrones")

    # Category in combined target
    assert matches_search("netflix", "Black Mirror NETFLIX")
    assert not matches_search("dark", "The Last Kingdom")


def test_global_search_has_dedicated_results_view(qtbot, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    window = MainWindow()
    qtbot.addWidget(window)

    from app.models import MediaItem
    window.catalog.add_items([
        MediaItem(id="m1", kind="movie", title="Cidade de Deus", stream_id=1, category_name="Nacionais"),
        MediaItem(id="s1", kind="series", title="Cidade Invisível", stream_id=2, category_name="Netflix"),
        MediaItem(id="l1", kind="live", title="Canal Cidade", stream_id=3, category_name="Notícias"),
    ])
    window.search_input.setText("cidade")
    window._execute_global_search()

    assert window.view_stack.currentWidget() is window.view_search
    assert window.global_results.topLevelItemCount() == 3
    assert "3 resultados" in window.search_summary.text()

    window.search_input.clear()
    assert window.view_stack.currentWidget() is not window.view_search


def test_streaming_keywords_coverage() -> None:
    assert "netflix" in STREAMING_KEYWORDS["netflix"]
    assert "prime" in STREAMING_KEYWORDS["prime"]
    assert "globo" in STREAMING_KEYWORDS["globo"]
    assert "apple" in STREAMING_KEYWORDS["apple"]
    assert "hbo" in STREAMING_KEYWORDS["hbo"]
    assert "disney" in STREAMING_KEYWORDS["disney"]


def test_series_streaming_and_search_filtering(qtbot, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    window = MainWindow()
    qtbot.addWidget(window)

    window.series_data = [
        {"name": "Shameless", "category_id": "10", "category_name": "HBO Max", "series_id": 101},
        {"name": "Ted Lasso", "category_id": "20", "category_name": "Apple TV+", "series_id": 102},
        {"name": "Stranger Things", "category_id": "30", "category_name": "Netflix", "series_id": 103},
        {"name": "Sob Pressão", "category_id": "40", "category_name": "Globoplay", "series_id": 104},
    ]

    # 1. Typo search: "shamelesse" should find "Shameless"
    window.series_search.setText("shamelesse")
    window._filter_series()
    assert window.series_list.count() == 1
    assert "Shameless" in window.series_list.item(0).text()

    # Clear search
    window.series_search.setText("")
    window._filter_series()
    assert window.series_list.count() == 4

    # 2. Filter by Apple TV+
    idx_apple = window.series_streaming_filter.findData("apple")
    assert idx_apple >= 0
    window.series_streaming_filter.setCurrentIndex(idx_apple)
    window._filter_series()
    assert window.series_list.count() == 1
    assert "Ted Lasso" in window.series_list.item(0).text()

    # 3. Filter by Globoplay
    idx_globo = window.series_streaming_filter.findData("globo")
    assert idx_globo >= 0
    window.series_streaming_filter.setCurrentIndex(idx_globo)
    window._filter_series()
    assert window.series_list.count() == 1
    assert "Sob Pressão" in window.series_list.item(0).text()

    # 4. Reset filter to All
    window.series_streaming_filter.setCurrentIndex(0)
    window._filter_series()
    assert window.series_list.count() == 4


def test_episode_multi_selection_and_select_all(qtbot, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    window = MainWindow()
    qtbot.addWidget(window)

    series = {"name": "Shameless", "series_id": 101}
    info = {
        "episodes": {
            "1": [
                {"id": "1", "episode_num": "1", "title": "Pilot", "container_extension": "mkv"},
                {"id": "2", "episode_num": "2", "title": "Frank the Plank", "container_extension": "mkv"},
            ],
            "2": [
                {"id": "3", "episode_num": "1", "title": "Summertime", "container_extension": "mkv"},
            ],
        }
    }

    window._set_episodes(series, info)

    # Initial state: 2 seasons
    assert window.episode_tree.topLevelItemCount() == 2
    assert window.download_episodes_button.text() == "Baixar selecionados"

    # Select all button
    window._select_all_episodes()
    items = window._episode_items()
    assert len(items) == 3
    assert window.download_episodes_button.text() == "Baixar selecionados (3)"

    # Deselect all
    window._deselect_all_episodes()
    assert len(window._episode_items()) == 0
    assert window.download_episodes_button.text() == "Baixar selecionados"

    # Select single episode by checking its checkbox
    season_1 = window.episode_tree.topLevelItem(0)
    ep_1 = season_1.child(0)
    ep_1.setCheckState(0, Qt.CheckState.Checked)

    items = window._episode_items()
    assert len(items) == 1
    assert items[0].text(1) == "Pilot"
    assert window.download_episodes_button.text() == "Baixar selecionados (1)"

    # Checking season 1 header should check all season 1 episodes
    season_1.setCheckState(0, Qt.CheckState.Checked)
    items = window._episode_items()
    assert len(items) == 2
    assert window.download_episodes_button.text() == "Baixar selecionados (2)"


def test_download_progress_banners_update(qtbot, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    window = MainWindow()
    qtbot.addWidget(window)

    # Initially no active download
    assert "Nenhum download ativo" in window.movie_download_banner.title_label.text()
    assert "Nenhum download ativo" in window.series_download_banner.title_label.text()

    # Create an active download task
    task = DownloadTask(
        id="test-1",
        kind="series",
        title="Shameless · S01E01",
        url_candidates=["http://example.test/stream.mkv"],
        destination_dir=str(tmp_path),
        base_filename="S01E01",
        status=DownloadStatus.DOWNLOADING,
        downloaded_bytes=50000000,
        total_bytes=100000000,
        speed_bps=2500000.0,
    )

    window.downloads.tasks["test-1"] = task
    window._update_download_banners()

    # Banners should now display download title, percent, and speed
    assert "Baixando: Shameless · S01E01" in window.series_download_banner.title_label.text()
    assert "50%" in window.series_download_banner.stats_label.text()
    assert window.series_download_banner.progress_bar.value() == 50

    assert "Baixando: Shameless · S01E01" in window.movie_download_banner.title_label.text()
    assert "50%" in window.movie_download_banner.stats_label.text()
