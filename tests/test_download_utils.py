from app.download_manager import infer_extension


def test_extension_from_content_disposition() -> None:
    assert infer_extension({"Content-Disposition": 'attachment; filename="video.mkv"'}, "https://x/42") == "mkv"


def test_extension_from_content_type() -> None:
    assert infer_extension({"Content-Type": "video/mp4; charset=binary"}, "https://x/42") == "mp4"

