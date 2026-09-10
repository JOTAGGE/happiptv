from __future__ import annotations

import base64
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from app.models import EPGProgram, MediaItem


def decode_epg_title(raw_title: str) -> str:
    if not raw_title:
        return "Programação Indisponível"
    try:
        # Check if Base64 encoded
        decoded = base64.b64decode(raw_title).decode("utf-8")
        if decoded.isprintable() and len(decoded) > 1:
            return decoded
    except Exception:
        pass
    return raw_title


class EPGWidget(QWidget):
    channel_selected = Signal(object)           # MediaItem to play
    channel_highlighted = Signal(object)        # MediaItem to preview EPG
    channel_favorited = Signal(str)             # channel_id
    add_to_playlist_requested = Signal(object)  # MediaItem

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.selected_channel: MediaItem | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # Left Column: Channels List & Search
        channels_col = QVBoxLayout()
        channels_col.setSpacing(10)

        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Filtrar canais ao vivo (digite sem trocar de tela)...")
        self.search_input.textChanged.connect(self._filter_channels)
        search_row.addWidget(self.search_input)

        self.btn_only_favs = QPushButton("★ Favoritos")
        self.btn_only_favs.setCheckable(True)
        self.btn_only_favs.clicked.connect(self._filter_channels)
        search_row.addWidget(self.btn_only_favs)
        channels_col.addLayout(search_row)

        self.channels_list = QListWidget()
        self.channels_list.itemClicked.connect(self._on_item_clicked)
        self.channels_list.itemDoubleClicked.connect(self._on_item_activated)
        self.channels_list.itemActivated.connect(self._on_item_activated)
        channels_col.addWidget(self.channels_list, 1)

        layout.addLayout(channels_col, 1)

        # Right Column: EPG Schedule & Program Details
        epg_col = QVBoxLayout()
        epg_col.setSpacing(12)

        self.epg_header = QFrame()
        self.epg_header.setStyleSheet("background: #11151f; border: 1px solid #1c2434; border-radius: 6px; padding: 14px;")
        header_layout = QVBoxLayout(self.epg_header)
        header_layout.setSpacing(6)

        self.channel_title_lbl = QLabel("Selecione um canal")
        self.channel_title_lbl.setStyleSheet("font-size: 16px; font-weight: 800; color: #ffffff;")
        self.channel_kicker = QLabel("BLUE LAB // GUIA DE PROGRAMAÇÃO EPG")
        self.channel_kicker.setStyleSheet("font-family: 'DM Mono'; font-size: 10px; color: #1749e8; font-weight: 800;")
        header_layout.addWidget(self.channel_kicker)
        header_layout.addWidget(self.channel_title_lbl)

        # "Agora no ar" card
        self.now_playing_title = QLabel("Programa atual: —")
        self.now_playing_title.setStyleSheet("font-size: 14px; font-weight: 700; color: #00e054;")
        self.now_playing_desc = QLabel("Selecione um canal na lista para ver a programação sem interromper sua navegação.")
        self.now_playing_desc.setWordWrap(True)
        self.now_playing_desc.setStyleSheet("font-size: 12px; color: #9aa7bc;")
        self.now_playing_time = QLabel("00:00 — 00:00")
        self.now_playing_time.setStyleSheet("font-family: 'DM Mono'; font-size: 11px; color: #cbd5e1;")

        header_layout.addWidget(self.now_playing_title)
        header_layout.addWidget(self.now_playing_time)
        header_layout.addWidget(self.now_playing_desc)

        # Action buttons row (Play channel, Add to playlist)
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)

        self.btn_play_channel = QPushButton("▶ Assistir Canal")
        self.btn_play_channel.setProperty("primary", True)
        self.btn_play_channel.clicked.connect(self._play_current_channel)
        self.btn_play_channel.setEnabled(False)

        self.btn_add_playlist = QPushButton("+ Playlist")
        self.btn_add_playlist.clicked.connect(self._add_current_to_playlist)
        self.btn_add_playlist.setEnabled(False)

        actions_row.addWidget(self.btn_play_channel)
        actions_row.addWidget(self.btn_add_playlist)
        actions_row.addStretch()
        header_layout.addLayout(actions_row)

        epg_col.addWidget(self.epg_header)

        # EPG Listing List
        sec_lbl = QLabel("PRÓXIMOS PROGRAMAS")
        sec_lbl.setStyleSheet("font-family: 'DM Mono'; font-size: 11px; font-weight: 800; color: #79879c;")
        epg_col.addWidget(sec_lbl)

        self.programs_list = QListWidget()
        epg_col.addWidget(self.programs_list, 1)

        layout.addLayout(epg_col, 1)


        self.all_channels: list[MediaItem] = []
        self.favorites: set[str] = set()

    def set_channels(self, channels: list[MediaItem], favorites: set[str] | None = None) -> None:
        self.all_channels = channels
        self.favorites = favorites or set()
        self._filter_channels()

    def _filter_channels(self) -> None:
        self.channels_list.clear()
        query = self.search_input.text().lower().strip()
        only_favs = self.btn_only_favs.isChecked()

        for ch in self.all_channels:
            if only_favs and ch.id not in self.favorites:
                continue
            if query and query not in ch.title.lower() and query not in ch.category_name.lower():
                continue

            fav_star = "★ " if ch.id in self.favorites else ""
            item = QListWidgetItem(f"{fav_star}{ch.title}")
            item.setData(Qt.UserRole, ch)
            self.channels_list.addItem(item)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        ch: MediaItem = item.data(Qt.UserRole)
        if not ch:
            return
        self.selected_channel = ch
        self.channel_title_lbl.setText(ch.title)
        self.btn_play_channel.setEnabled(True)
        self.btn_add_playlist.setEnabled(True)
        self.channel_highlighted.emit(ch)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        ch: MediaItem = item.data(Qt.UserRole)
        if not ch:
            return
        self.selected_channel = ch
        self.channel_title_lbl.setText(ch.title)
        self.btn_play_channel.setEnabled(True)
        self.btn_add_playlist.setEnabled(True)
        self.channel_selected.emit(ch)

    def _play_current_channel(self) -> None:
        if self.selected_channel:
            self.channel_selected.emit(self.selected_channel)

    def _add_current_to_playlist(self) -> None:
        if self.selected_channel:
            self.add_to_playlist_requested.emit(self.selected_channel)


    def update_epg_listings(self, listings: list[dict[str, Any]]) -> None:
        self.programs_list.clear()
        if not listings:
            self.now_playing_title.setText("Programa atual: Informação indisponível")
            self.now_playing_desc.setText("O provedor não enviou guia EPG para este canal.")
            self.now_playing_time.setText("—")
            return

        first = listings[0]
        title = decode_epg_title(first.get("title", ""))
        desc = decode_epg_title(first.get("description", "")) or "Sem descrição."
        start = first.get("start", "")
        end = first.get("end", "")

        self.now_playing_title.setText(f"● AGORA: {title}")
        self.now_playing_desc.setText(desc)
        self.now_playing_time.setText(f"{start} — {end}")

        for prog in listings[1:]:
            p_title = decode_epg_title(prog.get("title", ""))
            p_start = prog.get("start", "")
            p_end = prog.get("end", "")
            time_str = f"[{p_start[11:16] if len(p_start)>=16 else p_start}] " if p_start else ""
            item = QListWidgetItem(f"{time_str}{p_title}")
            self.programs_list.addItem(item)
