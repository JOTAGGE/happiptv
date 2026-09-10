from app.models import DownloadStatus, DownloadTask, safe_filename


def test_safe_filename_for_windows() -> None:
    assert safe_filename('CON') == '_CON'
    assert safe_filename('Filme: Parte 1? ') == 'Filme_ Parte 1_'
    assert safe_filename('  ') == 'Sem título'


def test_interrupted_download_is_loaded_as_paused() -> None:
    task = DownloadTask(
        id="1", kind="movie", title="T", url_candidates=["https://x/1"],
        destination_dir="downloads", base_filename="T", status=DownloadStatus.DOWNLOADING,
    )
    restored = DownloadTask.from_dict(task.to_dict())
    assert restored.status == DownloadStatus.PAUSED

