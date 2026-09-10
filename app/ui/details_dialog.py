from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QTabWidget, QVBoxLayout,
    QWidget,
)

from app.models import Episode, MediaItem, format_duration


class DetailsDialog(QDialog):
    play_requested = Signal(object, object)              # media_item, episode or None
    download_requested = Signal(object, object)          # media_item, episode or None
    download_season_requested = Signal(object, int, list)# media_item, season_num, episodes
    toggle_watchlist_requested = Signal(str)             # content_id
    toggle_favorite_requested = Signal(str)              # content_id
    toggle_episode_watched = Signal(str)                 # episode_id
    add_to_playlist_requested = Signal(object)           # media_item

    def __init__(
        self,
        item: MediaItem,
        episodes_by_season: dict[int, list[Episode]] | None = None,
        is_in_watchlist: bool = False,
        is_favorite: bool = False,
        poster_pixmap: QPixmap | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.item = item
        self.episodes_by_season = episodes_by_season or {}
        self.is_in_watchlist = is_in_watchlist
        self.is_favorite = is_favorite
        self._net_mgr: QNetworkAccessManager | None = None
        self._poster_reply: QNetworkReply | None = None

        self.setWindowTitle(f"{item.title} — Happitv")
        self.resize(880, 680)
        self.setStyleSheet("background-color: #0d1118; color: #f1f5f9;")

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: 0; background: transparent; }")
        
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(20)

        # Header Hero Card
        hero_card = QFrame()
        hero_card.setObjectName("HeroBackdropCard")
        hero_card.setStyleSheet("background-color: #111622; border: 1px solid #1f2a3e; border-radius: 8px; padding: 20px;")
        hero_layout = QHBoxLayout(hero_card)
        hero_layout.setSpacing(24)

        # Poster representation
        self.poster_lbl = QLabel()
        self.poster_lbl.setFixedSize(140, 210)
        self.poster_lbl.setAlignment(Qt.AlignCenter)
        self.poster_lbl.setStyleSheet(
            "background: #171d2b; border: 1px solid #25334c; border-radius: 8px; font-size: 32px;"
        )
        self._load_poster(poster_pixmap)
        hero_layout.addWidget(self.poster_lbl)

        # Info column
        info_col = QVBoxLayout()
        info_col.setSpacing(8)

        kicker = QLabel(f"HAPPITV // {item.kind.upper()} // {item.category_name.upper()}")
        kicker.setStyleSheet("font-family: 'DM Mono'; font-size: 11px; color: #1749e8; font-weight: 800; letter-spacing: 1px;")
        info_col.addWidget(kicker)

        title_lbl = QLabel(item.title)
        title_lbl.setStyleSheet("font-size: 26px; font-weight: 900; color: #ffffff;")
        title_lbl.setWordWrap(True)
        info_col.addWidget(title_lbl)

        # Meta pills row (Year, Rating, Duration, Genre)
        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        if item.year:
            meta_row.addWidget(self._make_pill(str(item.year)))
        if item.rating:
            meta_row.addWidget(self._make_pill(f"★ {item.rating}"))
        if item.duration_str:
            meta_row.addWidget(self._make_pill(item.duration_str))
        if item.genre:
            meta_row.addWidget(self._make_pill(item.genre))
        meta_row.addStretch()
        info_col.addLayout(meta_row)

        # Synopsis
        synopsis_lbl = QLabel(item.synopsis or "Sinopse não disponível pelo provedor.")
        synopsis_lbl.setWordWrap(True)
        synopsis_lbl.setStyleSheet("font-size: 13px; color: #9aa7bc; line-height: 1.4;")
        info_col.addWidget(synopsis_lbl)

        # Quick Actions Row
        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)

        self.btn_play = QPushButton("▶ Assistir Agora")
        self.btn_play.setProperty("primary", True)
        self.btn_play.setStyleSheet("padding: 10px 20px; font-weight: 800; font-size: 14px;")
        self.btn_play.clicked.connect(self._on_play_clicked)
        actions_row.addWidget(self.btn_play)

        if item.kind == "movie":
            self.btn_download = QPushButton("⬇ Baixar Filme")
            self.btn_download.clicked.connect(lambda: self.download_requested.emit(self.item, None))
            actions_row.addWidget(self.btn_download)

        self.btn_watchlist = QPushButton("✓ Na Minha Lista" if is_in_watchlist else "+ Minha Lista")
        self.btn_watchlist.clicked.connect(self._toggle_watchlist)
        actions_row.addWidget(self.btn_watchlist)

        self.btn_fav = QPushButton("★ Favorito" if is_favorite else "☆ Favoritar")
        self.btn_fav.clicked.connect(self._toggle_fav)
        actions_row.addWidget(self.btn_fav)

        self.btn_playlist = QPushButton("📁 + Playlist")
        self.btn_playlist.clicked.connect(lambda: self.add_to_playlist_requested.emit(self.item))
        actions_row.addWidget(self.btn_playlist)

        actions_row.addStretch()
        info_col.addLayout(actions_row)

        hero_layout.addLayout(info_col, 1)
        layout.addWidget(hero_card)


        # Series Season & Episodes Section
        if item.kind == "series" and self.episodes_by_season:
            series_box = QFrame()
            series_box.setStyleSheet("background-color: #10141e; border: 1px solid #1c2434; border-radius: 8px; padding: 18px;")
            series_layout = QVBoxLayout(series_box)
            series_layout.setSpacing(14)

            top_series_row = QHBoxLayout()
            sec_title = QLabel("TEMPORADAS & EPISÓDIOS")
            sec_title.setStyleSheet("font-family: 'DM Mono'; font-size: 12px; font-weight: 800; color: #ffffff;")
            top_series_row.addWidget(sec_title)

            self.season_combo = QComboBox()
            for s_num in sorted(self.episodes_by_season.keys()):
                self.season_combo.addItem(f"Temporada {s_num}", s_num)
            self.season_combo.currentIndexChanged.connect(self._on_season_changed)
            top_series_row.addWidget(self.season_combo)

            top_series_row.addStretch()

            self.btn_dl_season = QPushButton("⬇ Baixar Temporada Completa")
            self.btn_dl_season.clicked.connect(self._download_current_season)
            top_series_row.addWidget(self.btn_dl_season)

            series_layout.addLayout(top_series_row)

            # Episodes List Widget
            self.episodes_list = QListWidget()
            self.episodes_list.setMinimumHeight(240)
            self.episodes_list.itemDoubleClicked.connect(self._on_episode_double_clicked)
            series_layout.addWidget(self.episodes_list)

            layout.addWidget(series_box)
            self._load_episodes_for_season(self.season_combo.currentData())

        scroll.setWidget(content_widget)
        root_layout.addWidget(scroll)

    def _make_pill(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("border: 1px solid #27354d; background: #161c28; color: #cbd5e1; border-radius: 4px; padding: 3px 8px; font-family: 'DM Mono'; font-size: 11px;")
        return lbl

    def _on_play_clicked(self) -> None:
        if self.item.kind == "series" and self.episodes_by_season:
            # Play first episode of active season
            season = self.season_combo.currentData()
            eps = self.episodes_by_season.get(season, [])
            ep = eps[0] if eps else None
            self.play_requested.emit(self.item, ep)
        else:
            self.play_requested.emit(self.item, None)
        self.accept()

    def _toggle_watchlist(self) -> None:
        self.is_in_watchlist = not self.is_in_watchlist
        self.btn_watchlist.setText("✓ Na Minha Lista" if self.is_in_watchlist else "+ Minha Lista")
        self.toggle_watchlist_requested.emit(self.item.id)

    def _toggle_fav(self) -> None:
        self.is_favorite = not self.is_favorite
        self.btn_fav.setText("★ Favorito" if self.is_favorite else "☆ Favoritar")
        self.toggle_favorite_requested.emit(self.item.id)

    def _on_season_changed(self) -> None:
        season = self.season_combo.currentData()
        self._load_episodes_for_season(season)

    def _load_episodes_for_season(self, season_num: int) -> None:
        self.episodes_list.clear()
        episodes = self.episodes_by_season.get(season_num, [])
        for ep in episodes:
            status_tag = "✓ " if ep.is_watched else ""
            dur = f" ({format_duration(ep.duration_sec)})" if ep.duration_sec else ""
            item_text = f"{status_tag}E{ep.episode_num:02d} — {ep.title}{dur}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, ep)
            self.episodes_list.addItem(item)

    def _on_episode_double_clicked(self, item: QListWidgetItem) -> None:
        ep: Episode = item.data(Qt.UserRole)
        if ep:
            self.play_requested.emit(self.item, ep)
            self.accept()

    def _download_current_season(self) -> None:
        season = self.season_combo.currentData()
        eps = self.episodes_by_season.get(season, [])
        if eps:
            self.download_season_requested.emit(self.item, season, eps)

    def _load_poster(self, pixmap: QPixmap | None = None) -> None:
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(140, 210, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.poster_lbl.setPixmap(scaled)
            return

        poster_url = self.item.poster or self.item.backdrop
        if not poster_url:
            self.poster_lbl.setText("🎬" if self.item.kind == "movie" else "📺")
            return

        try:
            if Path(poster_url).is_file():
                p = QPixmap(poster_url)
                if not p.isNull():
                    self.poster_lbl.setPixmap(p.scaled(140, 210, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    return
        except Exception:
            pass

        if poster_url.startswith(("http://", "https://")):
            self.poster_lbl.setText("⏳")
            self._net_mgr = QNetworkAccessManager(self)
            req = QNetworkRequest(QUrl(poster_url))
            self._poster_reply = self._net_mgr.get(req)
            self._poster_reply.finished.connect(self._on_poster_downloaded)
        else:
            self.poster_lbl.setText("🎬" if self.item.kind == "movie" else "📺")

    def _on_poster_downloaded(self) -> None:
        if not self._poster_reply:
            return
        if self._poster_reply.error() == QNetworkReply.NetworkError.NoError:
            data = self._poster_reply.readAll()
            pix = QPixmap()
            if pix.loadFromData(data):
                scaled = pix.scaled(140, 210, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.poster_lbl.setPixmap(scaled)
                return
        self.poster_lbl.setText("🎬" if self.item.kind == "movie" else "📺")

