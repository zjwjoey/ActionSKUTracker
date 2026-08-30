from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .service import OperationsService


def serve(service: OperationsService, *, host: str = "127.0.0.1", port: int = 8787) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("CONTROL_CENTER_LOCALHOST_ONLY")
    svc = service
    class Handler(BaseHTTPRequestHandler):
        def _send(self, payload):
            data = json.dumps(payload, ensure_ascii=False, default=str).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        def _send_html(self, html):
            data = html.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                status = svc.system_status(); health = svc.health(); db_role = status["database"].get("metadata", {}).get("database_role", "-")
                html = f"<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>ActionSKUTracker Operations</title><style>body{{font-family:system-ui;margin:2rem;background:#f5f7fa;color:#17202a}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem}}.card{{background:white;border:1px solid #d9e2ec;border-radius:8px;padding:1rem}}.value{{font-size:1.8rem;font-weight:700}}</style><h1>ActionSKUTracker Operations</h1><p>本机只读控制台 · 状态: <b>{status['state']}</b> · 健康: <b>{health['state']}</b></p><div class='grid'><div class='card'>CURRENT SKU<div class='value'>{status['current_sku']}</div></div><div class='card'>数据库角色<div class='value'>{db_role}</div></div><div class='card'>待导出同步<div class='value'>{status['database'].get('pending_export_sync', 0)}</div></div><div class='card'>待审核<div class='value'>{status['reviews_open']}</div></div><div class='card'>AI队列<div class='value'>{status['translation_queue_pending']}</div></div></div><p><a href='/api/status'>Status JSON</a> · <a href='/api/runs'>Runs JSON</a> · <a href='/api/quality'>Quality JSON</a> · <a href='/api/health'>Health JSON</a></p></html>"
                return self._send_html(html)
            if path == "/api/status": return self._send(svc.system_status())
            if path == "/api/runs": return self._send(svc.run_history())
            if path == "/api/health": return self._send(svc.health())
            if path == "/api/quality": return self._send(svc.data_quality())
            self.send_error(404)
        def log_message(self, *_args): return
    ThreadingHTTPServer((host, port), Handler).serve_forever()
