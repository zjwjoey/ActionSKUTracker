from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from action_tracker.database.connection import connect, transaction
from action_tracker.database.mirror import build_mirror
from action_tracker.database.schema import migrate
from action_tracker.database.validation import validate_mirror


def _sheet(wb: Workbook, name: str, headers: list[str], rows: list[list[object]], header_row: int = 1):
    ws = wb.create_sheet(name)
    for _ in range(header_row - 1):
        ws.append([])
    ws.append(headers)
    for row in rows:
        ws.append(row)
    return ws


def make_fixture(path: Path) -> None:
    wb = Workbook()
    wb.remove(wb.active)
    _sheet(wb, "01_SKU_ZH_CURRENT", [
        "SKU", "中文品名", "一级类目（中文）", "二级类目（中文）", "规格（中文）",
        "中文描述", "中文产品详情", "翻译状态",
    ], [["100", "测试商品", "家居", "收纳", "1件", "描述", "详情", "CONFIRMED"]])
    _sheet(wb, "02_SKU_ES_CURRENT", [
        "Canonical_ID", "SKU", "西班牙语品名", "当前售价 (€)", "原价 (€)", "上次售价 (€)",
        "最近一次变价方向", "本期价格变化", "变化金额 (€)", "变化幅度 (%)", "最近变价日期",
        "历史最低价 (€)", "历史最高价 (€)", "一级类目（西语）", "二级类目（西语）",
        "规格（西语）", "单价", "新品", "促销", "可持续", "折扣", "原始标签", "当前状态",
        "首次发现日期", "最后确认存在日期", "描述（西语）", "产品详情（西语）", "商品链接", "图片链接", "匹配状态",
    ], [["ACT-1", "100", "Producto de prueba", 2.5, 3.0, 2.0, "UP", "本期上涨", 0.5, 25, "2026-08-29", 1.0, 3.0, "Hogar", "Almacenaje", "1 unidad", "2,50 €/ud", 0, 0, 0, 0, "", "CURRENT", "2026-01-01", "2026-08-29", "Descripción ES", "Detalle ES", "https://example/100", "https://example/image", "OK"]])
    _sheet(wb, "03_PRICE_HISTORY", ["Canonical_ID", "SKU", "日期", "旧售价 (€)", "新售价 (€)", "原价 (€)", "变化类型", "变化金额 (€)", "变化幅度 (%)", "促销状态", "来源文件", "来源Sheet"], [["ACT-1", "100", "2026-08-29", 2.0, 2.5, 3.0, "UP", 0.5, 25, "", "run.xlsx", "Sheet1"], ["ACT-X", "", "2026-04-05", None, 1.59, None, "NEW", None, None, "", "old.xlsx", "Sheet1"]])
    _sheet(wb, "04_EVENT_HISTORY", ["Canonical_ID", "SKU", "日期", "事件类型", "旧值", "新值", "来源文件", "备注"], [["ACT-1", "100", "2026-08-29", "PRICE_UP", "2", "2.5", "run.xlsx", "evidence"], ["ACT-X", "", "2026-04-05", "FIRST_SEEN", "", "", "old.xlsx", "pending"]])
    _sheet(wb, "05_RUN_LOG", ["Run ID", "运行日期", "开始时间", "结束时间", "Git Commit", "Sitemap SKU数", "Listing SKU数", "ACTIVE", "NEW", "REAPPEARED", "MISSING_FIRST", "MISSING_CONTINUED", "OFFLINE", "PRICE_UP", "PRICE_DOWN", "PROMO_START", "PROMO_END", "NEW_BADGE_ON", "NEW_BADGE_OFF", "CONTENT_CHANGE", "异常数量", "QA状态", "运行状态", "备注"], [["run-1", "2026-08-29", "2026-08-29T01:00:00", "2026-08-29T01:10:00", "abc", 2, 1, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, "PASS", "FULL_COMMIT", ""]])
    _sheet(wb, "06_REVIEW_QUEUE", ["日期", "SKU", "问题类型", "证据", "候选值", "置信度", "建议动作", "人工备注"], [["2026-08-29", "100", "NAME", "evidence", "测试商品", 0.9, "REVIEW", ""]])
    _sheet(wb, "07_APRIL_ARCHIVE", ["四月归档ID", "正式SKU"], [["APR-1", "100"]])
    _sheet(wb, "08_LONG_TERM_MASTER", ["实体ID", "正式SKU", "四月归档ID", "身份类型", "匹配状态", "匹配置信度", "当前状态", "中文品名", "西班牙语品名", "一级类目（中文）", "一级类目（西语）", "规格（中文）", "规格（西语）", "当前售价 (€)", "历史最低价 (€)", "历史最高价 (€)", "首次观察日期", "最后观察日期", "四月原始记录数", "四月归档ID集合", "来源数", "来源工作表", "商品链接", "核对备注"], [["ENT-1", "100", "APR-1", "MATCHED", "MATCHED", 1.0, "CURRENT", "测试商品", "Producto de prueba", "家居", "Hogar", "1件", "1 unidad", 2.5, 1.0, 3.0, "2026-01-01", "2026-08-29", 1, "APR-1", 1, "01", "https://example/100", ""], ["ENT-2", "200", "", "NEW", "MATCHED", 1.0, "HISTORICAL", "历史商品", "Historico", "家居", "Hogar", "1件", "1 unidad", 1.0, 1.0, 1.0, "2026-01-01", "2026-01-02", 0, "", 1, "08", "", ""], ["ENT-3", "", "APR-3", "PENDING", "UNMATCHED", 0.0, "ARCHIVE_PENDING_MATCH", "", "", "", "", "", "", "", "", "2026-04-05", "2026-04-05", 1, "APR-3", 1, "07", "", ""]], header_row=7)
    _sheet(wb, "09_APRIL_MATCH_AUDIT", ["四月归档ID", "正式SKU"], [["APR-1", "100"]])
    _sheet(wb, "10_SOURCE_SCHEMA", ["日期", "文件名", "Sheet", "Raw 行数", "Raw 列数", "真实 Raw Schema", "来源作用", "数据状态", "备注"], [["2026-08-29", "run.xlsx", "Sheet1", 1, 1, "SKU", "事实", "OK", ""]])
    wb.save(path)


