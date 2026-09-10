from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(value: str, fallback: str = "Sem título", max_length: int = 150) -> str:
    cleaned = INVALID_WINDOWS_CHARS.sub("_", value).strip().rstrip(". ")
    cleaned = re.sub(r"\s+", " ", cleaned)[:max_length].rstrip(". ")
    if not cleaned:
        cleaned = fallback
    if cleaned.upper() in RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def format_duration(seconds: int | float | None) -> str:
    if not seconds or seconds < 0:
        return "00:00"
    total_sec = int(seconds)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def human_size(value: int | float | None, suffix: str = "B") -> str:
    if value is None:
        return "—"
    number = float(value)
    for unit in ("", "K", "M", "G", "T"):
        if abs(number) < 1024:
            return f"{number:,.1f} {unit}{suffix}".replace(",", ".")
        number /= 1024
    return f"{number:.1f} P{suffix}"


class DownloadStatus(str, Enum):
    QUEUED = "Na fila"
    DOWNLOADING = "Baixando"
    PAUSED = "Pausado"
    COMPLETED = "Concluído"
    CANCELLED = "Cancelado"
    FAILED = "Falhou"


@dataclass(slots=True)
class DownloadTask:
    id: str
    kind: str
    title: str
    url_candidates: list[str]
    destination_dir: str
    base_filename: str
    extension: str | None = None
    status: DownloadStatus = DownloadStatus.QUEUED
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed_bps: float = 0.0
    error: str | None = None
    output_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    max_retries: int = 3
    eta_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DownloadTask":
        values = dict(data)
        values["status"] = DownloadStatus(values.get("status", DownloadStatus.QUEUED.value))
        if values["status"] == DownloadStatus.DOWNLOADING:
            values["status"] = DownloadStatus.PAUSED
        # Backward compatibility for newly added fields
        values.setdefault("retry_count", 0)
        values.setdefault("max_retries", 3)
        values.setdefault("eta_seconds", None)
        allowed = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in values.items() if k in allowed}
        return cls(**filtered)

    @property
    def destination(self) -> Path:
        suffix = f".{self.extension.lstrip('.')}" if self.extension else ""
        return Path(self.destination_dir) / f"{self.base_filename}{suffix}"


@dataclass(slots=True)
class Account:
    id: str
    name: str
    account_type: str = "xtream"  # "xtream" or "m3u"
    server_url: str = ""
    username: str = ""
    password: str = ""
    m3u_url: str = ""
    is_active: bool = True
    last_synced: float | None = None
    exp_date: str | None = None
    max_connections: int | str = 1
    active_connections: int | str = 0
    latency_ms: int | None = None
    status_text: str = "Pronto"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Account":
        allowed = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass(slots=True)
class CustomPlaylist:
    id: str
    name: str
    emoji: str = "📁"
    color: str = "#1749e8"
    item_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CustomPlaylist":
        allowed = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass(slots=True)
class Profile:
    id: str
    name: str
    avatar: str = "⚡"
    is_kids: bool = False
    pin: str = ""  # Specific PIN for this profile if set
    favorites: list[str] = field(default_factory=list)      # List of content IDs
    watchlist: list[str] = field(default_factory=list)      # "Minha Lista" content IDs
    progress: dict[str, dict[str, Any]] = field(default_factory=dict)  # id -> {position_ms, duration_ms, updated_at}
    history: list[dict[str, Any]] = field(default_factory=list)        # [{id, title, kind, timestamp, progress_pct}]
    hidden_categories: list[str] = field(default_factory=list)
    custom_playlists: list[dict[str, Any]] = field(default_factory=list)  # list of CustomPlaylist dicts

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        allowed = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in data.items() if k in allowed})



@dataclass(slots=True)
class MediaItem:
    id: str
    kind: str  # "movie", "series", "live"
    title: str
    stream_id: str | int
    category_id: str = "0"
    category_name: str = "Geral"
    poster: str = ""
    backdrop: str = ""
    synopsis: str = ""
    rating: float | str = ""
    year: str = ""
    genre: str = ""
    duration_str: str = ""
    container_extension: str = "mp4"
    stream_url: str = ""
    account_id: str = ""
    epg_channel_id: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MediaItem":
        allowed = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass(slots=True)
class Episode:
    id: str
    series_id: str | int
    season_num: int
    episode_num: int
    title: str
    container_extension: str = "mp4"
    duration_sec: int = 0
    plot: str = ""
    cover: str = ""
    stream_url: str = ""
    is_watched: bool = False
    progress_ms: int = 0
    duration_ms: int = 0
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Episode":
        allowed = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass(slots=True)
class EPGProgram:
    id: str
    stream_id: str | int
    title: str
    start_time: str
    end_time: str
    description: str = ""
    now_playing: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StreamDiagnostics:
    codec_video: str = "H.264 / AVC"
    codec_audio: str = "AAC-LC"
    resolution: str = "1080p (1920x1080)"
    fps: float = 60.0
    bitrate_kbps: int = 4800
    latency_ms: int = 42
    buffer_seconds: float = 8.5
    dropped_frames: int = 0
    source_server: str = ""
