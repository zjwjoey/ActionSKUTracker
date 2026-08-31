import json
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from action_tracker.operations.server import serve
from action_tracker.operations.service import OperationsService
from test_extraction_v2 import _db


def _url(base: str, path: str):
    return base + path


def _json_request(url: str, *, method: str = "GET", form: dict | None = None):
    body = urlencode(form or {}).encode() if form is not None else None
    request = Request(url, data=body, method=method)
    if body is not None: request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


@pytest.fixture
def workspace_server(tmp_path: Path):
    db = _db(tmp_path)
    service = OperationsService(db, reports_root=tmp_path / "reports", lock_path=tmp_path / "lock")
    probe = socket.socket(); probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]; probe.close()
    thread = threading.Thread(target=serve, args=(service,), kwargs={"port": port}, daemon=True); thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(30):
        try:
            with urlopen(base + "/api/health", timeout=1): break
        except OSError: time.sleep(0.02)
    return base


def test_workspace_query_saved_view_selection_and_artifact_routes(workspace_server: str):
    base = workspace_server
    with urlopen(base + "/workspace?statuses=CURRENT&min_price=2", timeout=5) as response:
        html = response.read().decode("utf-8")
    assert response.status == 200 and "保存为 Saved View" in html and "最低价" in html
    status, products = _json_request(base + "/api/products?statuses=CURRENT&limit=1")
    assert status == 200 and products["matched_count"] == 1
    status, view = _json_request(base + "/api/views", method="POST", form={"name": "测试View", "query_json": json.dumps({"statuses": ["CURRENT"]})})
    assert status == 200 and view["view_id"]
    status, ran = _json_request(base + f"/api/views/{view['view_id']}/run")
    assert status == 200 and ran["matched_count"] == 1
    status, _ = _json_request(base + f"/api/views/{view['view_id']}", method="PUT", form={"name": "更新View", "query_json": json.dumps({"statuses": ["OFFLINE"]})})
    assert status == 200
    status, _ = _json_request(base + f"/api/views/{view['view_id']}", method="DELETE")
    assert status == 200
    status, selection = _json_request(base + "/api/selections", method="POST", form={"name": "测试Selection", "query_json": json.dumps({"statuses": ["CURRENT"]})})
    assert status == 200 and selection["matched_count"] == 1
    status, detail = _json_request(base + f"/api/selections/{selection['selection_id']}")
    assert status == 200 and detail["members"] and "items" in detail and "artifacts" in detail
    status, artifacts = _json_request(base + f"/api/selections/{selection['selection_id']}/artifacts")
    assert status == 200 and artifacts == []


def test_workspace_server_rejects_public_bind(tmp_path: Path):
    with pytest.raises(ValueError, match="LOCALHOST_ONLY"):
        serve(OperationsService(_db(tmp_path)), host="0.0.0.0", port=0)
