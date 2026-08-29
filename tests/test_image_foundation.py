from io import BytesIO
from pathlib import Path

from PIL import Image

from action_tracker.images.assets import ImageManifest
from action_tracker.images.derivatives import ImageDerivativeService
from action_tracker.images.sync import ImageSyncService


def _png(size=(100, 50), color=(20, 80, 150, 255)) -> bytes:
    image = Image.new("RGBA", size, color)
    buf = BytesIO()
    image.save(buf, format="PNG")
    image.close()
    return buf.getvalue()


def test_image_sync_normalizes_and_reuses(tmp_path: Path):
    manifest = tmp_path / "image_manifest.csv"
    calls = []

    def download(url, timeout):
        calls.append(url)
        return _png()

    service = ImageSyncService(asset_root=tmp_path / "assets", staging_root=tmp_path / "staging", manifest_path=manifest, downloader=download)
    first = service.sync([{"sku": "1001", "image_url": "https://asset.action.com/1001.webp"}], run_id="run-1")
    assert first["downloaded_count"] == 1
    assert first["available_count"] == 1
    assert Image.open(tmp_path / "assets" / "1001" / "master.png").format == "PNG"
    second = service.sync([{"sku": "1001", "image_url": "https://asset.action.com/1001.webp"}], run_id="run-2")
    assert second["reused_count"] == 1
    assert len(calls) == 1


def test_image_sync_tracks_missing_and_bad_content(tmp_path: Path):
    def download(url, timeout):
        return b"not-an-image"

    service = ImageSyncService(asset_root=tmp_path / "assets", staging_root=tmp_path / "staging", manifest_path=tmp_path / "manifest.csv", downloader=download, max_retries=0)
    result = service.sync([{"sku": "1", "image_url": ""}, {"sku": "2", "image_url": "https://asset.action.com/2.webp"}], run_id="run")
    assert result["missing_source_url_count"] == 1
    assert result["download_failed_count"] == 1
    records = ImageManifest(tmp_path / "manifest.csv").records
    assert records["1"].download_status == "NO_SOURCE_URL"
    assert records["2"].download_status == "INVALID_CONTENT"


def test_derivative_is_250_white_and_aspect_preserving(tmp_path: Path):
    master = tmp_path / "master.png"
    Image.open(BytesIO(_png((100, 50)))).save(master)
    output = ImageDerivativeService(tmp_path / "derivatives").excel_250(master, "1001")
    with Image.open(output) as image:
        assert image.size == (250, 250)
        assert image.mode == "RGB"
        assert image.getpixel((0, 0)) == (255, 255, 255)
