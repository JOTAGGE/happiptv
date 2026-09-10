from __future__ import annotations

import time
from typing import Any, Callable

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeyEvent, QMouseEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
    QSlider, QStackedLayout, QVBoxLayout, QWidget,
)

from app.models import format_duration, StreamDiagnostics


class StreamDiagnosticsOverlay(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DiagnosticsHUD")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        header = QLabel("BLUE LAB // STREAM DIAGNOSTICS HUD")
        header.setStyleSheet("font-size: 10px; font-weight: 800; color: #1749e8; font-family: 'DM Mono';")
        layout.addWidget(header)

        self.lbl_resolution = QLabel("Resolução: Desconhecida")
        self.lbl_fps = QLabel("FPS: —")
        self.lbl_bitrate = QLabel("Bitrate estimado: —")
        self.lbl_codecs = QLabel("Codecs: V: AVC / A: AAC")
        self.lbl_latency = QLabel("Latência da fonte: —")
        self.lbl_buffer = QLabel("Saúde do Buffer: OK")

        for lbl in (self.lbl_resolution, self.lbl_fps, self.lbl_bitrate, self.lbl_codecs, self.lbl_latency, self.lbl_buffer):
            lbl.setStyleSheet("font-family: 'DM Mono'; font-size: 11px; color: #d0d7e3;")
            layout.addWidget(lbl)

        self.hide()

    def update_diagnostics(self, diag: StreamDiagnostics) -> None:
        self.lbl_resolution.setText(f"Resolução: {diag.resolution}")
        self.lbl_fps.setText(f"FPS: {diag.fps:.1f} fps")
        self.lbl_bitrate.setText(f"Bitrate: ~{diag.bitrate_kbps} kbps")
        self.lbl_codecs.setText(f"Codecs: V: {diag.codec_video} | A: {diag.codec_audio}")
        self.lbl_latency.setText(f"Latência da fonte: {diag.latency_ms} ms")
        self.lbl_buffer.setText(f"Buffer: {diag.buffer_seconds:.1f}s | Quedas: {diag.dropped_frames}")


class VideoPlayerWidget(QWidget):
    progress_updated = Signal(str, int, int)       # content_id, position_ms, duration_ms
    episode_finished = Signal(str)                 # content_id
    next_episode_requested = Signal()
    previous_channel_requested = Signal()
    fullscreen_toggled = Signal(bool)
    cinema_mode_toggled = Signal(bool)
    download_requested = Signal(str, str)          # content_id, kind

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PlayerContainer")

        self.content_id: str = ""
        self.kind: str = "movie"  # "movie", "series", "live"
        self.has_next_episode: bool = False
        self.is_live: bool = False
        self.is_controls_collapsed: bool = False
        self.last_channel_id: str = ""
        self.stream_diag = StreamDiagnostics()


        # QtMultimedia Player Setup
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)

        self.video_widget = QVideoWidget(self)
        self.player.setVideoOutput(self.video_widget)

        # Main Layout: Stack overlay (Video at bottom, controls & HUD on top)
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)

        self.root_layout.addWidget(self.video_widget, 1)

        # Controls Bar
        self.controls_bar = QFrame(self)
        self.controls_bar.setObjectName("PlayerControlsBar")
        self.controls_layout = QVBoxLayout(self.controls_bar)
        self.controls_layout.setContentsMargins(14, 6, 14, 10)
        self.controls_layout.setSpacing(6)

        # Timeline Slider Row
        self.timeline_row = QHBoxLayout()
        self.time_current_lbl = QLabel("00:00", objectName="PlayerTimeLabel")
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(self._on_seek_moved)
        self.slider.sliderReleased.connect(self._on_seek_released)
        self.time_total_lbl = QLabel("00:00", objectName="PlayerTimeLabel")

        self.timeline_row.addWidget(self.time_current_lbl)
        self.timeline_row.addWidget(self.slider, 1)
        self.timeline_row.addWidget(self.time_total_lbl)
        self.controls_layout.addLayout(self.timeline_row)

        # Buttons Control Row
        self.buttons_row = QHBoxLayout()
        self.buttons_row.setSpacing(8)

        # Play / Pause
        self.btn_play = QPushButton("▶ Reproduzir")
        self.btn_play.setProperty("primary", True)
        self.btn_play.clicked.connect(self.toggle_play)

        # Seek buttons (-10s / +10s)
        self.btn_rewind = QPushButton("⏪ -10s")
        self.btn_rewind.clicked.connect(lambda: self.seek_relative(-10000))
        self.btn_forward = QPushButton("+10s ⏩")
        self.btn_forward.clicked.connect(lambda: self.seek_relative(10000))

        # Volume slider & Mute
        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setFixedWidth(36)
        self.btn_mute.clicked.connect(self.toggle_mute)
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(85)
        self.audio_output.setVolume(0.8)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)

        # Speed Dropdown
        self.speed_combo = QComboBox()
        self.speed_combo.setFixedWidth(75)
        for spd in ("0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x"):
            self.speed_combo.addItem(spd)
        self.speed_combo.setCurrentText("1.0x")
        self.speed_combo.currentTextChanged.connect(self._on_speed_changed)

        # Tracks Menus (Audio & Subtitles)
        self.btn_audio_tracks = QPushButton("Áudio")
        self.btn_audio_tracks.clicked.connect(self._show_audio_tracks_menu)

        self.btn_sub_tracks = QPushButton("Legenda")
        self.btn_sub_tracks.clicked.connect(self._show_subtitles_menu)

        # Next episode button
        self.btn_next_ep = QPushButton("Próximo ⏭")
        self.btn_next_ep.clicked.connect(self.next_episode_requested.emit)
        self.btn_next_ep.hide()

        # Download button
        self.btn_download = QPushButton("⬇ Baixar")
        self.btn_download.clicked.connect(self._on_download_clicked)

        # Previous Channel (for Live TV)
        self.btn_prev_channel = QPushButton("↺ Canal Anterior")
        self.btn_prev_channel.clicked.connect(self.previous_channel_requested.emit)
        self.btn_prev_channel.hide()

        # Diagnostics HUD Toggle
        self.btn_diagnostics = QPushButton("HUD [D]")
        self.btn_diagnostics.clicked.connect(self.toggle_diagnostics)

        # Cinema Mode Toggle
        self.btn_cinema = QPushButton("Cinema [C]")
        self.btn_cinema.clicked.connect(self.toggle_cinema_mode)

        # Fullscreen Toggle
        self.btn_fullscreen = QPushButton("⛶ Tela Cheia [F]")
        self.btn_fullscreen.clicked.connect(self.toggle_fullscreen)

        # Collapse Controls Toggle
        self.btn_collapse = QPushButton("▼ Ocultar [H]")
        self.btn_collapse.clicked.connect(self.toggle_controls_bar)

        # Build buttons row
        self.buttons_row.addWidget(self.btn_play)
        self.buttons_row.addWidget(self.btn_rewind)
        self.buttons_row.addWidget(self.btn_forward)
        self.buttons_row.addWidget(self.btn_mute)
        self.buttons_row.addWidget(self.volume_slider)
        self.buttons_row.addSpacing(6)
        self.buttons_row.addWidget(self.speed_combo)
        self.buttons_row.addWidget(self.btn_audio_tracks)
        self.buttons_row.addWidget(self.btn_sub_tracks)
        self.buttons_row.addWidget(self.btn_download)
        self.buttons_row.addWidget(self.btn_next_ep)
        self.buttons_row.addWidget(self.btn_prev_channel)
        self.buttons_row.addStretch()
        self.buttons_row.addWidget(self.btn_diagnostics)
        self.buttons_row.addWidget(self.btn_cinema)
        self.buttons_row.addWidget(self.btn_fullscreen)
        self.buttons_row.addWidget(self.btn_collapse)

        self.controls_layout.addLayout(self.buttons_row)
        self.root_layout.addWidget(self.controls_bar)

        # Floating Expand Button (shown when controls are collapsed)
        self.btn_expand = QPushButton("▲ Controles [H]", self)
        self.btn_expand.setStyleSheet(
            "background: rgba(17, 21, 31, 0.9); color: #ffffff; border: 1px solid #1c2434; "
            "border-radius: 4px; padding: 4px 10px; font-size: 11px; font-weight: 700; "
            "font-family: 'DM Mono', Consolas, monospace;"
        )
        self.btn_expand.clicked.connect(self.toggle_controls_bar)
        self.btn_expand.hide()

        # Floating Diagnostics HUD
        self.hud = StreamDiagnosticsOverlay(self)

        # Signals from QMediaPlayer
        self.player.positionChanged.connect(self._on_player_position_changed)
        self.player.durationChanged.connect(self._on_player_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)

        # Auto-hide controls timer
        self.controls_hide_timer = QTimer(self)
        self.controls_hide_timer.setInterval(3500)
        self.controls_hide_timer.setSingleShot(True)
        self.controls_hide_timer.timeout.connect(self._auto_hide_controls)

        self.is_seeking = False
        self.is_fullscreen_state = False
        self.is_cinema_state = False

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        # Position floating HUD top-right
        self.hud.move(self.width() - self.hud.width() - 20, 20)
        hint = self.btn_expand.sizeHint()
        self.btn_expand.move((self.width() - hint.width()) // 2, self.height() - hint.height() - 8)


    def load_media(
        self,
        url: str,
        content_id: str = "",
        kind: str = "movie",
        resume_position_ms: int = 0,
        has_next_ep: bool = False,
        source_title: str = "",
        latency_ms: int = 40,
    ) -> None:
        self.content_id = content_id
        self.kind = kind
        self.has_next_episode = has_next_ep
        self.is_live = (kind == "live")

        self.btn_next_ep.setVisible(has_next_ep)
        self.btn_prev_channel.setVisible(self.is_live)
        self.btn_download.setVisible(not self.is_live)
        self.timeline_row.setEnabled(not self.is_live)

        if self.is_live:
            self.time_current_lbl.setText("AO VIVO")
            self.time_total_lbl.setText("● EM TRANSMISSÃO")
            self.btn_rewind.setEnabled(False)
            self.btn_forward.setEnabled(False)
        else:
            self.btn_rewind.setEnabled(True)
            self.btn_forward.setEnabled(True)

        self.stream_diag.latency_ms = latency_ms
        self.stream_diag.source_server = url
        self.hud.update_diagnostics(self.stream_diag)

        target_url = QUrl.fromLocalFile(url) if "://" not in url else QUrl(url)
        self.player.setSource(target_url)
        self.player.play()

        if resume_position_ms > 0 and not self.is_live:
            # Set position once player buffers
            QTimer.singleShot(600, lambda: self.player.setPosition(resume_position_ms))

        self.controls_hide_timer.start()

    def toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop(self) -> None:
        self.player.stop()

    def seek_relative(self, delta_ms: int) -> None:
        if self.is_live:
            return
        new_pos = max(0, min(self.player.position() + delta_ms, self.player.duration()))
        self.player.setPosition(new_pos)

    def toggle_mute(self) -> None:
        is_muted = self.audio_output.isMuted()
        self.audio_output.setMuted(not is_muted)
        self.btn_mute.setText("🔇" if not is_muted else "🔊")

    def _on_volume_changed(self, value: int) -> None:
        self.audio_output.setVolume(value / 100.0)
        if value == 0:
            self.btn_mute.setText("🔇")
        else:
            self.btn_mute.setText("🔊")
            if self.audio_output.isMuted():
                self.audio_output.setMuted(False)

    def _on_speed_changed(self, text: str) -> None:
        try:
            val = float(text.replace("x", ""))
            self.player.setPlaybackRate(val)
        except Exception:
            pass

    def _show_audio_tracks_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet("background: #11151e; color: #fff; border: 1px solid #1c2230;")
        tracks = self.player.audioTracks()
        active = self.player.activeAudioTrack()
        if not tracks:
            action = menu.addAction("Padrão / Automático")
            action.setEnabled(False)
        else:
            for idx, track in enumerate(tracks):
                title = f"Faixa de Áudio {idx + 1}"
                action = menu.addAction(title)
                action.setCheckable(True)
                action.setChecked(idx == active)
                action.triggered.connect(lambda checked, i=idx: self.player.setActiveAudioTrack(i))
        menu.exec(self.btn_audio_tracks.mapToGlobal(QPoint(0, -menu.sizeHint().height())))

    def _show_subtitles_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet("background: #11151e; color: #fff; border: 1px solid #1c2230;")
        tracks = self.player.subtitleTracks()
        active = self.player.activeSubtitleTrack()
        off_action = menu.addAction("Desativada")
        off_action.setCheckable(True)
        off_action.setChecked(active == -1)
        off_action.triggered.connect(lambda: self.player.setActiveSubtitleTrack(-1))

        for idx, track in enumerate(tracks):
            title = f"Legenda {idx + 1}"
            action = menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(idx == active)
            action.triggered.connect(lambda checked, i=idx: self.player.setActiveSubtitleTrack(i))
        menu.exec(self.btn_sub_tracks.mapToGlobal(QPoint(0, -menu.sizeHint().height())))

    def toggle_diagnostics(self) -> None:
        if self.hud.isVisible():
            self.hud.hide()
        else:
            self.hud.show()
            self.hud.raise_()

    def toggle_cinema_mode(self) -> None:
        self.is_cinema_state = not self.is_cinema_state
        self.btn_cinema.setProperty("primary", self.is_cinema_state)
        self.btn_cinema.style().unpolish(self.btn_cinema)
        self.btn_cinema.style().polish(self.btn_cinema)
        self.cinema_mode_toggled.emit(self.is_cinema_state)

    def toggle_fullscreen(self) -> None:
        self.is_fullscreen_state = not self.is_fullscreen_state
        self.btn_fullscreen.setText("Sair Fullscreen" if self.is_fullscreen_state else "⛶ Tela Cheia [F]")
        self.fullscreen_toggled.emit(self.is_fullscreen_state)

    def _on_player_position_changed(self, position: int) -> None:
        if self.is_live:
            return
        if not self.is_seeking:
            dur = self.player.duration()
            if dur > 0:
                self.slider.setValue(int((position / dur) * 1000))
                self.time_current_lbl.setText(format_duration(position / 1000))
        if self.content_id and position > 0:
            self.progress_updated.emit(self.content_id, position, self.player.duration())

    def _on_player_duration_changed(self, duration: int) -> None:
        if self.is_live:
            return
        self.time_total_lbl.setText(format_duration(duration / 1000))
        if duration > 0:
            res_str = f"{self.video_widget.width()}x{self.video_widget.height()}"
            if self.video_widget.width() > 1200:
                self.stream_diag.resolution = "1080p FHD (1920x1080)"
            else:
                self.stream_diag.resolution = "720p HD (1280x720)"
            self.hud.update_diagnostics(self.stream_diag)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_play.setText("⏸ Pausar")
        else:
            self.btn_play.setText("▶ Reproduzir")

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.content_id:
                self.episode_finished.emit(self.content_id)
            if self.has_next_episode:
                self.next_episode_requested.emit()

    def _on_seek_moved(self, value: int) -> None:
        self.is_seeking = True
        dur = self.player.duration()
        if dur > 0:
            pos = int((value / 1000.0) * dur)
            self.time_current_lbl.setText(format_duration(pos / 1000))

    def _on_seek_released(self) -> None:
        self.is_seeking = False
        dur = self.player.duration()
        if dur > 0:
            pos = int((self.slider.value() / 1000.0) * dur)
            self.player.setPosition(pos)

    def _on_download_clicked(self) -> None:
        if self.content_id and not self.is_live:
            self.download_requested.emit(self.content_id, self.kind)
            self.btn_download.setText("✓ Baixando...")
            QTimer.singleShot(2500, lambda: self.btn_download.setText("⬇ Baixar"))

    def toggle_controls_bar(self) -> None:
        self.is_controls_collapsed = not self.is_controls_collapsed
        if self.is_controls_collapsed:
            self.controls_bar.hide()
            self.btn_expand.show()
            self.btn_expand.raise_()
            hint = self.btn_expand.sizeHint()
            self.btn_expand.move((self.width() - hint.width()) // 2, self.height() - hint.height() - 8)
        else:
            self.controls_bar.show()
            self.btn_expand.hide()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)
        if not self.is_controls_collapsed:
            self.controls_bar.show()
            self.controls_hide_timer.start()
        else:
            y = event.position().y() if hasattr(event, "position") else event.y()
            if y > self.height() - 70:
                self.btn_expand.show()
                self.btn_expand.raise_()

    def _auto_hide_controls(self) -> None:
        if self.is_controls_collapsed:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and (self.is_fullscreen_state or self.is_cinema_state):
            self.controls_bar.hide()

