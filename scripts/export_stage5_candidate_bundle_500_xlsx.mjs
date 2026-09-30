import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const depRoot = "C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules";
const { Workbook, SpreadsheetFile } = await import(pathToFileURL(path.join(depRoot, "@oai/artifact-tool/dist/artifact_tool.mjs")).href);

const root = "F:/ActionSKUTracker";
const oldPath = path.join(root, "runtime/training/qwen3_8b/20260910/qwen_incremental_approved_500.jsonl");
const freshPath = path.join(root, "runtime/training/qwen3_8b/20260913/qwen_incremental_stage5_fresh12_v1_candidate_12.jsonl");
const outDir = path.join(root, "outputs/stage5_candidate_bundle_500_20260913");
const outPath = path.join(outDir, "Action_Stage5_候选数据_500条.xlsx");

const quarantined = new Set(["2556104", "3206734", "3215546", "3219090", "3221992", "3222807", "3224146", "3224907", "3225230", "3225667"]);
const parse = async (p) => (await fs.readFile(p, "utf8")).split(/\r?\n/).filter(Boolean).map((x) => JSON.parse(x));
const text = (x) => x == null ? "" : String(x);
const rows = [...await parse(oldPath), ...await parse(freshPath)];
if (rows.length !== 500) throw new Error(`EXPECTED_500_ROWS:${rows.length}`);

function unpack(record) {
  const msgs = record.messages || [];
  const user = JSON.parse((msgs.find((m) => m.role === "user") || {}).content || "{}");
  const assistant = JSON.parse((msgs.find((m) => m.role === "assistant") || {}).content || "{}");
  const meta = record.metadata || {};
  const sku = text(meta.sku);
  return [
    sku, text(user.name), text(assistant.name), text(user.cat1), text(assistant.cat1),
    text(user.cat2), text(assistant.cat2), text(user.spec), text(assistant.spec),
    text(user.description), text(assistant.description), text(user.details), text(assistant.details),
    text(meta.label_tier || meta.gold_tier || ""),
    text(meta.review_status || meta.candidate_status || ""),
    quarantined.has(sku) ? "隔离：Owner Major/Ambiguous" : "未隔离",
    text(meta.source_hash),
  ];
}
const matrix = rows.map(unpack);
const qRows = matrix.filter((r) => r[15] !== "未隔离").map((r) => [r[0], r[1], r[2], r[14], "保持隔离，不得进入训练"]);

const wb = Workbook.create();
const data = wb.worksheets.add("候选数据");
const quarantine = wb.worksheets.add("隔离清单");
const info = wb.worksheets.add("说明");
const headers = ["SKU", "西语品名", "中文品名", "西语一级类目", "中文一级类目", "西语二级类目", "中文二级类目", "西语规格", "中文规格", "西语描述", "中文描述", "西语详情", "中文详情", "来源层级", "审核状态", "隔离状态", "source_hash"];
data.getRangeByIndexes(0, 0, 1, headers.length).values = [headers];
data.getRangeByIndexes(1, 0, matrix.length, headers.length).values = matrix;
data.freezePanes.freezeRows(1);
data.showGridLines = false;
data.getRange("A1:Q1").format = { fill: "#1F4E78", font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true };
data.getRange(`A2:Q${matrix.length + 1}`).format = { font: { name: "Arial", size: 10, color: "#1F2937" }, verticalAlignment: "center", wrapText: true };
data.getRange(`A1:Q${matrix.length + 1}`).format.borders = { preset: "insideHorizontal", style: "thin", color: "#D9E2F3" };
data.getRange(`A1:Q${matrix.length + 1}`).format.columnWidth = 18;
for (const col of ["B", "C", "D", "E", "F", "G", "H", "I"]) data.getRange(`${col}:${col}`).format.columnWidth = 18;
for (const col of ["J", "K", "L", "M"]) data.getRange(`${col}:${col}`).format.columnWidth = 42;
data.getRange("Q:Q").format.columnWidth = 32;
data.getRange(`A2:A${matrix.length + 1}`).format.columnWidth = 12;
data.getRange(`A1:Q${matrix.length + 1}`).format.autofitRows();
data.tables.add(`A1:Q${matrix.length + 1}`, true, "Stage5Candidate500").showFilterButton = true;

quarantine.getRange("A1:E1").values = [["SKU", "西语品名", "中文品名", "隔离状态", "处理要求"]];
quarantine.getRangeByIndexes(1, 0, qRows.length, 5).values = qRows;
quarantine.freezePanes.freezeRows(1);
quarantine.showGridLines = false;
quarantine.getRange("A1:E1").format = { fill: "#9C0006", font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center" };
quarantine.getRange(`A2:E${qRows.length + 1}`).format = { font: { name: "Arial", size: 10, color: "#1F2937" }, verticalAlignment: "center", wrapText: true };
quarantine.getRange(`A1:E${qRows.length + 1}`).format.columnWidth = 24;
quarantine.tables.add(`A1:E${qRows.length + 1}`, true, "Stage5Quarantine").showFilterButton = true;

info.getRange("A1:B8").values = [
  ["项目", "Stage 5 候选数据包"],
  ["数据行数", 500],
  ["唯一 SKU 数", 500],
  ["组成", "原已审核候选 488 条 + 新增候选 12 条"],
  ["隔离行数", qRows.length],
  ["当前状态", "CANDIDATE_BUNDLE_NOT_TRAINING_READY"],
  ["训练限制", "隔离行不得训练；新增 12 条需完成审核与 source_hash 核验"],
  ["评估限制", "本表不得直接作为独立评估集，需另建未见过的 holdout"],
];
info.showGridLines = false;
info.getRange("A1:B1").format = { fill: "#1F4E78", font: { name: "Arial", size: 11, bold: true, color: "#FFFFFF" } };
info.getRange("A1:B8").format = { font: { name: "Arial", size: 10, color: "#1F2937" }, verticalAlignment: "center", wrapText: true };
info.getRange("A1:A8").format.font = { name: "Arial", size: 10, bold: true, color: "#1F2937" };
info.getRange("A:A").format.columnWidth = 18;
info.getRange("B:B").format.columnWidth = 80;

wb.recalculate();
await fs.mkdir(outDir, { recursive: true });
const preview = await wb.render({ sheetName: "候选数据", range: "A1:Q18", scale: 1, format: "png" });
await fs.writeFile(path.join(outDir, "preview.png"), new Uint8Array(await preview.arrayBuffer()));
const blob = await SpreadsheetFile.exportXlsx(wb);
await blob.save(outPath);
console.log(JSON.stringify({ outPath, rows: matrix.length, unique_skus: new Set(matrix.map((r) => r[0])).size, quarantine_rows: qRows.length }));
