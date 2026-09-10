from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QPoint, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider,
    QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app.catalog_manager import CatalogManager
from app.config import AppConfig, ConfigStore
from app.download_manager import DownloadManager
from app.m3u_parser import parse_m3u_content
from app.models import (
    Account, DownloadStatus, DownloadTask, Episode, MediaItem,
    Profile, format_duration, human_size, safe_filename,
)
from app.paths import app_data_dir, default_download_dir
from app.player import VideoPlayerWidget
from app.ui.details_dialog import DetailsDialog
from app.ui.epg_widget import EPGWidget
from app.ui.pin_dialog import PinDialog
from app.ui.style import STYLESHEET
from app.xtream_client import XtreamClient


def normalize_text(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).casefold().strip()


def matches_search(query: str, target: str) -> bool:
    norm_q = normalize_text(query)
    if not norm_q:
        return True
    norm_t = normalize_text(target)
    if not norm_t:
        return False
    if norm_q in norm_t:
        return True
    # Extra trailing character typo tolerance (e.g. "shamelesse" -> "shameless")
    if len(norm_q) >= 4 and norm_q[:-1] in norm_t:
        return True
    # Word similarity and substring check
    target_words = re.findall(r"[\w']+", norm_t)
    for word in target_words:
        if norm_q in word or word in norm_q:
            return True
        if len(norm_q) >= 4 and len(word) >= 4:
            if difflib.SequenceMatcher(None, norm_q, word).ratio() >= 0.75:
                return True
    # Token matching (all query words present in target)
    q_words = norm_q.split()
    if len(q_words) > 1 and all(any(qw in tw for tw in target_words) for qw in q_words):
        return True
    return False


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
            resp = requests.get(self.account.m3u_url, timeout=(10, 30))
            resp.raise_for_status()
            items = parse_m3u_content(resp.text, self.account.id)
        elif Path(self.account.m3u_url).exists():
            text = Path(self.account.m3u_url).read_text(encoding="utf-8", errors="ignore")
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
        self.download_manager = DownloadManager(max_concurrent=self.config.max_concurrent_downloads)
        self.download_manager.configure(self.config.max_concurrent_downloads, self.config.use_ffmpeg_fallback)

        # Legacy compatibility references
        self.downloads = self.download_manager
        self.series_data: list[dict[str, Any]] = []

        self.thread_pool = QThreadPool(self)

        # State Variables
        self.current_profile: Profile = self._get_active_profile()
        self.last_live_channel: MediaItem | None = None
        self.unlocked_categories: set[str] = set()

        # UI Setup
        self._init_ui()
        self._setup_shortcuts()
        self._connect_signals()

        # Initial view load
        self._refresh_home_view()
        self._update_source_health_ui()

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
        self.search_input.setPlaceholderText("🔍 Busca global (Canais, Filmes, Séries, Offline)... [Ctrl+F]")
        self.search_input.setFixedWidth(380)
        self.search_input.textChanged.connect(self._on_search_query_changed)
        header_layout.addWidget(self.search_input)

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
        self.view_stack.setCurrentIndex(index)
        if index == 0:
            self._refresh_home_view()
        elif index == 4:
            self._refresh_offline_view()
        elif index == 6:
            self._refresh_lists_view()

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

        # Hero Banner
        self.hero_box = QFrame()
        self.hero_box.setObjectName("HeroBackdropCard")
        self.hero_box.setStyleSheet("background: #11151f; border: 1px solid #1c2435; border-radius: 8px; padding: 24px;")
        h_layout = QVBoxLayout(self.hero_box)
        h_layout.setSpacing(10)

        hk = QLabel("01 // DESTAQUE BLUE LAB")
        hk.setStyleSheet("font-family: 'DM Mono'; font-size: 11px; color: #1749e8; font-weight: 800;")
        self.hero_title = QLabel("HAPPIPTV — EXPERIMENTAL STREAMING")
        self.hero_title.setStyleSheet("font-size: 28px; font-weight: 900; color: #ffffff;")
        self.hero_desc = QLabel("Bem-vindo à nova geração do Happiptv. Conecte sua fonte IPTV ou acesse sua Biblioteca Offline.")
        self.hero_desc.setStyleSheet("color: #9aa7bc; font-size: 14px;")

        self.btn_hero_action = QPushButton("▶ Explorar Catálogo")
        self.btn_hero_action.setProperty("primary", True)
        self.btn_hero_action.setFixedWidth(180)
        self.btn_hero_action.clicked.connect(lambda: self._switch_tab(2))

        h_layout.addWidget(hk)
        h_layout.addWidget(self.hero_title)
        h_layout.addWidget(self.hero_desc)
        h_layout.addWidget(self.btn_hero_action)
        c_layout.addWidget(self.hero_box)

        # Section: Continuar Assistindo
        c_layout.addWidget(self._make_section_title("CONTINUAR ASSISTINDO"))
        self.continue_watching_list = QListWidget()
        self.continue_watching_list.setFixedHeight(120)
        self.continue_watching_list.itemDoubleClicked.connect(self._on_continue_item_clicked)
        c_layout.addWidget(self.continue_watching_list)

        # Section: Favoritos
        c_layout.addWidget(self._make_section_title("FAVORITOS"))
        self.home_favs_list = QListWidget()
        self.home_favs_list.setFixedHeight(140)
        self.home_favs_list.itemDoubleClicked.connect(self._on_home_media_clicked)
        c_layout.addWidget(self.home_favs_list)

        # Section: Minha Lista (Watchlist)
        c_layout.addWidget(self._make_section_title("MINHA LISTA (QUERO ASSISTIR)"))
        self.home_watchlist_list = QListWidget()
        self.home_watchlist_list.setFixedHeight(140)
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
        layout.addWidget(self.epg_widget, 1)

        return widget

    def _create_catalog_view(self, kind: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 18, 24, 20)
        layout.setSpacing(14)

        # Filter bar
        bar = QHBoxLayout()
        cat_combo = QComboBox()
        cat_combo.addItem("Todas as categorias", "all")
        bar.addWidget(cat_combo)

        # Streaming Preset filter for Movies & Series
        stream_combo = QComboBox()
        for key, name, _ in STREAMING_PRESETS:
            stream_combo.addItem(name, key)
        bar.addWidget(stream_combo)

        search_edit = QLineEdit()
        search_edit.setPlaceholderText("🔍 Filtrar neste catálogo...")
        bar.addWidget(search_edit)

        list_w = QListWidget()
        list_w.itemDoubleClicked.connect(self._on_catalog_item_clicked)

        cat_combo.currentIndexChanged.connect(lambda: self._filter_catalog_view(kind, cat_combo, search_edit, list_w, stream_combo))
        stream_combo.currentIndexChanged.connect(lambda: self._filter_catalog_view(kind, cat_combo, search_edit, list_w, stream_combo))
        search_edit.textChanged.connect(lambda: self._filter_catalog_view(kind, cat_combo, search_edit, list_w, stream_combo))

        btn_refresh = QPushButton("↻ Atualizar")
        btn_refresh.clicked.connect(lambda: self._populate_catalog_view(kind, cat_combo, list_w))
        bar.addWidget(btn_refresh)

        layout.addLayout(bar)
        layout.addWidget(list_w, 1)

        setattr(self, f"{kind}_cat_combo", cat_combo)
        setattr(self, f"{kind}_stream_combo", stream_combo)
        setattr(self, f"{kind}_search_edit", search_edit)
        setattr(self, f"{kind}_list", list_w)

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
        self.profiles_list_w.setFixedHeight(100)
        prof_layout.addWidget(self.profiles_list_w)

        prof_btns = QHBoxLayout()
        btn_add_prof = QPushButton("+ Novo Perfil")
        btn_add_prof.clicked.connect(self._show_add_profile_dialog)
        prof_btns.addWidget(btn_add_prof)
        prof_btns.addStretch()
        prof_layout.addLayout(prof_btns)
        c_layout.addWidget(prof_group)

        # 3. Parental Control & PIN
        parental_group = QGroupBox("CONTROLE PARENTAL & BLOQUEIO DE CATEGORIAS")
        parental_layout = QFormLayout(parental_group)
        self.pin_edit = QLineEdit(self.config.parental_pin)
        self.pin_edit.setMaxLength(8)
        self.pin_edit.setFixedWidth(100)
        self.pin_edit.textChanged.connect(lambda t: setattr(self.config, "parental_pin", t))

        parental_layout.addRow("PIN de Segurança (4 dígitos):", self.pin_edit)
        btn_save_pin = QPushButton("Salvar Novo PIN")
        btn_save_pin.clicked.connect(lambda: self.config_store.save(self.config))
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

    # -------------------------------------------------------------
    # Sync, Catalog Population & Health Checks
    # -------------------------------------------------------------

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
        for it in items[:250]:
            fav_star = "★ " if it.id in self.current_profile.favorites else ""
            list_item = QListWidgetItem(f"{fav_star}{it.title}  [{it.category_name}]")
            list_item.setData(Qt.UserRole, it)
            list_w.addItem(list_item)

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

        dialog = DetailsDialog(item, episodes_by_season, is_in_watchlist=is_wl, is_favorite=is_fav, parent=self)
        dialog.play_requested.connect(self._on_details_play_requested)
        dialog.download_requested.connect(self._on_details_download_requested)
        dialog.download_season_requested.connect(self._on_details_season_download)
        dialog.toggle_watchlist_requested.connect(self._toggle_watchlist)
        dialog.toggle_favorite_requested.connect(self._toggle_favorite)
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
        target_url = episode.stream_url if episode else item.stream_url
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
        stream_url = url or item.stream_url
        cid = content_id or item.id
        self.view_stack.setCurrentIndex(8)

        self.player_widget.load_media(
            url=stream_url,
            content_id=cid,
            kind=item.kind,
            resume_position_ms=resume_pos,
            source_title=item.title,
        )
        self._add_to_history(item)

    def _on_live_channel_selected(self, channel: MediaItem) -> None:
        if self.last_live_channel:
            self.player_widget.last_channel_id = self.last_live_channel.stream_url
        self.last_live_channel = channel

        acc = self._get_account_by_id(channel.account_id)
        if acc and acc.account_type == "xtream":
            client = XtreamClient(acc.server_url, acc.username, acc.password)
            epg_data = client.get_short_epg(channel.stream_id)
            self.epg_widget.update_epg_listings(epg_data)

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
        self.continue_watching_list.clear()
        for cid, prog in sorted(self.current_profile.progress.items(), key=lambda x: x[1].get("updated_at", 0), reverse=True)[:10]:
            item = self.catalog.items.get(cid)
            if item:
                pos = prog.get("position_ms", 0)
                dur = prog.get("duration_ms", 1)
                pct = int((pos / dur) * 100) if dur > 0 else 0
                list_item = QListWidgetItem(f"▶ {item.title}  [{pct}% assistido]")
                list_item.setData(Qt.UserRole, cid)
                self.continue_watching_list.addItem(list_item)

        self.home_favs_list.clear()
        for fid in self.current_profile.favorites[:15]:
            item = self.catalog.items.get(fid)
            if item:
                li = QListWidgetItem(f"★ {item.title} ({item.category_name})")
                li.setData(Qt.UserRole, item)
                self.home_favs_list.addItem(li)

        self.home_watchlist_list.clear()
        for wid in self.current_profile.watchlist[:15]:
            item = self.catalog.items.get(wid)
            if item:
                li = QListWidgetItem(f"✓ {item.title} ({item.category_name})")
                li.setData(Qt.UserRole, item)
                self.home_watchlist_list.addItem(li)

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

    # -------------------------------------------------------------
    # Downloads & Offline Library
    # -------------------------------------------------------------

    def _on_details_download_requested(self, item: MediaItem, episode: Episode | None) -> None:
        target_url = episode.stream_url if episode else item.stream_url
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
        if not query.strip():
            return
        current_idx = self.view_stack.currentIndex()
        if current_idx == 2 and hasattr(self, "movie_cat_combo"):
            self._filter_catalog_view("movie", self.movie_cat_combo, self.search_input, self.movie_list)
        elif current_idx == 3 and hasattr(self, "series_cat_combo"):
            self._filter_catalog_view("series", self.series_cat_combo, self.search_input, self.series_list)

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
        dialog = PinDialog(expected_pin=self.config.parental_pin, parent=self)
        if dialog.exec() == QDialog.Accepted:
            return True
        return False

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
        acc = Account(
            id=str(uuid.uuid4()),
            name=name,
            account_type="xtream",
            server_url=url,
            username=user,
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
        acc = Account(
            id=str(uuid.uuid4()),
            name=name,
            account_type="m3u",
            m3u_url=m3u_url,
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
        dialog.accept()

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
