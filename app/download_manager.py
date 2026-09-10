from __future__ import annotations

import json
import mimetypes
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

import requests
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from app.models import DownloadStatus, DownloadTask, safe_filename
from app.paths import app_data_dir


CONTENT_EXTENSIONS = {
    "video/mp4": "mp4", "video/x-matroska": "mkv", "video/webm": "webm",
    "video/mp2t": "ts", "video/mpeg": "mpeg", "application/octet-stream": None,
}


class WorkerSignals(QObject):
    progress = Signal(str, int, object, float, object)  # id, downloaded, total, speed, eta
    finished = Signal(str, str)
    failed = Signal(str, str)
    cancelled = Signal(str)


def infer_extension(headers: dict[str, str], url: str) -> str | None:
    disposition = headers.get("Content-Disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8''|\")?[^;\"]*\.([A-Za-z0-9]{2,5})", disposition, re.I)
    if match:
        return match.group(1).lower()
    content_type = headers.get("Content-Type", "").split(";", 1)[0].lower()
    mapped = CONTENT_EXTENSIONS.get(content_type)
    if mapped:
        return mapped
    guessed = mimetypes.guess_extension(content_type) if content_type else None
    if guessed:
        return guessed.lstrip(".")
    suffix = Path(url.split("?", 1)[0]).suffix.lstrip(".")
    return suffix if 1 < len(suffix) <= 5 else None


