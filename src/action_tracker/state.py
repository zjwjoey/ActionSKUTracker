"""文件型状态数据读写（规范 §12-§13、§35-§36）。

维护 runtime/state 下的机器状态文件：
    known_skus.csv        系统历史认识的全部商品身份（生命周期唯一状态源）
    offline_skus.csv      由 known_skus 派生的 OFFLINE 商品（非独立状态源）
    sku_identity_map.csv  canonical_id <-> SKU <-> URL
    translation_state.csv 中文翻译状态
    image_map.csv         本地图片映射

状态文件一律原子写：临时 CSV → 完整验证 → os.replace，失败原文件保持完整。
"""
from __future__ import annotations

import csv
import datetime as dt
import os
from pathlib import Path

KNOWN_HEADERS = ["canonical_id", "official_sku", "first_seen_date", "last_seen_date", "last_status",
                 "missing_count", "last_missing_date", "offline_date", "last_state_observation_date",
                 "ever_offline", "last_run_id", "updated_at"]
OFFLINE_HEADERS = ["canonical_id", "official_sku", "offline_date", "last_seen_date", "last_status"]
IDENTITY_HEADERS = ["canonical_id", "official_sku", "product_url"]
TRANS_HEADERS = ["canonical_id", "official_sku", "source_hash", "translation_status", "translated_at"]
IMAGE_HEADERS = ["canonical_id", "official_sku", "local_image_path", "source_image_url", "download_date", "image_hash"]


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: list[dict], headers: list[str]) -> None:
    """原子写 CSV：临时文件 → 读回验证行数 → os.replace。失败则原文件保持完整。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    with tmp.open("r", encoding="utf-8-sig", newline="") as f:
        n = sum(1 for _ in csv.reader(f)) - 1
    if n != len(rows):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"CSV 验证失败: {path.name} 期望 {len(rows)} 行实际 {n} 行")
    os.replace(tmp, path)


def stage_known_skus(state_dir: Path, known: dict[str, dict]) -> tuple[Path, Path]:
    """暂存新的 known_skus.csv（临时文件 + 验证），返回 (tmp, final)。不替换正式文件。

    供正式 daily-run 在 Master + 状态文件"一起先生成再统一提交"时使用。
    """
    path = state_dir / "known_skus.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    rows = sorted(known.values(), key=lambda r: r.get("official_sku", ""))
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=KNOWN_HEADERS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    with tmp.open("r", encoding="utf-8-sig", newline="") as f:
        n = sum(1 for _ in csv.reader(f)) - 1
    if n != len(rows):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"known_skus 暂存验证失败: 期望 {len(rows)} 行实际 {n} 行")
    return tmp, path


def commit_state_file(tmp: Path, final: Path) -> None:
    """原子替换正式状态文件（配合 stage_* 使用）。"""
    os.replace(tmp, final)


def derive_offline_skus(known: dict[str, dict]) -> list[dict]:
    """offline_skus 完全由 known_skus[last_status == OFFLINE] 派生，不是第二套状态源。"""
    rows = []
    for r in known.values():
        if r.get("last_status") == "OFFLINE":
            rows.append({
                "canonical_id": r.get("canonical_id", ""),
                "official_sku": r.get("official_sku", ""),
                "offline_date": r.get("offline_date", ""),
                "last_seen_date": r.get("last_seen_date", ""),
                "last_status": "OFFLINE",
            })
    return rows


def apply_state_transition(
    known: dict[str, dict],
    statuses: dict,
    run_date: str,
    run_id: str,
    offline_runs: int = 3,
) -> dict:
    """按当日 statuses 推进 known_skus 生命周期状态，返回 {"known": ..., "offline": [...]}。

    纯函数：只读 known/statuses，不写盘。规则（规范 §56"更新 known_skus"）：
      - NEW           创建记录；first_seen_date=当天（已有则保留）；last_status=ACTIVE；missing_count=0
      - ACTIVE         last_status=ACTIVE；last_seen_date=当天；missing_count=0
      - REAPPEARED     last_status=ACTIVE；last_seen_date=当天；missing_count=0（ever_offline 保留）
      - MISSING_FIRST  last_status=MISSING；missing_count 推进（同一天重复运行不 +1）；
                       last_missing_date=当天；不更新 last_seen_date
      - MISSING_CONTINUED  继续推进；达到 offline_runs 后由状态机输出 OFFLINE
      - OFFLINE        last_status=OFFLINE；offline_date 首次置当天；保留历史 first/last_seen；
                       missing_count 不再增长
      - ABSENT         不创建记录；已有记录保持不变

    missing_count 代表"连续缺失的有效观察日"，不是程序执行次数：记录里的
    last_state_observation_date == run_date 时，同日重复运行不得再次 +1。

    statuses: {sku: 对象}，需有 .status / .canonical_id / .missing_count。
    """
    new_known: dict[str, dict] = {sku: dict(rec) for sku, rec in known.items()}
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for sku, st in statuses.items():
        status = getattr(st, "status", None)
        if status == "ABSENT":
            continue  # 从未见过不建档；已知但 ABSENT 的异常状态保持不变
        rec = new_known.get(sku)
        if rec is None:
            rec = {
                "canonical_id": getattr(st, "canonical_id", ""), "official_sku": sku,
                "first_seen_date": "", "last_seen_date": "", "last_status": "",
                "missing_count": "0", "last_missing_date": "", "offline_date": "",
                "last_state_observation_date": "", "ever_offline": "false",
                "last_run_id": "", "updated_at": "",
            }
            new_known[sku] = rec
        prev_mc = int(rec.get("missing_count") or 0)
        prev_obs = rec.get("last_state_observation_date") or ""
        rec["last_run_id"] = run_id
        rec["updated_at"] = now
        if status == "NEW":
            if not rec.get("first_seen_date"):
                rec["first_seen_date"] = run_date
            rec["last_seen_date"] = run_date
            rec["last_status"] = "ACTIVE"
            rec["missing_count"] = "0"
            rec["last_missing_date"] = ""
            rec["last_state_observation_date"] = run_date
        elif status in ("ACTIVE", "REAPPEARED"):
            rec["last_status"] = "ACTIVE"
            rec["last_seen_date"] = run_date
            rec["missing_count"] = "0"
            rec["last_missing_date"] = ""
            rec["last_state_observation_date"] = run_date
        elif status in ("MISSING_FIRST", "MISSING_CONTINUED"):
            new_mc = int(getattr(st, "missing_count", 0) or 0)
            if prev_obs == run_date:
                new_mc = prev_mc  # 同一天重复正式运行：不得再次 +1
            rec["missing_count"] = str(new_mc)
            rec["last_status"] = "MISSING"
            rec["last_missing_date"] = run_date
            rec["last_state_observation_date"] = run_date
            # 缺失当天不更新 last_seen_date
        elif status == "OFFLINE":
            # 转为 OFFLINE 的当天：记录完整的连续缺失天数（classify 已给出 new_missing=3）
            # 已是 OFFLINE 的后续观察日：计数冻结，不再增长（商品已确认下架，缺失天数无新信息）
            if rec.get("last_status") == "OFFLINE":
                rec["missing_count"] = str(prev_mc)
            else:
                rec["missing_count"] = str(getattr(st, "missing_count", prev_mc) or prev_mc)
            rec["last_status"] = "OFFLINE"
            rec["offline_date"] = rec.get("offline_date") or run_date
            rec["last_missing_date"] = run_date
            rec["last_state_observation_date"] = run_date
            rec["ever_offline"] = "true"
    return {"known": new_known, "offline": derive_offline_skus(new_known)}


# ---- known_skus ----

def load_known_skus(state_dir: Path) -> dict[str, dict]:
    rows = _read_csv(state_dir / "known_skus.csv")
    out = {}
    for r in rows:
        sku = (r.get("official_sku") or "").strip()
        if sku:
            r.setdefault("missing_count", "0")
            r.setdefault("ever_offline", "false")
            out[sku] = r
    return out


def save_known_skus(state_dir: Path, known: dict[str, dict]) -> None:
    rows = sorted(known.values(), key=lambda r: r.get("official_sku", ""))
    _write_csv(state_dir / "known_skus.csv", rows, KNOWN_HEADERS)


# ---- offline_skus ----

def load_offline_skus(state_dir: Path) -> dict[str, dict]:
    rows = _read_csv(state_dir / "offline_skus.csv")
    return {r.get("official_sku"): r for r in rows if r.get("official_sku")}


def save_offline_skus(state_dir: Path, offline) -> None:
    """原子写 offline_skus.csv。接受 dict{sku: row} 或 list[dict]（derive_offline_skus 输出）。"""
    if isinstance(offline, dict):
        rows = list(offline.values())
    else:
        rows = list(offline)
    _write_csv(state_dir / "offline_skus.csv", sorted(rows, key=lambda r: r.get("official_sku", "")), OFFLINE_HEADERS)


# ---- sku_identity_map ----

def load_identity_map(state_dir: Path) -> dict[str, dict]:
    rows = _read_csv(state_dir / "sku_identity_map.csv")
    return {r.get("official_sku"): r for r in rows if r.get("official_sku")}


def save_identity_map(state_dir: Path, mapping: dict[str, dict]) -> None:
    _write_csv(state_dir / "sku_identity_map.csv", sorted(mapping.values(), key=lambda r: r.get("official_sku", "")), IDENTITY_HEADERS)


# ---- translation_state ----

def load_translation_state(state_dir: Path) -> dict[str, dict]:
    rows = _read_csv(state_dir / "translation_state.csv")
    return {r.get("canonical_id"): r for r in rows if r.get("canonical_id")}


def save_translation_state(state_dir: Path, trans: dict[str, dict]) -> None:
    _write_csv(state_dir / "translation_state.csv", sorted(trans.values(), key=lambda r: r.get("canonical_id", "")), TRANS_HEADERS)


# ---- image_map ----

def load_image_map(state_dir: Path) -> dict[str, dict]:
    rows = _read_csv(state_dir / "image_map.csv")
    return {r.get("canonical_id"): r for r in rows if r.get("canonical_id")}


def save_image_map(state_dir: Path, imap: dict[str, dict]) -> None:
    _write_csv(state_dir / "image_map.csv", sorted(imap.values(), key=lambda r: r.get("canonical_id", "")), IMAGE_HEADERS)
