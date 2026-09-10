from app.download_manager import DownloadManager, infer_extension, safe_error_message
from app.models import DownloadStatus, DownloadTask


def test_extension_from_content_disposition() -> None:
    assert infer_extension({"Content-Disposition": 'attachment; filename="video.mkv"'}, "https://x/42") == "mkv"


def test_extension_from_content_type() -> None:
    assert infer_extension({"Content-Type": "video/mp4; charset=binary"}, "https://x/42") == "mp4"


def test_error_message_redacts_authenticated_url() -> None:
    message = safe_error_message(RuntimeError("failed https://server/movie/user/secret/42.mp4"))
    assert "secret" not in message
    assert "URL protegida" in message


def test_download_deletion_cannot_escape_root(tmp_path) -> None:
    root = tmp_path / "downloads"
    root.mkdir()
    outside = tmp_path / "important.txt"
    outside.write_text("keep", encoding="utf-8")
    manager = DownloadManager(history_path=tmp_path / "history.json", download_root=root)
    task = DownloadTask(
        id="tampered", kind="movie", title="Tampered", url_candidates=[],
        destination_dir=str(root), base_filename="safe", extension="mp4",
        status=DownloadStatus.COMPLETED,
    )
    task.output_path = str(outside)
    manager.tasks[task.id] = task
    manager.delete_task_and_file(task.id)
    assert outside.exists()

