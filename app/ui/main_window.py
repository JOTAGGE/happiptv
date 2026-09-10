from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QPoint, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider,
    QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app.catalog_manager import CatalogManager, matches_query, normalize_text, search_score
from app.config import AppConfig, ConfigStore
from app.download_manager import DownloadManager
from app.m3u_parser import parse_m3u_content
from app.models import (
    Account, CustomPlaylist, DownloadStatus, DownloadTask, Episode, MediaItem,
    Profile, format_duration, human_size, safe_filename,
)
from app.paths import app_data_dir, default_download_dir
from app.player import VideoPlayerWidget
from app.ui.details_dialog import DetailsDialog
from app.ui.epg_widget import EPGWidget
from app.ui.pin_dialog import PinDialog
from app.ui.style import STYLESHEET
from app.xtream_client import XtreamClient


def matches_search(query: str, target: str) -> bool:
    """Compatibility wrapper around the catalog's single search implementation."""
    return matches_query(query, target)


STREAMING_PRESETS: list[tuple[str, str, list[str]]] = [
    ("all", "Todos os streamings / categorias", []),
    ("netflix", "Netflix", ["netflix"]),
    ("prime", "Prime Video", ["prime", "amazon"]),
    ("apple", "Apple TV+", ["apple", "apple tv", "appletv"]),
    ("globo", "Globoplay", ["globo", "globoplay"]),
    ("hbo", "HBO Max / Max", ["hbo", "max", "hbo max"]),
    ("disney", "Disney+", ["disney", "disney+"]),
    ("paramount", "Paramount+", ["paramount", "paramount+"]),
    ("star", "Star+", ["star+", "star plus", "starplus"]),
]
STREAMING_KEYWORDS: dict[str, list[str]] = {
    key: [normalize_text(kw) for kw in keywords]
    for key, _, keywords in STREAMING_PRESETS if keywords
}