class DownloadWorker(QRunnable):
    def __init__(self, task: DownloadTask, use_ffmpeg: bool = True) -> None:
        super().__init__()
        self.task = task
        self.use_ffmpeg = use_ffmpeg
        self.signals = WorkerSignals()
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        last_error = "URL indisponível"
        # Retry loop for resilience
        while self.task.retry_count <= self.task.max_retries:
            if self._cancel.is_set():
                self.signals.cancelled.emit(self.task.id)
                return

            success = False
            for url in self.task.url_candidates:
                if self._cancel.is_set():
                    self.signals.cancelled.emit(self.task.id)
                    return
                try:
                    output = self._download_http(url)
                    self.signals.finished.emit(self.task.id, str(output))
                    return
                except InterruptedError:
                    self.signals.cancelled.emit(self.task.id)
                    return
                except Exception as exc:
                    last_error = str(exc)

            if self.use_ffmpeg and shutil.which("ffmpeg") and not self._cancel.is_set():
                try:
                    output = self._download_ffmpeg(self.task.url_candidates[0])
                    self.signals.finished.emit(self.task.id, str(output))
                    return
                except InterruptedError:
                    self.signals.cancelled.emit(self.task.id)
                    return
                except Exception as exc:
                    last_error = str(exc)

            self.task.retry_count += 1
            if self.task.retry_count <= self.task.max_retries and not self._cancel.is_set():
                time.sleep(1.5 * self.task.retry_count)
            else:
                break

        self.signals.failed.emit(self.task.id, last_error)

    def _download_http(self, url: str) -> Path:
        destination_dir = Path(self.task.destination_dir)
        destination_dir.mkdir(parents=True, exist_ok=True)
        temp = destination_dir / f"{self.task.base_filename}.part"
        existing = temp.stat().st_size if temp.exists() else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}

        with requests.get(url, headers=headers, stream=True, timeout=(10, 60), allow_redirects=True) as response:
            if response.status_code == 416 and existing:
                temp.unlink(missing_ok=True)
                return self._download_http(url)
            response.raise_for_status()
            append = existing > 0 and response.status_code == 206
            if existing and not append:
                existing = 0
            extension = self.task.extension or infer_extension(dict(response.headers), response.url) or "mp4"
            destination = destination_dir / f"{self.task.base_filename}.{extension.lstrip('.')}"
            length = response.headers.get("Content-Length")
            total = (int(length) + existing) if length and length.isdigit() else None
            downloaded = existing
            started = time.monotonic()
            checkpoint = started
            checkpoint_bytes = downloaded

            with temp.open("ab" if append else "wb") as stream:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if self._cancel.is_set():
                        raise InterruptedError("Download cancelado")
                    if not chunk:
                        continue
                    stream.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if now - checkpoint >= 0.25:
                        speed = (downloaded - checkpoint_bytes) / max(now - checkpoint, 0.001)
                        eta = (total - downloaded) / speed if total and speed > 1024 else None
                        self.signals.progress.emit(self.task.id, downloaded, total, speed, eta)
                        checkpoint, checkpoint_bytes = now, downloaded

            self.signals.progress.emit(self.task.id, downloaded, total, 0.0, 0.0)
            temp.replace(destination)
            return destination

    def _download_ffmpeg(self, url: str) -> Path:
        destination_dir = Path(self.task.destination_dir)
        destination_dir.mkdir(parents=True, exist_ok=True)
        extension = self.task.extension or "mkv"
        destination = destination_dir / f"{self.task.base_filename}.{extension}"
        process = subprocess.Popen(
            ["ffmpeg", "-y", "-i", url, "-c", "copy", str(destination)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        while process.poll() is None:
            if self._cancel.wait(0.25):
                process.terminate()
                destination.unlink(missing_ok=True)
                raise InterruptedError("Download cancelado")
        if process.returncode:
            detail = (process.stderr.read() if process.stderr else "")[-500:]
            raise RuntimeError(f"ffmpeg não conseguiu baixar o conteúdo. {detail}")
        return destination


class DownloadManager(QObject):
    task_added = Signal(object)
    task_updated = Signal(object)
    task_removed = Signal(str)

    def __init__(self, history_path: Path | None = None, max_concurrent: int = 2) -> None:
        super().__init__()
        self.history_path = history_path or app_data_dir() / "history.json"
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max_concurrent)
        self.tasks: dict[str, DownloadTask] = {}
        self._pending: deque[str] = deque()
        self._workers: dict[str, DownloadWorker] = {}
        self.use_ffmpeg_fallback = True
        self._load()

    def configure(self, max_concurrent: int, use_ffmpeg: bool) -> None:
        self.pool.setMaxThreadCount(max(1, min(max_concurrent, 6)))
        self.use_ffmpeg_fallback = use_ffmpeg

    def add(self, **kwargs: Any) -> DownloadTask:
        task_id = kwargs.pop("id", str(uuid.uuid4()))
        task = DownloadTask(id=task_id, **kwargs)
        self.tasks[task.id] = task
        self._pending.append(task.id)
        self.task_added.emit(task)
        self._save()
        self._start_next()
        return task

    def pause(self, task_id: str) -> None:
        if worker := self._workers.get(task_id):
            worker.cancel()
        elif task_id in self.tasks:
            task = self.tasks[task_id]
            task.status = DownloadStatus.PAUSED
            task.speed_bps = 0.0
            self.task_updated.emit(task)
            self._save()

    def resume(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if not task or task.status == DownloadStatus.DOWNLOADING:
            return
        task.status = DownloadStatus.QUEUED
        task.error = None
        self._pending.append(task_id)
        self.task_updated.emit(task)
        self._start_next()

    def retry(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if not task:
            return
        task.status, task.error, task.retry_count = DownloadStatus.QUEUED, None, 0
        self._pending.append(task_id)
        self.task_updated.emit(task)
        self._start_next()

    def cancel(self, task_id: str) -> None:
        if worker := self._workers.get(task_id):
            worker.cancel()
        elif task_id in self.tasks:
            task = self.tasks[task_id]
            task.status = DownloadStatus.CANCELLED
            self.task_updated.emit(task)
            self._save()

    def delete_task_and_file(self, task_id: str) -> None:
        self.cancel(task_id)
        task = self.tasks.pop(task_id, None)
        if task:
            try:
                if task.output_path and Path(task.output_path).exists():
                    Path(task.output_path).unlink(missing_ok=True)
                dest = task.destination
                if dest.exists():
                    dest.unlink(missing_ok=True)
                part = dest.with_suffix(".part")
                if part.exists():
                    part.unlink(missing_ok=True)
            except Exception:
                pass
            self.task_removed.emit(task_id)
            self._save()

    def get_completed_tasks(self) -> list[DownloadTask]:
        """Returns completed tasks whose files exist on disk for the Offline Library"""
        completed = []
        for task in self.tasks.values():
            if task.status == DownloadStatus.COMPLETED:
                path = task.output_path or str(task.destination)
                if path and Path(path).exists():
                    completed.append(task)
        return completed

    def _start_next(self) -> None:
        while self._pending and len(self._workers) < self.pool.maxThreadCount():
            task_id = self._pending.popleft()
            task = self.tasks.get(task_id)
            if not task or task.status != DownloadStatus.QUEUED:
                continue
            task.status = DownloadStatus.DOWNLOADING
            worker = DownloadWorker(task, self.use_ffmpeg_fallback)
            worker.signals.progress.connect(self._on_progress)
            worker.signals.finished.connect(self._on_finished)
            worker.signals.failed.connect(self._on_failed)
            worker.signals.cancelled.connect(self._on_cancelled)
            self._workers[task_id] = worker
            self.task_updated.emit(task)
            self.pool.start(worker)

    def _on_progress(self, task_id: str, downloaded: int, total: object, speed: float, eta: object) -> None:
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.downloaded_bytes = downloaded
            task.total_bytes = total if isinstance(total, int) else None
            task.speed_bps = speed
            task.eta_seconds = float(eta) if isinstance(eta, (int, float)) else None
            self.task_updated.emit(task)

    def _finish(self, task_id: str) -> DownloadTask | None:
        self._workers.pop(task_id, None)
        task = self.tasks.get(task_id)
        self._save()
        self._start_next()
        return task

    def _on_finished(self, task_id: str, path: str) -> None:
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.status, task.output_path, task.speed_bps = DownloadStatus.COMPLETED, path, 0
            self.task_updated.emit(task)
        self._finish(task_id)

    def _on_failed(self, task_id: str, error: str) -> None:
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.status, task.error, task.speed_bps = DownloadStatus.FAILED, error, 0
            self.task_updated.emit(task)
        self._finish(task_id)

    def _on_cancelled(self, task_id: str) -> None:
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.status, task.speed_bps = DownloadStatus.CANCELLED, 0
            self.task_updated.emit(task)
        self._finish(task_id)

    def _load(self) -> None:
        if not self.history_path.exists():
            return
        try:
            for raw in json.loads(self.history_path.read_text(encoding="utf-8")):
                task = DownloadTask.from_dict(raw)
                self.tasks[task.id] = task
        except (OSError, ValueError, TypeError):
            self.tasks = {}

    def _save(self) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        data = [task.to_dict() for task in self.tasks.values()]
        temp = self.history_path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.history_path)