def test_schema_v1_and_transaction_rollback(tmp_path):
    db = tmp_path / "empty.db"
    migrate(db)
    with connect(db) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        assert {"products", "product_localizations", "observations", "price_history", "events", "runs", "reviews", "v_db_current_skus"} <= names
        with pytest.raises(RuntimeError):
            with transaction(conn) as tx:
                tx.execute("INSERT INTO products(sku, canonical_id, source_sheet, source_row_no, source_raw_json) VALUES ('x','x','T',1,'{}')")
                raise RuntimeError("rollback")
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0


def test_fixture_mirror_parity_and_master_unchanged(tmp_path):
    master = tmp_path / "Master.xlsx"
    db = tmp_path / "runtime" / "db" / "action_tracker.db"
    make_fixture(master)
    before = master.read_bytes()
    result = build_mirror(master, db, tmp_path / "reports")
    assert result["status"] == "PASS", result
    assert master.read_bytes() == before
    with connect(db, read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM v_db_current_skus").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM migration_source_issues").fetchone()[0] >= 3
    validation = validate_mirror(master, db)
    assert validation["status"] == "PASS", validation


def test_validation_rejects_current_set_mismatch(tmp_path):
    master = tmp_path / "Master.xlsx"
    db = tmp_path / "action.db"
    make_fixture(master)
    assert build_mirror(master, db, tmp_path / "reports")["status"] == "PASS"
    wb = load_workbook(master)
    ws = wb["02_SKU_ES_CURRENT"]
    ws["B2"] = "200"
    wb.save(master)
    result = validate_mirror(master, db)
    assert result["status"] == "FAIL"
    assert result["checks"]["es_db_current_equal"] is False