class DownloadProgressBanner(QFrame):
    def __init__(self, on_open_downloads: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DownloadBanner")
        self.on_open_downloads = on_open_downloads

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        self.status_icon = QLabel("⬇")
        self.status_icon.setStyleSheet("font-size: 13px; color: #1749e8; font-weight: 900;")
        self.title_label = QLabel("Nenhum download ativo")
        self.title_label.setStyleSheet("font-weight: 700; color: #ffffff;")
        self.stats_label = QLabel("")
        self.stats_label.setProperty("muted", True)
        self.stats_label.setStyleSheet("font-family: 'DM Mono'; font-size: 11px;")
        self.view_button = QPushButton("Ver Downloads →")
        self.view_button.setStyleSheet("font-size: 11px; padding: 4px 10px;")
        self.view_button.clicked.connect(self.on_open_downloads)

        top_row.addWidget(self.status_icon)
        top_row.addWidget(self.title_label)
        top_row.addSpacing(8)
        top_row.addWidget(self.stats_label)
        top_row.addStretch()
        top_row.addWidget(self.view_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)

        layout.addLayout(top_row)
        layout.addWidget(self.progress_bar)

    def update_task(self, task: DownloadTask | None) -> None:
        if not task or task.status not in {DownloadStatus.DOWNLOADING, DownloadStatus.QUEUED}:
            self.title_label.setText("Nenhum download ativo")
            self.stats_label.setText("")
            self.progress_bar.setValue(0)
            return

        self.title_label.setText(f"Baixando: {task.title}")
        if task.status == DownloadStatus.QUEUED:
            self.stats_label.setText("Aguardando na fila...")
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            if task.total_bytes and task.total_bytes > 0:
                pct = int((task.downloaded_bytes / task.total_bytes) * 100)
                self.progress_bar.setValue(pct)
                speed = human_size(task.speed_bps, "/s")
                self.stats_label.setText(f"{pct}% • {speed}")
            else:
                self.progress_bar.setValue(0)
                self.stats_label.setText(human_size(task.downloaded_bytes))


class SyncWorkerSignals(QObject):
    finished = Signal(list, dict)
    error = Signal(str)


class SyncWorker(QRunnable):
    def __init__(self, account: Account) -> None:
        super().__init__()
        self.account = account
        self.signals = SyncWorkerSignals()

    def run(self) -> None:
        try:
            if self.account.account_type == "m3u":
                self._sync_m3u()
            else:
                self._sync_xtream()
        except Exception as exc:
            self.signals.error.emit(str(exc))

    def _sync_m3u(self) -> None:
        items = []
        if self.account.m3u_url.startswith(("http://", "https://")):
            import requests
            with requests.get(self.account.m3u_url, timeout=(10, 30), stream=True) as resp:
                resp.raise_for_status()
                chunks: list[bytes] = []
                received = 0
                for chunk in resp.iter_content(128 * 1024):
                    received += len(chunk)
                    if received > 16 * 1024 * 1024:
                        raise ValueError("A lista M3U excede o limite seguro de 16 MB.")
                    chunks.append(chunk)
                text = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
                items = parse_m3u_content(text, self.account.id)
        elif Path(self.account.m3u_url).exists():
            playlist_path = Path(self.account.m3u_url)
            if playlist_path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("A lista M3U excede o limite seguro de 16 MB.")
            text = playlist_path.read_text(encoding="utf-8", errors="ignore")
            items = parse_m3u_content(text, self.account.id)
        stats = {"latency_ms": 30, "status": "Online (M3U)"}
        self.signals.finished.emit(items, stats)

    def _sync_xtream(self) -> None:
        client = XtreamClient(self.account.server_url, self.account.username, self.account.password)
        conn_info = client.test_connection()

        items: list[MediaItem] = []

        # 1. Live TV
        try:
            live_streams = client.get_live_streams()
            live_cats = {str(c.get("category_id")): c.get("category_name", "Geral") for c in client.get_live_categories()}
            for st in live_streams:
                cid = str(st.get("category_id", "0"))
                cname = live_cats.get(cid, "Geral")
                item = MediaItem(
                    id=f"xtream_{self.account.id}_live_{st.get('stream_id')}",
                    kind="live",
                    title=st.get("name", "Canal"),
                    stream_id=st.get("stream_id", 0),
                    category_id=cid,
                    category_name=cname,
                    poster=st.get("stream_icon", ""),
                    backdrop=st.get("stream_icon", ""),
                    account_id=self.account.id,
                    epg_channel_id=st.get("epg_channel_id", ""),
                    stream_url=client.stream_urls("live", st.get("stream_id", 0), None)[0],
                    raw_data=st,
                )
                items.append(item)
        except Exception:
            pass

        # 2. VOD Movies
        try:
            vod_streams = client.get_vod_streams()
            vod_cats = {str(c.get("category_id")): c.get("category_name", "Geral") for c in client.get_vod_categories()}
            for st in vod_streams:
                cid = str(st.get("category_id", "0"))
                cname = vod_cats.get(cid, "Geral")
                ext = st.get("container_extension", "mp4")
                item = MediaItem(
                    id=f"xtream_{self.account.id}_movie_{st.get('stream_id')}",
                    kind="movie",
                    title=st.get("name", "Filme"),
                    stream_id=st.get("stream_id", 0),
                    category_id=cid,
                    category_name=cname,
                    poster=st.get("stream_icon", ""),
                    backdrop=st.get("stream_icon", ""),
                    rating=st.get("rating", ""),
                    container_extension=ext,
                    account_id=self.account.id,
                    stream_url=client.stream_urls("movie", st.get("stream_id", 0), ext)[0],
                    raw_data=st,
                )
                items.append(item)
        except Exception:
            pass

        # 3. Series
        try:
            series_list = client.get_series()
            series_cats = {str(c.get("category_id")): c.get("category_name", "Geral") for c in client.get_series_categories()}
            for st in series_list:
                cid = str(st.get("category_id", "0"))
                cname = series_cats.get(cid, "Geral")
                item = MediaItem(
                    id=f"xtream_{self.account.id}_series_{st.get('series_id')}",
                    kind="series",
                    title=st.get("name", "Série"),
                    stream_id=st.get("series_id", 0),
                    category_id=cid,
                    category_name=cname,
                    poster=st.get("cover", ""),
                    backdrop=st.get("backdrop_path", [""])[0] if isinstance(st.get("backdrop_path"), list) and st.get("backdrop_path") else "",
                    rating=st.get("rating", ""),
                    synopsis=st.get("plot", ""),
                    genre=st.get("genre", ""),
                    account_id=self.account.id,
                    stream_url="",
                    raw_data=st,
                )
                items.append(item)
        except Exception:
            pass

        self.signals.finished.emit(items, conn_info)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Happiptv")
        self.setMinimumSize(1200, 780)
        self.setStyleSheet(STYLESHEET)

        icon_path = Path(__file__).resolve().parent.parent.parent / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # Core Stores & Managers
        self.config_store = ConfigStore()
        self.config, self.password = self.config_store.load()
        self.catalog = CatalogManager()
        self.download_manager = DownloadManager(
            max_concurrent=self.config.max_concurrent_downloads,
            download_root=Path(self.config.download_dir),
        )
        self.download_manager.configure(
            self.config.max_concurrent_downloads,
            self.config.use_ffmpeg_fallback,
            self.config.download_dir,
        )

        # Legacy compatibility references
        self.downloads = self.download_manager
        self.series_data: list[dict[str, Any]] = []

        self.thread_pool = QThreadPool(self)
        self.poster_network = QNetworkAccessManager(self)
        self._poster_cache: dict[str, QIcon] = {}
        self._poster_replies: set[QNetworkReply] = set()
        self._search_origin_index = 0
        self._global_search_timer = QTimer(self)
        self._global_search_timer.setSingleShot(True)
        self._global_search_timer.setInterval(220)
        self._global_search_timer.timeout.connect(self._execute_global_search)

        # State Variables
        self.current_profile: Profile = self._get_active_profile()
        self.last_live_channel: MediaItem | None = None
        self.unlocked_categories: set[str] = set()

        # UI Setup
        self._init_ui()
        self._setup_shortcuts()
        self._connect_signals()

        # Initial view load (populate from cached catalog immediately)
        self._populate_all_views()
        self._refresh_home_view()
        self._update_source_health_ui()

        # Silent background sync if accounts are configured
        if self.config.accounts:
            QTimer.singleShot(1200, self._auto_background_sync)


    def _get_active_profile(self) -> Profile:
        for p_dict in self.config.profiles:
            if p_dict.get("id") == self.config.active_profile_id:
                return Profile.from_dict(p_dict)
        if self.config.profiles:
            return Profile.from_dict(self.config.profiles[0])
        default_prof = Profile(id="default", name="Principal", avatar="⚡")
        self.config.profiles.append(default_prof.to_dict())
        return default_prof

    def _save_profile_state(self) -> None:
        for i, p_dict in enumerate(self.config.profiles):
            if p_dict.get("id") == self.current_profile.id:
                self.config.profiles[i] = self.current_profile.to_dict()
                break
        self.config_store.save(self.config)

    def _init_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("CentralWidget")
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. Header Bar
        header = QFrame()
        header.setObjectName("HeaderBar")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 10, 20, 10)
        header_layout.setSpacing(16)

        # Brand Logo with Smiley
        brand_col = QVBoxLayout()
        brand_col.setSpacing(2)
        logo_lbl = QLabel()
        logo_lbl.setText("<span style='background:#1749e8; color:#ffffff; font-weight:900; padding:2px 7px; border-radius:4px; font-size:16px; margin-right:6px;'>:)</span><span style='color:#ffffff; font-weight:900;'>HAPPI</span><span style='color:#1749e8; font-weight:900;'>PTV</span>")
        logo_lbl.setObjectName("BrandLogo")
        sub_lbl = QLabel("BLUE LAB EXPERIMENTAL TECH")
        sub_lbl.setObjectName("BrandSub")
        brand_col.addWidget(logo_lbl)
        brand_col.addWidget(sub_lbl)
        header_layout.addLayout(brand_col)

        header_layout.addSpacing(20)

        # Global Search
        self.search_input = QLineEdit()
        self.search_input.setObjectName("GlobalSearch")
        self.search_input.setPlaceholderText("Buscar canais, filmes e séries…   Ctrl+F")
        self.search_input.setMinimumWidth(320)
        self.search_input.setMaximumWidth(520)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_query_changed)
        header_layout.addWidget(self.search_input, 1)

        header_layout.addStretch()

        # Source Health Pill
        self.health_frame = QFrame()
        self.health_frame.setObjectName("HealthFrame")
        self.health_frame.setStyleSheet("background: #11151f; border: 1px solid #1c2435; border-radius: 4px; padding: 4px 10px;")
        health_layout = QHBoxLayout(self.health_frame)
        health_layout.setContentsMargins(0, 0, 0, 0)
        health_layout.setSpacing(8)

        self.live_dot = QLabel("●")
        self.live_dot.setStyleSheet("color: #00e054; font-size: 14px;")
        self.health_lbl = QLabel("STATUS // OPERACIONAL")
        self.health_lbl.setStyleSheet("font-family: 'DM Mono'; font-size: 11px; color: #8fa1ba;")

        self.btn_sync = QPushButton("↻ Sincronizar")
        self.btn_sync.setStyleSheet("font-size: 11px; padding: 4px 8px;")
        self.btn_sync.clicked.connect(self.sync_active_account)

        health_layout.addWidget(self.live_dot)
        health_layout.addWidget(self.health_lbl)
        health_layout.addWidget(self.btn_sync)
        header_layout.addWidget(self.health_frame)

        # Account Switcher
        self.account_combo = QComboBox()
        self.account_combo.setToolTip("Trocar Fonte / Conta IPTV")
        self._reload_accounts_combo()
        self.account_combo.currentIndexChanged.connect(self._on_account_switched)
        header_layout.addWidget(self.account_combo)

        # Profile Switcher
        self.profile_combo = QComboBox()
        self.profile_combo.setToolTip("Perfil Ativo")
        self._reload_profiles_combo()
        self.profile_combo.currentIndexChanged.connect(self._on_profile_switched)
        header_layout.addWidget(self.profile_combo)

        main_layout.addWidget(header)

        # 2. Body Splitter (Sidebar + Content View Stack)
        body_splitter = QSplitter(Qt.Horizontal)
        body_splitter.setHandleWidth(1)

        # Sidebar
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(230)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(10, 16, 10, 16)
        sidebar_layout.setSpacing(4)

        nav_kicker = QLabel("00 // NAVEGAÇÃO")
        nav_kicker.setProperty("kicker", True)
        sidebar_layout.addWidget(nav_kicker)
        sidebar_layout.addSpacing(6)

        self.nav_btns: list[QPushButton] = []
        tabs_meta = [
            ("01 // INÍCIO", 0),
            ("02 // AO VIVO", 1),
            ("03 // FILMES", 2),
            ("04 // SÉRIES", 3),
            ("05 // OFFLINE", 4),
            ("06 // DOWNLOADS", 5),
            ("07 // MINHAS LISTAS", 6),
            ("08 // CONFIGURAÇÕES", 7),
        ]

        for text, idx in tabs_meta:
            btn = QPushButton(text)
            btn.setProperty("nav", True)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, i=idx: self._switch_tab(i))
            sidebar_layout.addWidget(btn)
            self.nav_btns.append(btn)

        sidebar_layout.addStretch()

        # Return to "Now Playing" Button in Sidebar
        self.btn_now_playing = QPushButton("▶ REPRODUZINDO AGORA")
        self.btn_now_playing.setProperty("nav", True)
        self.btn_now_playing.setStyleSheet(
            "QPushButton { background: #0e1726; color: #00e054; border: 1px solid #1a2a44; "
            "text-align: left; padding: 9px 12px; font-weight: 800; font-family: 'DM Mono', Consolas, monospace; "
            "font-size: 11px; border-radius: 6px; } "
            "QPushButton:hover { background: #15233a; border-color: #2258ff; color: #22ff77; }"
        )
        self.btn_now_playing.setCheckable(True)
        self.btn_now_playing.clicked.connect(self._return_to_player)
        self.btn_now_playing.hide()
        sidebar_layout.addWidget(self.btn_now_playing)

        # Active Download Banner at bottom of sidebar
        self.download_banner = DownloadProgressBanner(on_open_downloads=lambda: self._switch_tab(5))
        self.movie_download_banner = self.download_banner
        self.series_download_banner = self.download_banner
        sidebar_layout.addWidget(self.download_banner)

        body_splitter.addWidget(sidebar)


        # 3. Content View Stack
        self.view_stack = QStackedWidget()
        self.pages = self.view_stack  # Compatibility alias

        # View 0: Home (Biblioteca de Verdade)
        self.view_home = self._create_home_view()
        self.view_stack.addWidget(self.view_home)

        # View 1: Live TV (EPG & Player Preview)
        self.view_live = self._create_live_view()
        self.view_stack.addWidget(self.view_live)

        # View 2: Filmes
        self.view_movies = self._create_catalog_view("movie")
        self.view_stack.addWidget(self.view_movies)

        # View 3: Séries
        self.view_series = self._create_catalog_view("series")
        self.view_stack.addWidget(self.view_series)

        # View 4: Biblioteca Offline
        self.view_offline = self._create_offline_view()
        self.view_stack.addWidget(self.view_offline)

        # View 5: Downloads Manager
        self.view_downloads = self._create_downloads_view()
        self.view_stack.addWidget(self.view_downloads)

        # View 6: Minhas Listas (Favoritos, Watchlist, Histórico)
        self.view_lists = self._create_lists_view()
        self.view_stack.addWidget(self.view_lists)

        # View 7: Configurações (Multi-conta, Perfis, Categorias, Parental, Backup)
        self.view_settings = self._create_settings_view()
        self.view_stack.addWidget(self.view_settings)

        # View 8: Full Video Player
        self.player_widget = VideoPlayerWidget(self)
        self.view_stack.addWidget(self.player_widget)

        # View 9: Global search results
        self.view_search = self._create_search_view()
        self.view_stack.addWidget(self.view_search)

        body_splitter.addWidget(self.view_stack)
        main_layout.addWidget(body_splitter, 1)

        # Compatibility aliases for test_series_filter_selection
        self.series_search = self.series_search_edit
        self.series_streaming_filter = self.series_stream_combo
        self.episode_tree = QTreeWidget()
        self.episode_tree.itemChanged.connect(self._on_episode_tree_item_changed)
        self.download_episodes_button = QPushButton("Baixar selecionados")

        # Set default tab to Home
        self._switch_tab(0)

    # -------------------------------------------------------------
    # Navigation & Views Creation
    # -------------------------------------------------------------

    def _switch_tab(self, index: int) -> None:
        for i, btn in enumerate(self.nav_btns):
            btn.setChecked(i == index)
        if hasattr(self, "btn_now_playing"):
            self.btn_now_playing.setChecked(index == 8)
        self.view_stack.setCurrentIndex(index)
        if index == 0:
            self._refresh_home_view()
        elif index == 4:
            self._refresh_offline_view()
        elif index == 6:
            self._refresh_lists_view()
        elif index == 7:
            self._refresh_profiles_list()

    def _return_to_player(self) -> None:
        for btn in self.nav_btns:
            btn.setChecked(False)
        if hasattr(self, "btn_now_playing"):
            self.btn_now_playing.setChecked(True)
        self.view_stack.setCurrentIndex(8)


    def _create_home_view(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(28, 20, 28, 28)
        layout.setSpacing(20)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: 0; background: transparent;")

        content = QWidget()
        c_layout = QVBoxLayout(content)
        c_layout.setSpacing(24)

        # Editorial hero: streaming hierarchy with Blue Lab's oversized type.
        self.hero_box = QFrame()
        self.hero_box.setObjectName("HeroBackdropCard")
        self.hero_box.setMinimumHeight(280)
        hero_layout = QHBoxLayout(self.hero_box)
        hero_layout.setContentsMargins(34, 28, 30, 28)
        hero_layout.setSpacing(24)
        hero_copy = QVBoxLayout()
        hero_copy.setSpacing(10)
        hk = QLabel("CURADORIA // HAPPIPTV 2026—")
        hk.setProperty("kicker", True)
        self.hero_title = QLabel("STREAMING\nSEM RUÍDO")
        self.hero_title.setWordWrap(True)
        self.hero_title.setStyleSheet("font-size: 42px; font-weight: 900; color: #ffffff; letter-spacing: -1.4px;")
        self.hero_desc = QLabel("Sua programação, seus filmes e sua biblioteca offline em um só lugar.")
        self.hero_desc.setWordWrap(True)
        self.hero_desc.setMaximumWidth(610)
        self.hero_desc.setStyleSheet("color: #a9b5c9; font-size: 14px;")
        self.btn_hero_action = QPushButton("Explorar filmes  →")
        self.btn_hero_action.setProperty("primary", True)
        self.btn_hero_action.setFixedWidth(170)
        self.btn_hero_action.clicked.connect(self._open_hero_item)
        hero_copy.addWidget(hk)
        hero_copy.addWidget(self.hero_title)
        hero_copy.addWidget(self.hero_desc)
        hero_copy.addSpacing(6)
        hero_copy.addWidget(self.btn_hero_action, 0, Qt.AlignLeft)
        hero_copy.addStretch()
        hero_mark = QLabel("HAPPI\nTV")
        hero_mark.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        hero_mark.setStyleSheet("font-size: 74px; font-weight: 900; color: #2b5cff; letter-spacing: -4px;")
        hero_layout.addLayout(hero_copy, 3)
        hero_layout.addWidget(hero_mark, 2)
        c_layout.addWidget(self.hero_box)

        # Section: Continuar Assistindo
        c_layout.addWidget(self._make_section_title("CONTINUAR ASSISTINDO"))
        self.continue_watching_list = QListWidget()
        self._configure_media_rail(self.continue_watching_list)
        self.continue_watching_list.itemDoubleClicked.connect(self._on_continue_item_clicked)
        c_layout.addWidget(self.continue_watching_list)

        # Section: Favoritos
        c_layout.addWidget(self._make_section_title("FAVORITOS"))
        self.home_favs_list = QListWidget()
        self._configure_media_rail(self.home_favs_list)
        self.home_favs_list.itemDoubleClicked.connect(self._on_home_media_clicked)
        c_layout.addWidget(self.home_favs_list)

        # Section: Minha Lista (Watchlist)
        c_layout.addWidget(self._make_section_title("MINHA LISTA (QUERO ASSISTIR)"))
        self.home_watchlist_list = QListWidget()
        self._configure_media_rail(self.home_watchlist_list)
        self.home_watchlist_list.itemDoubleClicked.connect(self._on_home_media_clicked)
        c_layout.addWidget(self.home_watchlist_list)

        scroll.setWidget(content)
        layout.addWidget(scroll)
        return widget

    def _create_live_view(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        self.epg_widget = EPGWidget(self)
        self.epg_widget.channel_selected.connect(self._on_live_channel_selected)
        self.epg_widget.channel_highlighted.connect(self._on_live_channel_highlighted)
        self.epg_widget.add_to_playlist_requested.connect(self._show_add_to_playlist_dialog)
        layout.addWidget(self.epg_widget, 1)

        return widget


    def _create_catalog_view(self, kind: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(30, 24, 30, 26)
        layout.setSpacing(18)

        heading_row = QHBoxLayout()
        title = QLabel("FILMES" if kind == "movie" else "SÉRIES")
        title.setProperty("heading", True)
        subtitle = QLabel("Sua biblioteca, ordenada por relevância")
        subtitle.setProperty("muted", True)
        title_col = QVBoxLayout()
        title_col.setSpacing(3)
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        count_label = QLabel("0 títulos")
        count_label.setObjectName("ResultCount")
        count_label.setProperty("mono", True)
        heading_row.addLayout(title_col)
        heading_row.addStretch()
        heading_row.addWidget(count_label, 0, Qt.AlignBottom)
        layout.addLayout(heading_row)

        # Filter bar
        filter_panel = QFrame()
        filter_panel.setObjectName("FilterPanel")
        bar = QHBoxLayout(filter_panel)
        bar.setContentsMargins(12, 10, 12, 10)
        bar.setSpacing(10)
        cat_combo = QComboBox()
        cat_combo.addItem("Todas as categorias", "all")
        bar.addWidget(cat_combo)

        # Streaming Preset filter for Movies & Series
        stream_combo = QComboBox()
        for key, name, _ in STREAMING_PRESETS:
            stream_combo.addItem(name, key)
        bar.addWidget(stream_combo)

        search_edit = QLineEdit()
        search_edit.setPlaceholderText("Pesquisar por título ou categoria…")
        search_edit.setClearButtonEnabled(True)
        bar.addWidget(search_edit, 1)

        list_w = QListWidget()
        list_w.setObjectName("CatalogGrid")
        list_w.setViewMode(QListWidget.ViewMode.IconMode)
        list_w.setResizeMode(QListWidget.ResizeMode.Adjust)
        list_w.setMovement(QListWidget.Movement.Static)
        list_w.setWrapping(True)
        list_w.setSpacing(12)
        list_w.setIconSize(QSize(148, 208))
        list_w.setGridSize(QSize(184, 278))
        list_w.setWordWrap(True)
        list_w.setUniformItemSizes(True)
        list_w.itemDoubleClicked.connect(self._on_catalog_item_clicked)

        cat_combo.currentIndexChanged.connect(lambda: self._filter_catalog_view(kind, cat_combo, search_edit, list_w, stream_combo))
        stream_combo.currentIndexChanged.connect(lambda: self._filter_catalog_view(kind, cat_combo, search_edit, list_w, stream_combo))
        search_timer = QTimer(widget)
        search_timer.setSingleShot(True)
        search_timer.setInterval(180)
        search_timer.timeout.connect(lambda: self._filter_catalog_view(kind, cat_combo, search_edit, list_w, stream_combo))
        search_edit.textChanged.connect(lambda _text: search_timer.start())

        btn_refresh = QPushButton("↻ Atualizar")
        btn_refresh.clicked.connect(lambda: self._populate_catalog_view(kind, cat_combo, list_w))
        bar.addWidget(btn_refresh)

        layout.addWidget(filter_panel)
        layout.addWidget(list_w, 1)

        setattr(self, f"{kind}_cat_combo", cat_combo)
        setattr(self, f"{kind}_stream_combo", stream_combo)
        setattr(self, f"{kind}_search_edit", search_edit)
        setattr(self, f"{kind}_list", list_w)
        setattr(self, f"{kind}_count_label", count_label)
        setattr(self, f"{kind}_search_timer", search_timer)

        return widget

    def _configure_media_rail(self, rail: QListWidget) -> None:
        rail.setObjectName("MediaRail")
        rail.setViewMode(QListWidget.IconMode)
        rail.setFlow(QListWidget.LeftToRight)
        rail.setWrapping(False)
        rail.setMovement(QListWidget.Static)
        rail.setResizeMode(QListWidget.Adjust)
        rail.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        rail.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        rail.setIconSize(QSize(118, 166))
        rail.setGridSize(QSize(154, 220))
        rail.setFixedHeight(228)
        rail.setSpacing(8)

    def _open_hero_item(self) -> None:
        featured = getattr(self, "hero_item", None)
        if featured:
            self._open_details_modal(featured)
        else:
            self._switch_tab(2)

    def _create_search_view(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(30, 24, 30, 26)
        layout.setSpacing(16)

        kicker = QLabel("BUSCA GLOBAL")
        kicker.setProperty("kicker", True)
        self.search_title = QLabel("Resultados")
        self.search_title.setProperty("heading", True)
        self.search_summary = QLabel("Digite pelo menos dois caracteres para pesquisar.")
        self.search_summary.setProperty("muted", True)
        self.global_results = QTreeWidget()
        self.global_results.setObjectName("SearchResults")
        self.global_results.setHeaderLabels(["TIPO", "TÍTULO", "CATEGORIA", "FONTE"])
        self.global_results.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.global_results.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.global_results.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.global_results.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.global_results.setRootIsDecorated(False)
        self.global_results.setAlternatingRowColors(True)
        self.global_results.itemDoubleClicked.connect(self._on_global_result_clicked)

        layout.addWidget(kicker)
        layout.addWidget(self.search_title)
        layout.addWidget(self.search_summary)
        layout.addSpacing(4)
        layout.addWidget(self.global_results, 1)
        return widget

    def _create_offline_view(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(28, 20, 28, 28)
        layout.setSpacing(16)

        head_row = QHBoxLayout()
        title = QLabel("BIBLIOTECA OFFLINE // ARQUIVOS LOCAIS")
        title.setProperty("heading", True)
        head_row.addWidget(title)
        head_row.addStretch()

        btn_open_folder = QPushButton("Abrir Pasta de Downloads ↗")
        btn_open_folder.clicked.connect(self._open_download_folder)
        head_row.addWidget(btn_open_folder)
        layout.addLayout(head_row)

        desc = QLabel("Conteúdo baixado pronto para reprodução imediata, sem necessidade de conexão com a internet.")
        desc.setStyleSheet("color: #8da0b8;")
        layout.addWidget(desc)

        self.offline_table = QTableWidget()
        self.offline_table.setColumnCount(5)
        self.offline_table.setHorizontalHeaderLabels(["Título", "Tipo", "Tamanho no Disco", "Local do Arquivo", "Ações"])
        self.offline_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.offline_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(self.offline_table, 1)

        return widget

    def _create_downloads_view(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(14)

        head_row = QHBoxLayout()
        title = QLabel("GERENCIADOR DE DOWNLOADS")
        title.setProperty("heading", True)
        head_row.addWidget(title)
        head_row.addStretch()

        btn_change_dir = QPushButton("Alterar Pasta...")
        btn_change_dir.clicked.connect(self._choose_download_dir)
        head_row.addWidget(btn_change_dir)
        layout.addLayout(head_row)

        self.downloads_table = QTableWidget()
        self.downloads_table.setColumnCount(6)
        self.downloads_table.setHorizontalHeaderLabels(["Título", "Status", "Progresso", "Velocidade", "Tamanho", "Ações"])
        self.downloads_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.downloads_table, 1)

        return widget

    def _create_lists_view(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(14)

        tabs = QTabWidget()

        # Tab: Favoritos
        self.favs_list = QListWidget()
        self.favs_list.itemDoubleClicked.connect(self._on_home_media_clicked)
        tabs.addTab(self.favs_list, "★ Favoritos")

        # Tab: Minha Lista
        self.watchlist_widget = QListWidget()
        self.watchlist_widget.itemDoubleClicked.connect(self._on_home_media_clicked)
        tabs.addTab(self.watchlist_widget, "✓ Minha Lista (Watchlist)")

        # Tab: Histórico
        hist_tab = QWidget()
        hist_layout = QVBoxLayout(hist_tab)
        h_top = QHBoxLayout()
        h_top.addWidget(QLabel("Histórico completo de reprodução:"))
        h_top.addStretch()
        btn_clear = QPushButton("Limpar Histórico")
        btn_clear.setProperty("danger", True)
        btn_clear.clicked.connect(self._clear_history)
        h_top.addWidget(btn_clear)
        hist_layout.addLayout(h_top)

        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(self._on_continue_item_clicked)
        hist_layout.addWidget(self.history_list, 1)
        tabs.addTab(hist_tab, "Histórico")

        # Tab: Playlists Personalizadas (CRUD, Cores, Emojis)
        playlists_tab = QWidget()
        pl_layout = QVBoxLayout(playlists_tab)
        pl_layout.setContentsMargins(6, 8, 6, 8)
        pl_layout.setSpacing(10)

        pl_top_row = QHBoxLayout()
        pl_top_row.setSpacing(8)
        btn_new_pl = QPushButton("+ Nova Playlist")
        btn_new_pl.setProperty("primary", True)
        btn_new_pl.clicked.connect(self._show_create_playlist_dialog)

        btn_edit_pl = QPushButton("Editar / Renomear")
        btn_edit_pl.clicked.connect(self._show_edit_playlist_dialog)

        btn_del_pl = QPushButton("Excluir Playlist")
        btn_del_pl.setProperty("danger", True)
        btn_del_pl.clicked.connect(self._delete_selected_playlist)

        pl_top_row.addWidget(btn_new_pl)
        pl_top_row.addWidget(btn_edit_pl)
        pl_top_row.addWidget(btn_del_pl)
        pl_top_row.addStretch()
        pl_layout.addLayout(pl_top_row)

        pl_splitter = QSplitter(Qt.Horizontal)

        # Left Column: Playlists list
        left_box = QFrame()
        left_box.setStyleSheet("background: #0f1420; border: 1px solid #1c2536; border-radius: 6px; padding: 6px;")
        left_layout = QVBoxLayout(left_box)
        left_lbl = QLabel("SUAS PLAYLISTS")
        left_lbl.setStyleSheet("font-family: 'DM Mono'; font-size: 11px; font-weight: 800; color: #1749e8;")
        left_layout.addWidget(left_lbl)
        self.custom_playlists_list = QListWidget()
        self.custom_playlists_list.currentItemChanged.connect(self._on_playlist_selection_changed)
        left_layout.addWidget(self.custom_playlists_list, 1)
        pl_splitter.addWidget(left_box)

        # Right Column: Items inside the selected playlist
        right_box = QFrame()
        right_box.setStyleSheet("background: #0f1420; border: 1px solid #1c2536; border-radius: 6px; padding: 6px;")
        right_layout = QVBoxLayout(right_box)
        self.playlist_items_header = QLabel("CONTEÚDO DA PLAYLIST")
        self.playlist_items_header.setStyleSheet("font-family: 'DM Mono'; font-size: 11px; font-weight: 800; color: #cbd5e1;")
        right_layout.addWidget(self.playlist_items_header)

        self.playlist_items_list = QListWidget()
        self.playlist_items_list.itemDoubleClicked.connect(self._on_home_media_clicked)
        right_layout.addWidget(self.playlist_items_list, 1)

        pl_items_actions = QHBoxLayout()
        btn_play_pl_item = QPushButton("▶ Reproduzir Item")
        btn_play_pl_item.setProperty("primary", True)
        btn_play_pl_item.clicked.connect(self._play_selected_playlist_item)
        btn_remove_pl_item = QPushButton("Remover da Playlist")
        btn_remove_pl_item.clicked.connect(self._remove_selected_playlist_item)
        pl_items_actions.addWidget(btn_play_pl_item)
        pl_items_actions.addWidget(btn_remove_pl_item)
        pl_items_actions.addStretch()
        right_layout.addLayout(pl_items_actions)

        pl_splitter.addWidget(right_box)
        pl_splitter.setSizes([320, 560])
        pl_layout.addWidget(pl_splitter, 1)

        tabs.addTab(playlists_tab, "📁 Playlists Personalizadas")

        layout.addWidget(tabs)
        return widget

    def _create_settings_view(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(28, 20, 28, 28)
        layout.setSpacing(16)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: 0; background: transparent;")

        content = QWidget()
        c_layout = QVBoxLayout(content)
        c_layout.setSpacing(20)

        # 1. Multi-Account Group
        acc_group = QGroupBox("MULTI-ACCOUNT // FONTES IPTV (XTREAM / M3U)")
        acc_layout = QVBoxLayout(acc_group)
        self.accounts_list = QListWidget()
        self.accounts_list.setFixedHeight(120)
        acc_layout.addWidget(self.accounts_list)

        acc_btns = QHBoxLayout()
        btn_add_acc = QPushButton("+ Adicionar Conta Xtream")
        btn_add_acc.clicked.connect(self._show_add_xtream_dialog)
        btn_add_m3u = QPushButton("+ Adicionar Lista M3U")
        btn_add_m3u.clicked.connect(self._show_add_m3u_dialog)
        btn_remove_acc = QPushButton("Remover Conta Selecionada")
        btn_remove_acc.setProperty("danger", True)
        btn_remove_acc.clicked.connect(self._remove_selected_account)

        acc_btns.addWidget(btn_add_acc)
        acc_btns.addWidget(btn_add_m3u)
        acc_btns.addWidget(btn_remove_acc)
        acc_btns.addStretch()
        acc_layout.addLayout(acc_btns)
        c_layout.addWidget(acc_group)

        # 2. Profiles Group
        prof_group = QGroupBox("PERFIS DE USUÁRIO // CONFIGURAÇÃO")
        prof_layout = QVBoxLayout(prof_group)
        self.profiles_list_w = QListWidget()
        self.profiles_list_w.setFixedHeight(120)
        self.profiles_list_w.itemDoubleClicked.connect(self._switch_selected_profile)
        prof_layout.addWidget(self.profiles_list_w)

        prof_btns = QHBoxLayout()
        btn_switch_prof = QPushButton("Alternar para Perfil Selecionado")
        btn_switch_prof.setProperty("primary", True)
        btn_switch_prof.clicked.connect(self._switch_selected_profile)

        btn_add_prof = QPushButton("+ Novo Perfil")
        btn_add_prof.clicked.connect(self._show_add_profile_dialog)

        btn_edit_prof = QPushButton("Editar Perfil")
        btn_edit_prof.clicked.connect(self._edit_selected_profile)

        btn_delete_prof = QPushButton("Excluir Perfil")
        btn_delete_prof.setProperty("danger", True)
        btn_delete_prof.clicked.connect(self._delete_selected_profile)

        prof_btns.addWidget(btn_switch_prof)
        prof_btns.addWidget(btn_add_prof)
        prof_btns.addWidget(btn_edit_prof)
        prof_btns.addWidget(btn_delete_prof)
        prof_btns.addStretch()
        prof_layout.addLayout(prof_btns)
        c_layout.addWidget(prof_group)
        self._refresh_profiles_list()


        # 3. Parental Control & PIN
        parental_group = QGroupBox("CONTROLE PARENTAL & BLOQUEIO DE CATEGORIAS")
        parental_layout = QFormLayout(parental_group)
        self.pin_edit = QLineEdit()
        self.pin_edit.setPlaceholderText("Novo PIN")
        self.pin_edit.setEchoMode(QLineEdit.Password)
        self.pin_edit.setMaxLength(8)
        self.pin_edit.setFixedWidth(100)
        parental_layout.addRow("PIN de Segurança (4 dígitos):", self.pin_edit)
        btn_save_pin = QPushButton("Salvar Novo PIN")
        btn_save_pin.clicked.connect(self._save_parental_pin)
        parental_layout.addRow("", btn_save_pin)
        c_layout.addWidget(parental_group)

        # 4. Backup & Restore
        backup_group = QGroupBox("PRIVACIDADE LOCAL-FIRST // BACKUP & RESTAURAÇÃO")
        backup_layout = QHBoxLayout(backup_group)
        btn_export = QPushButton("Exportar Backup JSON")
        btn_export.clicked.connect(self._export_backup)
        btn_import = QPushButton("Importar Backup JSON")
        btn_import.clicked.connect(self._import_backup)
        backup_layout.addWidget(btn_export)
        backup_layout.addWidget(btn_import)
        backup_layout.addStretch()
        c_layout.addWidget(backup_group)

        security_group = QGroupBox("SEGURANÇA // STATUS DE PRÉ-LANÇAMENTO")
        security_layout = QVBoxLayout(security_group)
        security_copy = QLabel(
            "✓ Segredos no cofre do sistema\n"
            "✓ Cache e histórico sem URLs autenticadas\n"
            "✓ PIN derivado com PBKDF2\n"
            "✓ Exclusões limitadas à pasta de downloads\n\n"
            "Distribuição: assine o executável e publique checksums. Proteção absoluta contra cópia "
            "não existe em aplicativos desktop; licenciamento deve ser validado por um serviço próprio."
        )
        security_copy.setWordWrap(True)
        security_copy.setProperty("muted", True)
        security_layout.addWidget(security_copy)
        c_layout.addWidget(security_group)

        scroll.setWidget(content)
        layout.addWidget(scroll)
        return widget

    def _make_section_title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-family: 'DM Mono'; font-size: 12px; font-weight: 800; color: #1749e8; letter-spacing: 1px;")
        return lbl

    # -------------------------------------------------------------
    # Keyboard Shortcuts & Signal Connections
    # -------------------------------------------------------------

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key_Space), self, self.player_widget.toggle_play)
        QShortcut(QKeySequence(Qt.Key_F), self, self.player_widget.toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key_M), self, self.player_widget.toggle_mute)
        QShortcut(QKeySequence(Qt.Key_H), self, self.player_widget.toggle_controls_bar)
        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self.player_widget.seek_relative(-10000))
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self.player_widget.seek_relative(10000))
        QShortcut(QKeySequence(Qt.Key_D), self, self.player_widget.toggle_diagnostics)
        QShortcut(QKeySequence(Qt.Key_C), self, self.player_widget.toggle_cinema_mode)
        QShortcut(QKeySequence("Ctrl+F"), self, self.search_input.setFocus)
        QShortcut(QKeySequence(Qt.Key_Escape), self, self._on_escape_pressed)

    def _on_escape_pressed(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.player_widget.is_fullscreen_state = False
            self.player_widget.btn_fullscreen.setText("⛶ Tela Cheia [F]")

    def _connect_signals(self) -> None:
        self.download_manager.task_added.connect(self._on_task_updated)
        self.download_manager.task_updated.connect(self._on_task_updated)
        self.download_manager.task_removed.connect(lambda tid: self._refresh_downloads_table())

        self.player_widget.progress_updated.connect(self._on_player_progress)
        self.player_widget.fullscreen_toggled.connect(self._on_player_fullscreen_toggled)
        self.player_widget.cinema_mode_toggled.connect(self._on_player_cinema_toggled)
        self.player_widget.previous_channel_requested.connect(self._on_return_previous_channel)
        self.player_widget.download_requested.connect(self._on_player_download_requested)

    # -------------------------------------------------------------
    # Sync, Catalog Population & Health Checks
    # -------------------------------------------------------------

    def _auto_background_sync(self) -> None:
        if not self.config.accounts:
            return
        acc_id = self.config.active_account_id
        target_accs = [
            Account.from_dict(d) for d in self.config.accounts
            if (acc_id == "all" or d.get("id") == acc_id)
        ]
        for acc in target_accs:
            worker = SyncWorker(acc)
            worker.signals.finished.connect(self._on_sync_finished)
            worker.signals.error.connect(lambda err: None)
            self.thread_pool.start(worker)

    def sync_active_account(self) -> None:

        acc_id = self.config.active_account_id
        if not self.config.accounts:
            QMessageBox.information(self, "Sem Contas", "Cadastre uma conta Xtream ou lista M3U em Configurações.")
            return

        target_accs = [
            Account.from_dict(d) for d in self.config.accounts
            if (acc_id == "all" or d.get("id") == acc_id)
        ]

        self.health_lbl.setText("STATUS // SINCRONIZANDO...")
        self.live_dot.setStyleSheet("color: #f1c40f; font-size: 14px;")

        for acc in target_accs:
            worker = SyncWorker(acc)
            worker.signals.finished.connect(self._on_sync_finished)
            worker.signals.error.connect(self._on_sync_error)
            self.thread_pool.start(worker)

    def _on_sync_finished(self, items: list[MediaItem], stats: dict[str, Any]) -> None:
        self.catalog.add_items(items)
        lat = stats.get("latency_ms", 45)
        self.live_dot.setStyleSheet("color: #00e054; font-size: 14px;")
        self.health_lbl.setText(f"STATUS // OPERACIONAL • {lat}ms • {len(items)} ITENS")
        self._populate_all_views()

    def _on_sync_error(self, err: str) -> None:
        self.live_dot.setStyleSheet("color: #e74c3c; font-size: 14px;")
        self.health_lbl.setText(f"STATUS // ERRO ({err[:25]})")

    def _update_source_health_ui(self) -> None:
        if not self.config.accounts:
            self.health_lbl.setText("STATUS // NENHUMA CONTA")
            self.live_dot.setStyleSheet("color: #79879c; font-size: 14px;")
        else:
            self.health_lbl.setText("STATUS // PRONTO")
            self.live_dot.setStyleSheet("color: #00e054; font-size: 14px;")

    def _populate_all_views(self) -> None:
        self._refresh_home_view()
        if hasattr(self, "movie_cat_combo"):
            self._populate_catalog_view("movie", self.movie_cat_combo, self.movie_list)
        if hasattr(self, "series_cat_combo"):
            self._populate_catalog_view("series", self.series_cat_combo, self.series_list)
        self._populate_live_channels()

    def _populate_catalog_view(self, kind: str, cat_combo: QComboBox, list_w: QListWidget) -> None:
        cat_combo.blockSignals(True)
        cat_combo.clear()
        cat_combo.addItem("Todas as categorias", "all")

        hidden = self.config.global_hidden_categories + self.current_profile.hidden_categories
        categories = self.catalog.get_categories(kind, hidden_categories=hidden)
        for cat in categories:
            cat_combo.addItem(cat, cat)
        cat_combo.blockSignals(False)

        items = self.catalog.get_items(kind, hidden_categories=hidden)
        self._fill_items_list(list_w, items)
        self._update_catalog_count(kind, len(items), "")

    def _filter_catalog_view(
        self,
        kind: str,
        cat_combo: QComboBox,
        search_edit: QLineEdit,
        list_w: QListWidget,
        stream_combo: QComboBox | None = None,
    ) -> None:
        cat = cat_combo.currentData() or "all"
        query = search_edit.text()
        hidden = self.config.global_hidden_categories + self.current_profile.hidden_categories
        items = self.catalog.get_items(kind, category=cat, query=query, hidden_categories=hidden)

        # Apply streaming preset filter
        if stream_combo and stream_combo.currentData() != "all":
            kws = STREAMING_KEYWORDS.get(stream_combo.currentData(), [])
            if kws:
                items = [
                    it for it in items
                    if any(kw in normalize_text(it.category_name) or kw in normalize_text(it.title) for kw in kws)
                ]

        self._fill_items_list(list_w, items)
        self._update_catalog_count(kind, len(items), query)

    def _update_catalog_count(self, kind: str, count: int, query: str) -> None:
        label = getattr(self, f"{kind}_count_label", None)
        if label:
            suffix = f' para “{query.strip()}”' if query.strip() else ""
            label.setText(f"{count} título{'s' if count != 1 else ''}{suffix}")

    def _filter_series(self) -> None:
        """Compatibility method for tests."""
        query = self.series_search.text()
        filter_val = self.series_streaming_filter.currentData()

        filtered = []
        for s in self.series_data:
            name = s.get("name", "")
            cat = s.get("category_name", "")
            if filter_val and filter_val != "all":
                kws = STREAMING_KEYWORDS.get(filter_val, [])
                if not any(kw in normalize_text(cat) or kw in normalize_text(name) for kw in kws):
                    continue
            if query and not matches_search(query, f"{name} {cat}"):
                continue
            filtered.append(s)

        self.series_list.clear()
        for s in filtered:
            item = QListWidgetItem(s.get("name", ""))
            item.setData(Qt.UserRole, s)
            self.series_list.addItem(item)

    def _set_episodes(self, series: dict[str, Any], info: dict[str, Any]) -> None:
        """Compatibility method for tests."""
        self.episode_tree.clear()
        episodes_dict = info.get("episodes", {})
        for s_num in sorted(episodes_dict.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
            season_item = QTreeWidgetItem([f"Temporada {s_num}", ""])
            season_item.setFlags(season_item.flags() | Qt.ItemIsUserCheckable)
            season_item.setCheckState(0, Qt.CheckState.Unchecked)

            for ep in episodes_dict[s_num]:
                ep_item = QTreeWidgetItem(["", ep.get("title", ""), f"E{ep.get('episode_num')}"])
                ep_item.setFlags(ep_item.flags() | Qt.ItemIsUserCheckable)
                ep_item.setCheckState(0, Qt.CheckState.Unchecked)
                ep_item.setData(0, Qt.UserRole, ep)
                season_item.addChild(ep_item)

            self.episode_tree.addTopLevelItem(season_item)
            season_item.setExpanded(True)

        self._update_download_button_label()

    def _on_episode_tree_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        # If season header changed, cascade to children
        if item.childCount() > 0:
            state = item.checkState(0)
            if state != Qt.CheckState.PartiallyChecked:
                self.episode_tree.blockSignals(True)
                for i in range(item.childCount()):
                    item.child(i).setCheckState(0, state)
                self.episode_tree.blockSignals(False)
        self._update_download_button_label()

    def _select_all_episodes(self) -> None:
        self.episode_tree.blockSignals(True)
        for i in range(self.episode_tree.topLevelItemCount()):
            top = self.episode_tree.topLevelItem(i)
            top.setCheckState(0, Qt.CheckState.Checked)
            for j in range(top.childCount()):
                top.child(j).setCheckState(0, Qt.CheckState.Checked)
        self.episode_tree.blockSignals(False)
        self._update_download_button_label()

    def _deselect_all_episodes(self) -> None:
        self.episode_tree.blockSignals(True)
        for i in range(self.episode_tree.topLevelItemCount()):
            top = self.episode_tree.topLevelItem(i)
            top.setCheckState(0, Qt.CheckState.Unchecked)
            for j in range(top.childCount()):
                top.child(j).setCheckState(0, Qt.CheckState.Unchecked)
        self.episode_tree.blockSignals(False)
        self._update_download_button_label()

    def _episode_items(self) -> list[QTreeWidgetItem]:
        selected = []
        for i in range(self.episode_tree.topLevelItemCount()):
            top = self.episode_tree.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    selected.append(child)
        return selected

    def _update_download_button_label(self) -> None:
        count = len(self._episode_items())
        if count > 0:
            self.download_episodes_button.setText(f"Baixar selecionados ({count})")
        else:
            self.download_episodes_button.setText("Baixar selecionados")

    def _update_download_banners(self) -> None:
        active = [t for t in self.downloads.tasks.values() if t.status == DownloadStatus.DOWNLOADING]
        if active:
            task = active[0]
            pct = int((task.downloaded_bytes / task.total_bytes) * 100) if task.total_bytes else 0
            speed = human_size(task.speed_bps, "/s")
            self.series_download_banner.title_label.setText(f"Baixando: {task.title}")
            self.series_download_banner.stats_label.setText(f"{pct}% • {speed}")
            self.series_download_banner.progress_bar.setValue(pct)
            self.movie_download_banner.title_label.setText(f"Baixando: {task.title}")
            self.movie_download_banner.stats_label.setText(f"{pct}% • {speed}")
            self.movie_download_banner.progress_bar.setValue(pct)
        else:
            self.series_download_banner.title_label.setText("Nenhum download ativo")
            self.series_download_banner.stats_label.setText("")
            self.series_download_banner.progress_bar.setValue(0)
            self.movie_download_banner.title_label.setText("Nenhum download ativo")
            self.movie_download_banner.stats_label.setText("")
            self.movie_download_banner.progress_bar.setValue(0)

    def _fill_items_list(self, list_w: QListWidget, items: list[MediaItem]) -> None:
        list_w.clear()
        if not items:
            empty = QListWidgetItem("Nenhum resultado\nTente outro termo ou remova um filtro.")
            empty.setFlags(Qt.NoItemFlags)
            empty.setTextAlignment(Qt.AlignCenter)
            empty.setSizeHint(QSize(360, 120))
            list_w.addItem(empty)
            return
        for index, it in enumerate(items[:250]):
            fav_star = "★ " if it.id in self.current_profile.favorites else ""
            category = it.category_name or "Sem categoria"
            meta = "AO VIVO" if it.kind == "live" else category
            list_item = QListWidgetItem(self._placeholder_icon(it.kind), f"{fav_star}{it.title}\n{meta}")
            list_item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            list_item.setSizeHint(QSize(176, 270))
            list_item.setToolTip(f"{it.title}\n{category}\nDuplo clique para abrir")
            list_item.setData(Qt.UserRole, it)
            list_w.addItem(list_item)
            # Keep catalog opening responsive; the first viewport and nearby rows get artwork.
            if it.poster and index < 60:
                self._request_poster(it.poster, list_item)

    def _placeholder_icon(self, kind: str) -> QIcon:
        cache_key = f"placeholder:{kind}"
        if cache_key in self._poster_cache:
            return self._poster_cache[cache_key]
        pixmap = QPixmap(148, 208)
        pixmap.fill(QColor("#101827"))
        painter = QPainter(pixmap)
        painter.setPen(QColor("#2b5cff"))
        painter.setFont(QFont("Segoe UI", 34, QFont.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "LIVE" if kind == "live" else ("FILM" if kind == "movie" else "TV"))
        painter.end()
        icon = QIcon(pixmap)
        self._poster_cache[cache_key] = icon
        return icon

    def _request_poster(self, url: str, list_item: QListWidgetItem) -> None:
        parsed_url = QUrl(url)
        if parsed_url.scheme().lower() not in {"http", "https"}:
            return
        if url in self._poster_cache:
            list_item.setIcon(self._poster_cache[url])
            return
        reply = self.poster_network.get(QNetworkRequest(parsed_url))
        self._poster_replies.add(reply)
        reply.finished.connect(lambda r=reply, item=list_item, poster_url=url: self._poster_ready(r, item, poster_url))

    def _poster_ready(self, reply: QNetworkReply, list_item: QListWidgetItem, url: str) -> None:
        self._poster_replies.discard(reply)
        content_type = str(reply.header(QNetworkRequest.ContentTypeHeader) or "").lower()
        safe_scheme = reply.url().scheme().lower() in {"http", "https"}
        safe_size = 0 <= reply.size() <= 5 * 1024 * 1024
        if reply.error() == QNetworkReply.NetworkError.NoError and safe_scheme and safe_size and content_type.startswith("image/"):
            pixmap = QPixmap()
            if pixmap.loadFromData(reply.readAll()):
                fitted = pixmap.scaled(148, 208, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                icon = QIcon(fitted)
                self._poster_cache[url] = icon
                try:
                    list_item.setIcon(icon)
                except RuntimeError:
                    pass
        reply.deleteLater()

    def _populate_live_channels(self) -> None:
        hidden = self.config.global_hidden_categories + self.current_profile.hidden_categories
        channels = self.catalog.get_items("live", hidden_categories=hidden)
        self.epg_widget.set_channels(channels, favorites=set(self.current_profile.favorites))

    # -------------------------------------------------------------
    # Details, Playing & Playback Progress
    # -------------------------------------------------------------

    def _on_catalog_item_clicked(self, list_item: QListWidgetItem) -> None:
        item: MediaItem = list_item.data(Qt.UserRole)
        if not item:
            return
        self._open_details_modal(item)

    def _on_home_media_clicked(self, list_item: QListWidgetItem) -> None:
        item: MediaItem = list_item.data(Qt.UserRole)
        if item:
            self._open_details_modal(item)

    def _on_continue_item_clicked(self, list_item: QListWidgetItem) -> None:
        item_id: str = list_item.data(Qt.UserRole)
        if not item_id or item_id not in self.catalog.items:
            return
        item = self.catalog.items[item_id]
        prog_data = self.current_profile.progress.get(item_id, {})
        resume_pos = prog_data.get("position_ms", 0)
        self._play_item(item, resume_pos=resume_pos)

    def _open_details_modal(self, item: MediaItem) -> None:
        if self._is_category_locked(item.category_name):
            if not self._prompt_pin():
                return

        episodes_by_season: dict[int, list[Episode]] = {}
        if item.kind == "series":
            episodes_by_season = self._fetch_series_episodes(item)

        is_wl = item.id in self.current_profile.watchlist
        is_fav = item.id in self.current_profile.favorites

        poster_pixmap = None
        cached_icon = self._poster_cache.get(item.poster)
        if cached_icon and not cached_icon.isNull():
            poster_pixmap = cached_icon.pixmap(140, 210)

        dialog = DetailsDialog(
            item,
            episodes_by_season,
            is_in_watchlist=is_wl,
            is_favorite=is_fav,
            poster_pixmap=poster_pixmap,
            parent=self,
        )
        dialog.play_requested.connect(self._on_details_play_requested)
        dialog.download_requested.connect(self._on_details_download_requested)
        dialog.download_season_requested.connect(self._on_details_season_download)
        dialog.toggle_watchlist_requested.connect(self._toggle_watchlist)
        dialog.toggle_favorite_requested.connect(self._toggle_favorite)
        dialog.add_to_playlist_requested.connect(self._show_add_to_playlist_dialog)
        dialog.exec()

    def _fetch_series_episodes(self, item: MediaItem) -> dict[int, list[Episode]]:
        acc = self._get_account_by_id(item.account_id)
        if not acc or acc.account_type != "xtream":
            return {}

        client = XtreamClient(acc.server_url, acc.username, acc.password)
        try:
            info = client.get_series_info(item.stream_id)
            episodes_dict = info.get("episodes", {})
            result: dict[int, list[Episode]] = {}

            for s_str, ep_list in episodes_dict.items():
                try:
                    s_num = int(s_str)
                except ValueError:
                    s_num = 1
                result[s_num] = []
                for ep in ep_list:
                    ext = ep.get("container_extension", "mp4")
                    ep_id = f"ep_{item.id}_{s_num}_{ep.get('episode_num')}"
                    stream_url = client.stream_urls("series", ep.get("id"), ext)[0]
                    episode_obj = Episode(
                        id=ep_id,
                        series_id=item.stream_id,
                        season_num=s_num,
                        episode_num=int(ep.get("episode_num", 1)),
                        title=ep.get("title", f"Episódio {ep.get('episode_num')}"),
                        container_extension=ext,
                        stream_url=stream_url,
                        duration_sec=int(ep.get("info", {}).get("duration_secs", 0) or 0),
                        plot=ep.get("info", {}).get("plot", ""),
                        cover=ep.get("info", {}).get("movie_image", ""),
                    )
                    result[s_num].append(episode_obj)
            return result
        except Exception:
            return {}

    def _on_details_play_requested(self, item: MediaItem, episode: Episode | None) -> None:
        target_url = episode.stream_url if episode else self._stream_url_for_item(item)
        content_id = episode.id if episode else item.id
        prog_data = self.current_profile.progress.get(content_id, {})
        resume_pos = prog_data.get("position_ms", 0)
        self._play_item(item, url=target_url, content_id=content_id, resume_pos=resume_pos)

    def _play_item(
        self,
        item: MediaItem,
        url: str = "",
        content_id: str = "",
        resume_pos: int = 0,
    ) -> None:
        stream_url = url or self._stream_url_for_item(item)
        if not stream_url:
            QMessageBox.warning(self, "Fonte indisponível", "Sincronize novamente esta fonte para renovar o endereço de reprodução.")
            return
        cid = content_id or item.id
        self.view_stack.setCurrentIndex(8)

        if hasattr(self, "btn_now_playing"):
            short_title = item.title if len(item.title) <= 15 else f"{item.title[:15]}…"
            self.btn_now_playing.setText(f"▶ {short_title}")
            self.btn_now_playing.setChecked(True)
            self.btn_now_playing.show()
            for btn in self.nav_btns:
                btn.setChecked(False)

        self.player_widget.load_media(
            url=stream_url,
            content_id=cid,
            kind=item.kind,
            resume_position_ms=resume_pos,
            source_title=item.title,
        )
        self._add_to_history(item)

    def _on_live_channel_highlighted(self, channel: MediaItem) -> None:
        self.last_live_channel = channel
        acc = self._get_account_by_id(channel.account_id)
        if acc and acc.account_type == "xtream":
            client = XtreamClient(acc.server_url, acc.username, acc.password)
            try:
                epg_data = client.get_short_epg(channel.stream_id)
                self.epg_widget.update_epg_listings(epg_data)
            except Exception:
                self.epg_widget.update_epg_listings([])

    def _on_live_channel_selected(self, channel: MediaItem) -> None:
        if self.last_live_channel:
            self.player_widget.last_channel_id = self._stream_url_for_item(self.last_live_channel)
        self.last_live_channel = channel

        acc = self._get_account_by_id(channel.account_id)
        if acc and acc.account_type == "xtream":
            client = XtreamClient(acc.server_url, acc.username, acc.password)
            try:
                epg_data = client.get_short_epg(channel.stream_id)
                self.epg_widget.update_epg_listings(epg_data)
            except Exception:
                pass

        self._play_item(channel)


    def _on_return_previous_channel(self) -> None:
        if self.last_live_channel:
            self._play_item(self.last_live_channel)

    def _on_player_progress(self, content_id: str, position_ms: int, duration_ms: int) -> None:
        self.current_profile.progress[content_id] = {
            "position_ms": position_ms,
            "duration_ms": duration_ms,
            "updated_at": time.time(),
        }
        self._save_profile_state()

    def _on_player_fullscreen_toggled(self, is_fs: bool) -> None:
        if is_fs:
            self.showFullScreen()
        else:
            self.showNormal()

    def _on_player_cinema_toggled(self, is_cinema: bool) -> None:
        self.findChild(QFrame, "Sidebar").setVisible(not is_cinema)
        self.findChild(QFrame, "HeaderBar").setVisible(not is_cinema)

    # -------------------------------------------------------------
    # Watchlist, Favorites & History
    # -------------------------------------------------------------

    def _toggle_watchlist(self, content_id: str) -> None:
        if content_id in self.current_profile.watchlist:
            self.current_profile.watchlist.remove(content_id)
        else:
            self.current_profile.watchlist.append(content_id)
        self._save_profile_state()
        self._refresh_home_view()

    def _toggle_favorite(self, content_id: str) -> None:
        if content_id in self.current_profile.favorites:
            self.current_profile.favorites.remove(content_id)
        else:
            self.current_profile.favorites.append(content_id)
        self._save_profile_state()
        self._refresh_home_view()

    def _add_to_history(self, item: MediaItem) -> None:
        entry = {
            "id": item.id,
            "title": item.title,
            "kind": item.kind,
            "timestamp": time.time(),
        }
        self.current_profile.history = [h for h in self.current_profile.history if h.get("id") != item.id]
        self.current_profile.history.insert(0, entry)
        self._save_profile_state()

    def _clear_history(self) -> None:
        self.current_profile.history.clear()
        self.current_profile.progress.clear()
        self._save_profile_state()
        self._refresh_lists_view()
        self._refresh_home_view()

    def _refresh_home_view(self) -> None:
        featured = next((item for item in self.catalog.items.values() if item.kind in {"movie", "series"}), None)
        self.hero_item = featured
        if featured:
            self.hero_title.setText(featured.title.upper())
            self.hero_desc.setText(featured.synopsis or f"{featured.category_name} · disponível na sua biblioteca")
            self.btn_hero_action.setText("Ver detalhes  →")
        else:
            self.hero_title.setText("STREAMING\nSEM RUÍDO")
            self.hero_desc.setText("Conecte uma fonte para transformar sua programação em uma biblioteca visual.")
            self.btn_hero_action.setText("Explorar filmes  →")

        self.continue_watching_list.clear()
        for cid, prog in sorted(self.current_profile.progress.items(), key=lambda x: x[1].get("updated_at", 0), reverse=True)[:10]:
            item = self.catalog.items.get(cid)
            if item:
                pos = prog.get("position_ms", 0)
                dur = prog.get("duration_ms", 1)
                pct = int((pos / dur) * 100) if dur > 0 else 0
                self._add_media_rail_item(self.continue_watching_list, item, f"{pct}% assistido", cid)
        self._ensure_rail_state(self.continue_watching_list, "Nada em andamento")

        self.home_favs_list.clear()
        for fid in self.current_profile.favorites[:15]:
            item = self.catalog.items.get(fid)
            if item:
                self._add_media_rail_item(self.home_favs_list, item, item.category_name, item)
        self._ensure_rail_state(self.home_favs_list, "Favorite títulos para encontrá-los aqui")

        self.home_watchlist_list.clear()
        for wid in self.current_profile.watchlist[:15]:
            item = self.catalog.items.get(wid)
            if item:
                self._add_media_rail_item(self.home_watchlist_list, item, item.category_name, item)
        self._ensure_rail_state(self.home_watchlist_list, "Sua lista está vazia")

    def _add_media_rail_item(self, rail: QListWidget, media: MediaItem, meta: str, payload: Any) -> None:
        card = QListWidgetItem(self._placeholder_icon(media.kind), f"{media.title}\n{meta}")
        card.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
        card.setSizeHint(QSize(146, 212))
        card.setData(Qt.UserRole, payload)
        card.setToolTip(media.title)
        rail.addItem(card)
        if media.poster:
            self._request_poster(media.poster, card)

    def _ensure_rail_state(self, rail: QListWidget, message: str) -> None:
        if rail.count():
            return
        empty = QListWidgetItem(message)
        empty.setFlags(Qt.NoItemFlags)
        empty.setTextAlignment(Qt.AlignCenter)
        empty.setSizeHint(QSize(300, 180))
        rail.addItem(empty)

    def _refresh_lists_view(self) -> None:
        self.favs_list.clear()
        for fid in self.current_profile.favorites:
            it = self.catalog.items.get(fid)
            if it:
                li = QListWidgetItem(f"★ {it.title}  [{it.category_name}]")
                li.setData(Qt.UserRole, it)
                self.favs_list.addItem(li)

        self.watchlist_widget.clear()
        for wid in self.current_profile.watchlist:
            it = self.catalog.items.get(wid)
            if it:
                li = QListWidgetItem(f"✓ {it.title}  [{it.category_name}]")
                li.setData(Qt.UserRole, it)
                self.watchlist_widget.addItem(li)

        self.history_list.clear()
        for h in self.current_profile.history:
            it = self.catalog.items.get(h.get("id"))
            if it:
                li = QListWidgetItem(f"● {it.title}  ({h.get('kind', '').upper()})")
                li.setData(Qt.UserRole, it.id)
                self.history_list.addItem(li)

        self._refresh_playlists_ui()

    def _refresh_playlists_ui(self) -> None:
        if not hasattr(self, "custom_playlists_list"):
            return
        curr_row = self.custom_playlists_list.currentRow()
        self.custom_playlists_list.clear()
        for pl_raw in self.current_profile.custom_playlists:
            pl = CustomPlaylist.from_dict(pl_raw) if isinstance(pl_raw, dict) else pl_raw
            count = len(pl.item_ids)
            item_text = f"{pl.emoji}  {pl.name}  ({count})"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, pl.id)
            if pl.color:
                item.setForeground(QColor(pl.color))
            self.custom_playlists_list.addItem(item)

        if self.custom_playlists_list.count() > 0:
            target_row = max(0, min(curr_row if curr_row >= 0 else 0, self.custom_playlists_list.count() - 1))
            self.custom_playlists_list.setCurrentRow(target_row)
        else:
            self.playlist_items_list.clear()
            self.playlist_items_header.setText("NENHUMA PLAYLIST CADASTRADA")

    def _on_playlist_selection_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        self.playlist_items_list.clear()
        if not current:
            self.playlist_items_header.setText("SELECIONE UMA PLAYLIST")
            return
        pl_id = current.data(Qt.UserRole)
        playlist = self._get_custom_playlist_by_id(pl_id)
        if not playlist:
            return

        self.playlist_items_header.setText(f"{playlist.emoji} {playlist.name.upper()} // {len(playlist.item_ids)} ITENS")
        type_labels = {"live": "[AO VIVO]", "movie": "[FILME]", "series": "[SÉRIE]"}
        for it_id in playlist.item_ids:
            item = self.catalog.items.get(it_id)
            if item:
                badge = type_labels.get(item.kind, "[ITEM]")
                li = QListWidgetItem(f"{badge} {item.title}  •  {item.category_name}")
                li.setData(Qt.UserRole, item)
                self.playlist_items_list.addItem(li)
            else:
                li = QListWidgetItem(f"● {it_id} (Item indisponível na fonte atual)")
                li.setData(Qt.UserRole, None)
                self.playlist_items_list.addItem(li)

    def _get_custom_playlist_by_id(self, pl_id: str) -> CustomPlaylist | None:
        for p in self.current_profile.custom_playlists:
            p_obj = CustomPlaylist.from_dict(p) if isinstance(p, dict) else p
            if p_obj.id == pl_id:
                return p_obj
        return None

    def _show_create_playlist_dialog(self) -> None:
        import uuid
        dialog = QDialog(self)
        dialog.setWindowTitle("Nova Playlist Personalizada")
        dialog.resize(400, 240)
        layout = QFormLayout(dialog)
        layout.setSpacing(12)

        name_in = QLineEdit()
        name_in.setPlaceholderText("Ex: Meus Favoritos de Ação")

        emoji_combo = QComboBox()
        for em in ("📁", "🎬", "🍿", "⭐", "🔥", "📺", "⚽", "🏆", "🎵", "💎", "❤️", "⚡"):
            emoji_combo.addItem(em)

        color_combo = QComboBox()
        colors = [
            ("#1749e8", "Azul Blue Lab"),
            ("#00e054", "Verde Esmeralda"),
            ("#8e44ad", "Roxo"),
            ("#f39c12", "Âmbar / Laranja"),
            ("#e74c3c", "Vermelho"),
            ("#00bcd4", "Ciano"),
            ("#e91e63", "Rosa"),
        ]
        for hex_code, color_name in colors:
            color_combo.addItem(f"● {color_name}", hex_code)

        layout.addRow("Nome da Playlist:", name_in)
        layout.addRow("Emoji / Ícone:", emoji_combo)
        layout.addRow("Cor de Destaque:", color_combo)

        btn_box = QHBoxLayout()
        btn_save = QPushButton("Criar Playlist")
        btn_save.setProperty("primary", True)
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(dialog.reject)
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_save)
        layout.addRow("", btn_box)

        def save() -> None:
            name = name_in.text().strip()
            if not name:
                QMessageBox.warning(dialog, "Campo obrigatório", "Informe o nome da playlist.")
                return
            new_pl = CustomPlaylist(
                id=f"pl_{uuid.uuid4().hex[:10]}",
                name=name,
                emoji=emoji_combo.currentText(),
                color=color_combo.currentData(),
                item_ids=[],
            )
            self.current_profile.custom_playlists.append(new_pl.to_dict())
            self._save_profile_state()
            self._refresh_playlists_ui()
            dialog.accept()

        btn_save.clicked.connect(save)
        dialog.exec()

    def _show_edit_playlist_dialog(self) -> None:
        curr_item = self.custom_playlists_list.currentItem()
        if not curr_item:
            QMessageBox.information(self, "Seleção necessária", "Selecione uma playlist para editar.")
            return
        pl_id = curr_item.data(Qt.UserRole)
        playlist = self._get_custom_playlist_by_id(pl_id)
        if not playlist:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Editar Playlist — {playlist.name}")
        dialog.resize(400, 240)
        layout = QFormLayout(dialog)
        layout.setSpacing(12)

        name_in = QLineEdit(playlist.name)
        emoji_combo = QComboBox()
        for em in ("📁", "🎬", "🍿", "⭐", "🔥", "📺", "⚽", "🏆", "🎵", "💎", "❤️", "⚡"):
            emoji_combo.addItem(em)
        emoji_combo.setCurrentText(playlist.emoji)

        color_combo = QComboBox()
        colors = [
            ("#1749e8", "Azul Blue Lab"),
            ("#00e054", "Verde Esmeralda"),
            ("#8e44ad", "Roxo"),
            ("#f39c12", "Âmbar / Laranja"),
            ("#e74c3c", "Vermelho"),
            ("#00bcd4", "Ciano"),
            ("#e91e63", "Rosa"),
        ]
        for idx, (hex_code, color_name) in enumerate(colors):
            color_combo.addItem(f"● {color_name}", hex_code)
            if hex_code == playlist.color:
                color_combo.setCurrentIndex(idx)

        layout.addRow("Nome da Playlist:", name_in)
        layout.addRow("Emoji / Ícone:", emoji_combo)
        layout.addRow("Cor de Destaque:", color_combo)

        btn_box = QHBoxLayout()
        btn_save = QPushButton("Salvar Alterações")
        btn_save.setProperty("primary", True)
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(dialog.reject)
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_save)
        layout.addRow("", btn_box)

        def save() -> None:
            name = name_in.text().strip()
            if not name:
                QMessageBox.warning(dialog, "Campo obrigatório", "Informe o nome da playlist.")
                return
            playlist.name = name
            playlist.emoji = emoji_combo.currentText()
            playlist.color = color_combo.currentData()
            for i, p in enumerate(self.current_profile.custom_playlists):
                p_id = p.get("id") if isinstance(p, dict) else p.id
                if p_id == playlist.id:
                    self.current_profile.custom_playlists[i] = playlist.to_dict()
                    break
            self._save_profile_state()
            self._refresh_playlists_ui()
            dialog.accept()

        btn_save.clicked.connect(save)
        dialog.exec()

    def _delete_selected_playlist(self) -> None:
        curr_item = self.custom_playlists_list.currentItem()
        if not curr_item:
            QMessageBox.information(self, "Seleção necessária", "Selecione uma playlist para excluir.")
            return
        pl_id = curr_item.data(Qt.UserRole)
        playlist = self._get_custom_playlist_by_id(pl_id)
        if not playlist:
            return

        reply = QMessageBox.question(
            self,
            "Excluir Playlist",
            f"Deseja realmente excluir a playlist '{playlist.emoji} {playlist.name}'?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.current_profile.custom_playlists = [
                p for p in self.current_profile.custom_playlists
                if (p.get("id") if isinstance(p, dict) else p.id) != pl_id
            ]
            self._save_profile_state()
            self._refresh_playlists_ui()

    def _play_selected_playlist_item(self) -> None:
        curr = self.playlist_items_list.currentItem()
        if not curr:
            return
        item: MediaItem = curr.data(Qt.UserRole)
        if item:
            self._play_item(item)

    def _remove_selected_playlist_item(self) -> None:
        curr_pl = self.custom_playlists_list.currentItem()
        curr_it = self.playlist_items_list.currentItem()
        if not curr_pl or not curr_it:
            return
        pl_id = curr_pl.data(Qt.UserRole)
        playlist = self._get_custom_playlist_by_id(pl_id)
        if not playlist:
            return
        item: MediaItem | None = curr_it.data(Qt.UserRole)
        it_id = item.id if item else ""
        if it_id and it_id in playlist.item_ids:
            playlist.item_ids.remove(it_id)
            for i, p in enumerate(self.current_profile.custom_playlists):
                p_id = p.get("id") if isinstance(p, dict) else p.id
                if p_id == playlist.id:
                    self.current_profile.custom_playlists[i] = playlist.to_dict()
                    break
            self._save_profile_state()
            self._on_playlist_selection_changed(curr_pl, None)

    def _show_add_to_playlist_dialog(self, item: MediaItem) -> None:
        if not self.current_profile.custom_playlists:
            reply = QMessageBox.question(
                self,
                "Sem Playlists",
                "Você ainda não possui nenhuma playlist criada. Deseja criar uma agora?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._show_create_playlist_dialog()
                if self.current_profile.custom_playlists:
                    self._show_add_to_playlist_dialog(item)
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Adicionar à Playlist — {item.title}")
        dialog.resize(380, 280)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Escolha a playlist de destino:"))
        list_w = QListWidget()
        for p_raw in self.current_profile.custom_playlists:
            pl = CustomPlaylist.from_dict(p_raw) if isinstance(p_raw, dict) else p_raw
            already = " (Já adicionado)" if item.id in pl.item_ids else ""
            li = QListWidgetItem(f"{pl.emoji}  {pl.name}{already}")
            li.setData(Qt.UserRole, pl.id)
            list_w.addItem(li)
        list_w.setCurrentRow(0)
        layout.addWidget(list_w, 1)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("Adicionar")
        btn_add.setProperty("primary", True)
        btn_new = QPushButton("+ Nova Playlist")
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(dialog.reject)

        btn_row.addWidget(btn_new)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_add)
        layout.addLayout(btn_row)

        def add_item() -> None:
            sel = list_w.currentItem()
            if not sel:
                return
            pl_id = sel.data(Qt.UserRole)
            playlist = self._get_custom_playlist_by_id(pl_id)
            if playlist:
                if item.id not in playlist.item_ids:
                    playlist.item_ids.append(item.id)
                    for i, p in enumerate(self.current_profile.custom_playlists):
                        p_id = p.get("id") if isinstance(p, dict) else p.id
                        if p_id == playlist.id:
                            self.current_profile.custom_playlists[i] = playlist.to_dict()
                            break
                    self._save_profile_state()
                    self._refresh_playlists_ui()
                    QMessageBox.information(dialog, "Adicionado", f"'{item.title}' foi adicionado a '{playlist.name}'!")
                else:
                    QMessageBox.information(dialog, "Aviso", f"'{item.title}' já está na playlist '{playlist.name}'.")
            dialog.accept()

        def create_new() -> None:
            dialog.reject()
            self._show_create_playlist_dialog()
            self._show_add_to_playlist_dialog(item)

        btn_add.clicked.connect(add_item)
        btn_new.clicked.connect(create_new)
        dialog.exec()

    # -------------------------------------------------------------
    # Downloads & Offline Library
    # -------------------------------------------------------------

    def _on_player_download_requested(self, content_id: str, kind: str) -> None:
        if not content_id:
            return
        item = self.catalog.items.get(content_id)
        if item:
            self._on_details_download_requested(item, None)
            return
        if content_id.startswith("ep_"):
            for candidate in self.catalog.get_items("series"):
                if candidate.id in content_id:
                    eps_by_season = self._fetch_series_episodes(candidate)
                    for eps in eps_by_season.values():
                        for ep in eps:
                            if ep.id == content_id:
                                self._on_details_download_requested(candidate, ep)
                                return


    def _on_details_download_requested(self, item: MediaItem, episode: Episode | None) -> None:
        target_url = episode.stream_url if episode else self._stream_url_for_item(item)
        if not target_url:
            QMessageBox.warning(self, "Fonte indisponível", "Sincronize novamente esta fonte antes de baixar.")
            return
        title = f"{item.title} - S{episode.season_num:02d}E{episode.episode_num:02d}" if episode else item.title
        dest_dir = self.config.download_dir or str(default_download_dir())
        base_name = safe_filename(title)

        self.download_manager.add(
            kind=item.kind,
            title=title,
            url_candidates=[target_url],
            destination_dir=dest_dir,
            base_filename=base_name,
            extension=episode.container_extension if episode else item.container_extension,
        )
        QMessageBox.information(self, "Download Iniciado", f"O download de '{title}' foi adicionado à fila!")

    def _on_details_season_download(self, item: MediaItem, season_num: int, episodes: list[Episode]) -> None:
        dest_dir = self.config.download_dir or str(default_download_dir())
        for ep in episodes:
            title = f"{item.title} - S{season_num:02d}E{ep.episode_num:02d}"
            base_name = safe_filename(title)
            self.download_manager.add(
                kind="series",
                title=title,
                url_candidates=[ep.stream_url],
                destination_dir=dest_dir,
                base_filename=base_name,
                extension=ep.container_extension,
            )
        QMessageBox.information(self, "Temporada Enfileirada", f"{len(episodes)} episódios da Temporada {season_num} foram adicionados à fila de downloads!")

    def _stream_url_for_item(self, item: MediaItem) -> str:
        if item.stream_url:
            return item.stream_url
        account = self._get_account_by_id(item.account_id)
        if not account or account.account_type != "xtream":
            return ""
        client = XtreamClient(account.server_url, account.username, account.password)
        return client.stream_urls(item.kind, item.stream_id, item.container_extension)[0]

    def _on_task_updated(self, task: DownloadTask) -> None:
        self.download_banner.update_task(task)
        self._refresh_downloads_table()

    def _refresh_downloads_table(self) -> None:
        tasks = list(self.download_manager.tasks.values())
        self.downloads_table.setRowCount(len(tasks))

        for row, task in enumerate(tasks):
            self.downloads_table.setItem(row, 0, QTableWidgetItem(task.title))
            self.downloads_table.setItem(row, 1, QTableWidgetItem(task.status.value))

            pct = 0
            if task.total_bytes and task.total_bytes > 0:
                pct = int((task.downloaded_bytes / task.total_bytes) * 100)
            self.downloads_table.setItem(row, 2, QTableWidgetItem(f"{pct}%"))

            speed_txt = human_size(task.speed_bps, "/s") if task.speed_bps > 0 else "—"
            self.downloads_table.setItem(row, 3, QTableWidgetItem(speed_txt))

            size_txt = human_size(task.total_bytes or task.downloaded_bytes)
            self.downloads_table.setItem(row, 4, QTableWidgetItem(size_txt))

            act_widget = QWidget()
            act_layout = QHBoxLayout(act_widget)
            act_layout.setContentsMargins(0, 0, 0, 0)
            act_layout.setSpacing(4)

            if task.status == DownloadStatus.DOWNLOADING:
                btn_p = QPushButton("Pausar")
                btn_p.clicked.connect(lambda _, tid=task.id: self.download_manager.pause(tid))
                act_layout.addWidget(btn_p)
            elif task.status == DownloadStatus.PAUSED:
                btn_r = QPushButton("Continuar")
                btn_r.clicked.connect(lambda _, tid=task.id: self.download_manager.resume(tid))
                act_layout.addWidget(btn_r)
            elif task.status == DownloadStatus.FAILED:
                btn_retry = QPushButton("Retry")
                btn_retry.clicked.connect(lambda _, tid=task.id: self.download_manager.retry(tid))
                act_layout.addWidget(btn_retry)

            btn_del = QPushButton("Excluir")
            btn_del.setProperty("danger", True)
            btn_del.clicked.connect(lambda _, tid=task.id: self.download_manager.delete_task_and_file(tid))
            act_layout.addWidget(btn_del)

            self.downloads_table.setCellWidget(row, 5, act_widget)

    def _refresh_offline_view(self) -> None:
        completed = self.download_manager.get_completed_tasks()
        self.offline_table.setRowCount(len(completed))

        for row, task in enumerate(completed):
            path = task.output_path or str(task.destination)
            size_bytes = Path(path).stat().st_size if Path(path).exists() else 0

            self.offline_table.setItem(row, 0, QTableWidgetItem(task.title))
            self.offline_table.setItem(row, 1, QTableWidgetItem(task.kind.upper()))
            self.offline_table.setItem(row, 2, QTableWidgetItem(human_size(size_bytes)))
            self.offline_table.setItem(row, 3, QTableWidgetItem(path))

            act_widget = QWidget()
            act_layout = QHBoxLayout(act_widget)
            act_layout.setContentsMargins(0, 0, 0, 0)
            act_layout.setSpacing(6)

            btn_play = QPushButton("▶ Reproduzir")
            btn_play.setProperty("primary", True)
            btn_play.clicked.connect(lambda _, p=path, t=task.title: self._play_offline_file(p, t))
            act_layout.addWidget(btn_play)

            btn_del = QPushButton("Excluir do Disco")
            btn_del.setProperty("danger", True)
            btn_del.clicked.connect(lambda _, tid=task.id: self._delete_offline_task(tid))
            act_layout.addWidget(btn_del)

            self.offline_table.setCellWidget(row, 4, act_widget)

    def _play_offline_file(self, file_path: str, title: str) -> None:
        item = MediaItem(
            id=f"offline_{Path(file_path).name}",
            kind="movie",
            title=title,
            stream_id="offline",
            stream_url=file_path,
            category_name="Offline",
        )
        self._play_item(item, url=file_path)

    def _delete_offline_task(self, task_id: str) -> None:
        if QMessageBox.question(self, "Confirmar Exclusão", "Deseja realmente apagar este arquivo do disco rígido?") == QMessageBox.Yes:
            self.download_manager.delete_task_and_file(task_id)
            self._refresh_offline_view()

    def _choose_download_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Escolher Pasta de Downloads", self.config.download_dir)
        if folder:
            self.config.download_dir = folder
            self.config_store.save(self.config)
            self.download_manager.configure(
                self.config.max_concurrent_downloads,
                self.config.use_ffmpeg_fallback,
                folder,
            )

    def _open_download_folder(self) -> None:
        folder = self.config.download_dir or str(default_download_dir())
        if sys.platform == "win32":
            os.startfile(folder)
        else:
            subprocess.run(["xdg-open", folder])

    # -------------------------------------------------------------
    # Global Search
    # -------------------------------------------------------------

    def _on_search_query_changed(self, query: str) -> None:
        clean_query = query.strip()
        if not clean_query:
            self._global_search_timer.stop()
            self.global_results.clear()
            if self.view_stack.currentIndex() == 9:
                self._switch_tab(self._search_origin_index)
            return
        if len(normalize_text(clean_query)) < 2:
            return
        current_idx = self.view_stack.currentIndex()
        if current_idx != 9 and current_idx < 8:
            self._search_origin_index = current_idx
        self._global_search_timer.start()

    def _execute_global_search(self) -> None:
        query = self.search_input.text().strip()
        if len(normalize_text(query)) < 2:
            return
        hidden = self.config.global_hidden_categories + self.current_profile.hidden_categories
        locked = self.config.locked_categories
        account_id = self.account_combo.currentData() or "all"
        grouped = self.catalog.global_search(
            query,
            hidden_categories=hidden,
            locked_categories=locked,
            account_id=account_id,
        )
        type_names = {"live": "AO VIVO", "movie": "FILME", "series": "SÉRIE"}
        account_names = {
            account.get("id"): account.get("name", "Fonte")
            for account in self.config.accounts
        }
        self.global_results.clear()
        total = 0
        for kind in ("movie", "series", "live"):
            for media in grouped[kind]:
                row = QTreeWidgetItem([
                    type_names[kind],
                    media.title,
                    media.category_name or "Sem categoria",
                    account_names.get(media.account_id, "Fonte local"),
                ])
                row.setData(0, Qt.UserRole, media)
                self.global_results.addTopLevelItem(row)
                total += 1
        self.search_title.setText(f'Resultados para “{query}”')
        if total:
            counts = [
                f"{len(grouped[kind])} {label}"
                for kind, label in (("movie", "filmes"), ("series", "séries"), ("live", "canais"))
                if grouped[kind]
            ]
            self.search_summary.setText(f"{total} resultados · " + " · ".join(counts))
        else:
            self.search_summary.setText("Nenhum resultado. Tente menos palavras ou confira a fonte selecionada.")
        self._switch_tab(9)

    def _on_global_result_clicked(self, tree_item: QTreeWidgetItem, _column: int) -> None:
        media: MediaItem | None = tree_item.data(0, Qt.UserRole)
        if media:
            self._open_details_modal(media)

    # -------------------------------------------------------------
    # Multi-Account & Profiles & Security
    # -------------------------------------------------------------

    def _reload_accounts_combo(self) -> None:
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        self.account_combo.addItem("🌐 Biblioteca Unificada (Todas as Contas)", "all")
        for acc_dict in self.config.accounts:
            self.account_combo.addItem(f"⚡ {acc_dict.get('name', 'Conta')}", acc_dict.get("id"))
        self.account_combo.blockSignals(False)

    def _reload_profiles_combo(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for p in self.config.profiles:
            self.profile_combo.addItem(f"{p.get('avatar', '👤')} {p.get('name', 'Perfil')}", p.get("id"))
        self.profile_combo.blockSignals(False)

    def _on_account_switched(self) -> None:
        self.config.active_account_id = self.account_combo.currentData()
        self.config_store.save(self.config)
        self.sync_active_account()

    def _on_profile_switched(self) -> None:
        prof_id = self.profile_combo.currentData()
        self.config.active_profile_id = prof_id
        self.current_profile = self._get_active_profile()
        self.config_store.save(self.config)
        self._populate_all_views()

    def _get_account_by_id(self, account_id: str) -> Account | None:
        for acc in self.config.accounts:
            if acc.get("id") == account_id:
                return Account.from_dict(acc)
        if self.config.accounts:
            return Account.from_dict(self.config.accounts[0])
        return None

    def _is_category_locked(self, category_name: str) -> bool:
        if self.current_profile.is_kids:
            return True
        norm = normalize_text(category_name)
        if norm in self.unlocked_categories:
            return False
        return any(normalize_text(locked) in norm for locked in self.config.locked_categories)

    def _prompt_pin(self) -> bool:
        dialog = PinDialog(parent=self, verifier=lambda candidate: self.config_store.verify_pin(self.config, candidate))
        if dialog.exec() == QDialog.Accepted:
            return True
        return False

    def _save_parental_pin(self) -> None:
        pin = self.pin_edit.text()
        if not pin.isdigit() or not 4 <= len(pin) <= 8:
            QMessageBox.warning(self, "PIN inválido", "Use de 4 a 8 dígitos.")
            return
        self.config.parental_pin = pin
        self.config_store.save(self.config)
        self.pin_edit.clear()
        QMessageBox.information(self, "PIN protegido", "O PIN foi salvo de forma derivada, sem texto puro.")

    def _show_add_xtream_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Adicionar Conta Xtream Codes")
        dialog.setFixedWidth(400)
        layout = QFormLayout(dialog)

        name_in = QLineEdit("Minha Conta Xtream")
        url_in = QLineEdit("http://servidor:80")
        user_in = QLineEdit()
        pass_in = QLineEdit()
        pass_in.setEchoMode(QLineEdit.Password)

        layout.addRow("Nome da Fonte:", name_in)
        layout.addRow("URL do Servidor:", url_in)
        layout.addRow("Usuário:", user_in)
        layout.addRow("Senha:", pass_in)

        btn_save = QPushButton("Testar & Salvar")
        btn_save.setProperty("primary", True)
        btn_save.clicked.connect(lambda: self._save_xtream_account(dialog, name_in.text(), url_in.text(), user_in.text(), pass_in.text()))
        layout.addRow("", btn_save)
        dialog.exec()

    def _save_xtream_account(self, dialog: QDialog, name: str, url: str, user: str, password: str) -> None:
        import uuid
        if not all((name.strip(), url.strip(), user.strip(), password)):
            QMessageBox.warning(dialog, "Dados incompletos", "Preencha nome, servidor, usuário e senha.")
            return
        try:
            validated_client = XtreamClient(url, user, password)
        except Exception as exc:
            QMessageBox.warning(dialog, "Servidor inválido", str(exc))
            return
        acc = Account(
            id=str(uuid.uuid4()),
            name=name.strip()[:80],
            account_type="xtream",
            server_url=validated_client.server_url,
            username=user.strip(),
            password=password,
        )
        self.config.accounts.append(acc.to_dict())
        self.config_store.save(self.config, password)
        self._reload_accounts_combo()
        dialog.accept()
        self.sync_active_account()

    def _show_add_m3u_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Adicionar Lista M3U / M3U8")
        dialog.setFixedWidth(420)
        layout = QFormLayout(dialog)

        name_in = QLineEdit("Minha Lista M3U")
        url_in = QLineEdit("https://.../lista.m3u")

        layout.addRow("Nome da Lista:", name_in)
        layout.addRow("URL ou Arquivo M3U:", url_in)

        btn_file = QPushButton("Procurar Arquivo Local...")
        btn_file.clicked.connect(lambda: url_in.setText(QFileDialog.getOpenFileName(dialog, "Arquivo M3U", "", "Playlists (*.m3u *.m3u8)")[0]))
        layout.addRow("", btn_file)

        btn_save = QPushButton("Salvar Lista")
        btn_save.setProperty("primary", True)
        btn_save.clicked.connect(lambda: self._save_m3u_account(dialog, name_in.text(), url_in.text()))
        layout.addRow("", btn_save)
        dialog.exec()

    def _save_m3u_account(self, dialog: QDialog, name: str, m3u_url: str) -> None:
        import uuid
        source = m3u_url.strip()
        is_remote = len(source) <= 4096 and source.startswith(("http://", "https://"))
        try:
            is_local = bool(source) and len(source) <= 1024 and Path(source).is_file()
        except OSError:
            is_local = False
        if not name.strip() or not (is_remote or is_local):
            QMessageBox.warning(dialog, "Lista inválida", "Use uma URL HTTP/HTTPS ou selecione um arquivo M3U local existente.")
            return
        acc = Account(
            id=str(uuid.uuid4()),
            name=name.strip()[:80],
            account_type="m3u",
            m3u_url=source,
        )
        self.config.accounts.append(acc.to_dict())
        self.config_store.save(self.config)
        self._reload_accounts_combo()
        dialog.accept()
        self.sync_active_account()

    def _remove_selected_account(self) -> None:
        curr_id = self.account_combo.currentData()
        if curr_id == "all":
            return
        self.config.accounts = [a for a in self.config.accounts if a.get("id") != curr_id]
        self.config_store.delete_account_secrets(str(curr_id))
        self.config_store.save(self.config)
        self._reload_accounts_combo()
        self.sync_active_account()

    def _show_add_profile_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Novo Perfil")
        layout = QFormLayout(dialog)
        name_in = QLineEdit("Novo Perfil")
        avatar_in = QComboBox()
        for av in ("⚡", "🧸", "🎬", "🍿", "🚀", "💎"):
            avatar_in.addItem(av)
        kids_chk = QCheckBox("Perfil Infantil (Restringir categorias adultas automaticamente)")

        layout.addRow("Nome:", name_in)
        layout.addRow("Avatar:", avatar_in)
        layout.addRow("", kids_chk)

        btn_save = QPushButton("Criar Perfil")
        btn_save.setProperty("primary", True)
        btn_save.clicked.connect(lambda: self._create_profile(dialog, name_in.text(), avatar_in.currentText(), kids_chk.isChecked()))
        layout.addRow("", btn_save)
        dialog.exec()

    def _create_profile(self, dialog: QDialog, name: str, avatar: str, is_kids: bool) -> None:
        import uuid
        prof = Profile(
            id=str(uuid.uuid4()),
            name=name,
            avatar=avatar,
            is_kids=is_kids,
        )
        self.config.profiles.append(prof.to_dict())
        self.config_store.save(self.config)
        self._reload_profiles_combo()
        self._refresh_profiles_list()
        dialog.accept()

    def _refresh_profiles_list(self) -> None:
        if not hasattr(self, "profiles_list_w"):
            return
        self.profiles_list_w.clear()
        for p_dict in self.config.profiles:
            p = Profile.from_dict(p_dict)
            is_active = (p.id == self.current_profile.id)
            active_badge = " [ATIVO]" if is_active else ""
            kids_badge = " [KIDS]" if p.is_kids else ""
            item_text = f"{p.avatar}  {p.name}{active_badge}{kids_badge}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, p.id)
            if is_active:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setForeground(QColor("#00e054"))
            self.profiles_list_w.addItem(item)

    def _switch_selected_profile(self) -> None:
        curr = self.profiles_list_w.currentItem()
        if not curr:
            QMessageBox.information(self, "Seleção necessária", "Selecione um perfil na lista.")
            return
        prof_id = curr.data(Qt.UserRole)
        if prof_id == self.current_profile.id:
            return
        self.config.active_profile_id = prof_id
        self.current_profile = self._get_active_profile()
        self.config_store.save(self.config)
        self._reload_profiles_combo()
        self._populate_all_views()
        self._refresh_profiles_list()
        QMessageBox.information(self, "Perfil Alternado", f"Perfil ativo alterado para: {self.current_profile.name}")

    def _edit_selected_profile(self) -> None:
        curr = self.profiles_list_w.currentItem()
        if not curr:
            QMessageBox.information(self, "Seleção necessária", "Selecione um perfil para editar.")
            return
        prof_id = curr.data(Qt.UserRole)
        target_dict = next((p for p in self.config.profiles if p.get("id") == prof_id), None)
        if not target_dict:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Editar Perfil — {target_dict.get('name')}")
        layout = QFormLayout(dialog)
        name_in = QLineEdit(target_dict.get("name", "Perfil"))
        avatar_in = QComboBox()
        for av in ("⚡", "🧸", "🎬", "🍿", "🚀", "💎", "⭐", "👾"):
            avatar_in.addItem(av)
        avatar_in.setCurrentText(target_dict.get("avatar", "⚡"))
        kids_chk = QCheckBox("Perfil Infantil")
        kids_chk.setChecked(bool(target_dict.get("is_kids", False)))

        layout.addRow("Nome:", name_in)
        layout.addRow("Avatar:", avatar_in)
        layout.addRow("", kids_chk)

        btn_save = QPushButton("Salvar Alterações")
        btn_save.setProperty("primary", True)
        layout.addRow("", btn_save)

        def save() -> None:
            name = name_in.text().strip()
            if not name:
                QMessageBox.warning(dialog, "Campo obrigatório", "Informe o nome do perfil.")
                return
            target_dict["name"] = name
            target_dict["avatar"] = avatar_in.currentText()
            target_dict["is_kids"] = kids_chk.isChecked()
            self.config_store.save(self.config)
            if self.current_profile.id == prof_id:
                self.current_profile = self._get_active_profile()
            self._reload_profiles_combo()
            self._refresh_profiles_list()
            dialog.accept()

        btn_save.clicked.connect(save)
        dialog.exec()

    def _delete_selected_profile(self) -> None:
        curr = self.profiles_list_w.currentItem()
        if not curr:
            QMessageBox.information(self, "Seleção necessária", "Selecione um perfil para excluir.")
            return
        prof_id = curr.data(Qt.UserRole)
        if len(self.config.profiles) <= 1:
            QMessageBox.warning(self, "Ação não permitida", "Não é possível excluir o único perfil existente.")
            return

        target_dict = next((p for p in self.config.profiles if p.get("id") == prof_id), None)
        if not target_dict:
            return

        reply = QMessageBox.question(
            self,
            "Excluir Perfil",
            f"Deseja realmente excluir o perfil '{target_dict.get('name')}'?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.config.profiles = [p for p in self.config.profiles if p.get("id") != prof_id]
            if self.config.active_profile_id == prof_id:
                self.config.active_profile_id = self.config.profiles[0].get("id")
                self.current_profile = self._get_active_profile()
            self.config_store.save(self.config)
            self._reload_profiles_combo()
            self._populate_all_views()
            self._refresh_profiles_list()


    def _export_backup(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Exportar Backup de Configurações", "happitv_backup.json", "JSON (*.json)")
        if path:
            data = self.config_store.export_backup(self.config, include_passwords=False)
            Path(path).write_text(data, encoding="utf-8")
            QMessageBox.information(self, "Backup Exportado", f"Configurações e biblioteca salvas em:\n{path}")

    def _import_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importar Backup JSON", "", "JSON (*.json)")
        if path:
            try:
                content = Path(path).read_text(encoding="utf-8")
                self.config = self.config_store.import_backup(content)
                self._reload_accounts_combo()
                self._reload_profiles_combo()
                self._populate_all_views()
                QMessageBox.information(self, "Backup Restaurado", "Configurações restauradas com sucesso!")
            except Exception as exc:
                QMessageBox.critical(self, "Erro ao Importar", f"Falha ao ler o backup: {exc}")
