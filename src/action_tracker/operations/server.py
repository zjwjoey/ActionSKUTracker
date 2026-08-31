from __future__ import annotations

import json
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import re

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
                html = f"<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>ActionSKUTracker Workspace</title><style>body{{font-family:system-ui;margin:2rem;background:#f5f7fa;color:#17202a}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem}}.card{{background:white;border:1px solid #d9e2ec;border-radius:8px;padding:1rem}}.value{{font-size:1.8rem;font-weight:700}}a{{margin-right:1rem}}</style><h1>ActionSKUTracker Workspace</h1><p>本机只读控制台 · 状态: <b>{status['state']}</b> · 健康: <b>{health['state']}</b></p><div class='grid'><div class='card'>CURRENT SKU<div class='value'>{status['current_sku']}</div></div><div class='card'>数据库角色<div class='value'>{db_role}</div></div><div class='card'>待导出同步<div class='value'>{status['database'].get('pending_export_sync', 0)}</div></div><div class='card'>待审核<div class='value'>{status['reviews_open']}</div></div><div class='card'>AI队列<div class='value'>{status['translation_queue_pending']}</div></div></div><p><a href='/workspace'>商品 Workspace</a><a href='/views'>Saved Views</a><a href='/selections'>Selections</a><a href='/artifacts'>导出记录</a><a href='/api/runs'>运行记录</a><a href='/api/quality'>数据质量</a><a href='/api/health'>系统健康</a></p></html>"
                return self._send_html(html)
            if path == "/workspace":
                params = parse_qs(urlparse(self.path).query); keyword = (params.get("keyword") or [""])[-1]
                result = svc.extract({"keyword": keyword, "statuses": ["CURRENT"], "limit": 50})
                esc = lambda value: html.escape(str(value or ""))
                rows = "".join(f"<tr><td>{esc(item.get('official_sku'))}</td><td>{esc(item.get('name_es'))}</td><td>{esc(item.get('zh_name'))}</td><td>{esc(item.get('status'))}</td><td>{esc(item.get('current_price'))}</td><td>{esc(item.get('image_status'))}</td></tr>" for item in result["items"])
                safe_keyword = html.escape(keyword, quote=True)
                return self._send_html(f"<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>商品 Workspace</title><style>body{{font-family:system-ui;margin:2rem}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:.4rem;text-align:left}}th{{background:#eef2f7}}input{{padding:.4rem;width:22rem}}</style><h1>商品 Workspace</h1><form><input name='keyword' value='{safe_keyword}' placeholder='SKU / 西语或中文关键词'><button>查询</button></form><p>匹配 {result['matched_count']} 条，当前显示 {len(result['items'])} 条 · <a href='/'>返回</a></p><table><tr><th>SKU</th><th>西语名称</th><th>中文名称</th><th>状态</th><th>价格</th><th>图片</th></tr>{rows}</table></html>")
            if path == "/api/status": return self._send(svc.system_status())
            if path == "/api/runs": return self._send(svc.run_history())
            match = re.fullmatch(r"/api/runs/([^/]+)", path)
            if match: return self._send(svc.run_detail(match.group(1)))
            if path == "/api/health": return self._send(svc.health())
            if path == "/api/quality": return self._send(svc.data_quality())
            if path == "/api/products":
                params = parse_qs(urlparse(self.path).query)
                query = {key: values[-1] for key, values in params.items() if values}
                for key in ("statuses", "skus", "cat1", "cat2"):
                    if key in query: query[key] = tuple(x for x in query[key].split(",") if x)
                for key in ("limit", "offset", "min_price", "max_price"):
                    if key in query:
                        try: query[key] = float(query[key]) if "price" in key else int(query[key])
                        except ValueError: return self._send({"error": f"INVALID_{key}"})
                for key in ("promotion", "new_badge", "sustainable", "has_original_price", "has_image"):
                    if key in query:
                        query[key] = str(query[key]).lower() in {"1", "true", "yes", "on"}
                return self._send(svc.extract(query))
            if path == "/api/views": return self._send(svc.saved_views())
            if path == "/api/selections": return self._send(svc.selections())
            if path == "/api/artifacts": return self._send(svc.artifacts())
            if path == "/views":
                views = svc.saved_views(); rows = "".join(f"<tr><td>{html.escape(str(v['name']))}</td><td>{html.escape(str(v['description']))}</td><td>{html.escape(str(v['query_hash'])[:12])}</td></tr>" for v in views)
                return self._send_html(f"<!doctype html><meta charset='utf-8'><title>Saved Views</title><h1>Saved Views</h1><form method='post' action='/api/views'><input name='name' required placeholder='名称'><input name='description' placeholder='说明'><input name='query_json' required value='{{\"statuses\":[\"CURRENT\"]}}' size='45'><button>保存</button></form><table border='1'><tr><th>名称</th><th>说明</th><th>Query</th></tr>{rows}</table><p><a href='/'>返回</a></p>")
            if path == "/selections":
                selections = svc.selections(); rows = "".join(f"<tr><td>{html.escape(str(s['name']))}</td><td>{s['matched_count']}</td><td>{html.escape(str(s['source_commit_id'] or ''))}</td></tr>" for s in selections)
                return self._send_html(f"<!doctype html><meta charset='utf-8'><title>Selections</title><h1>Selections</h1><form method='post' action='/api/selections'><input name='name' required placeholder='名称'><input name='description' placeholder='说明'><input name='query_json' required value='{{\"statuses\":[\"CURRENT\"]}}' size='45'><button>创建固定选择集</button></form><table border='1'><tr><th>名称</th><th>SKU 数</th><th>来源提交</th></tr>{rows}</table><p><a href='/'>返回</a></p>")
            if path == "/artifacts":
                artifacts = svc.artifacts(); rows = "".join(f"<tr><td>{html.escape(str(a['artifact_type']))}</td><td>{html.escape(str(a.get('selection_id') or ''))}</td><td>{a['row_count']}</td><td>{html.escape(str(a['status']))}</td><td>{html.escape(str(a['file_path']))}</td></tr>" for a in artifacts)
                return self._send_html(f"<!doctype html><meta charset='utf-8'><title>Artifacts</title><h1>导出记录</h1><table border='1'><tr><th>类型</th><th>Selection</th><th>行数</th><th>状态</th><th>文件</th></tr>{rows}</table><p><a href='/'>返回</a></p>")
            self.send_error(404)
        def do_POST(self):
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length", "0"))
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            try:
                if path == "/api/views":
                    from ..extraction.selections import SavedViewService
                    result = SavedViewService(svc.db_path).create((form.get("name") or [""])[-1], json.loads((form.get("query_json") or ["{}"]) [-1]), (form.get("description") or [""])[-1]); return self._send(result)
                if path == "/api/selections":
                    from ..extraction.selections import SelectionService
                    result = SelectionService(svc.db_path).create((form.get("name") or [""])[-1], json.loads((form.get("query_json") or ["{}"]) [-1]), description=(form.get("description") or [""])[-1]); return self._send(result)
            except Exception as exc:
                return self._send({"error": str(exc)})
            self.send_error(404)
        def log_message(self, *_args): return
    ThreadingHTTPServer((host, port), Handler).serve_forever()
