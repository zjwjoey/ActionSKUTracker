import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = "F:/ActionSKUTracker";
const dir = `${root}/runtime/stage6/20260913/gold_review_500_final`;
const input = `${dir}/stage6_gold_review_500_final.jsonl`;
const audit = JSON.parse(await fs.readFile(`${dir}/manifest.json`, "utf8"));
const rows = (await fs.readFile(input, "utf8")).split(/\r?\n/).filter(Boolean).map(JSON.parse);
if (rows.length !== 500) throw new Error(`EXPECTED_500_ROWS:${rows.length}`);

const fieldRows = rows.flatMap(row => row.field_reviews.map(item => ({
  review_id: row.review_id, sku: row.sku, source_run_id: row.source_run_id,
  source_hash: row.source_hash, field: item.field, source_es: item.source_es,
  proposed_zh: item.proposed_zh, resolution_source: item.resolution_source,
  field_status: item.field_status, guard_reasons: item.guard_reasons.join(";"),
  owner_disposition: item.owner_disposition,
})));
if (fieldRows.length !== 3000) throw new Error(`EXPECTED_3000_FIELD_ROWS:${fieldRows.length}`);

const fieldHeaders = ["review_id","sku","source_run_id","source_hash","field","source_es","proposed_zh","resolution_source","field_status","guard_reasons","owner_disposition"];
const skuHeaders = ["review_id","sku","source_run_id","source_hash","partition","name_es","name_zh_candidate","cat1_es","cat1_zh_candidate","cat2_es","cat2_zh_candidate","spec_es","spec_zh_candidate","description_es","description_zh_candidate","details_es","details_zh_candidate","candidate_status","training_eligible"];
const fieldMatrix = [fieldHeaders, ...fieldRows.map(row => fieldHeaders.map(k => row[k] ?? ""))];
const skuMatrix = [skuHeaders, ...rows.map(row => {
  const s = row.source, p = row.proposed_zh;
  return [row.review_id,row.sku,row.source_run_id,row.source_hash,row.partition,s.name,p.name ?? "",s.cat1,p.cat1 ?? "",s.cat2,p.cat2 ?? "",s.spec,p.spec ?? "",s.description,p.description ?? "",s.details,p.details ?? "",row.candidate_status,row.training_eligible ? "YES" : "NO"];
})];

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Summary");
const fieldSheet = workbook.worksheets.add("Field Review");
const skuSheet = workbook.worksheets.add("SKU Review");
const navy = "#1F4E78", amber = "#FFF2CC", red = "#FCE4D6";
const font = { name: "Arial", size: 10, color: "#1F1F1F" };
summary.showGridLines = false;
summary.getRange("A1:F1").values = [["Stage 6 最终中文 Gold 审核包", "", "", "", "", ""]];
summary.getRange("A1:F1").format = { font: { name: "Arial", size: 14, bold: true, color: navy } };
summary.getRange("A3:B15").values = [
  ["指标", "结果"], ["源候选 SKU", audit.source_count], ["字段审核行", audit.field_review_count],
  ["规则/字典 Guard 通过", audit.rule_closed_fields], ["模型 Guard 通过", audit.model_guard_pass_fields],
  ["待人工审核字段", audit.remaining_manual_review_fields], ["Blind Holdout 行", audit.blind_holdout_rows],
  ["Blind Holdout 产品族", audit.blind_holdout_families], ["训练候选行", audit.training_pool_rows],
  ["自动 Gold 晋级", 0], ["生产写入 / 训练运行", "0 / 0"],
  ["源哈希重算", audit.source_hash_recompute], ["最终状态", audit.status],
];
summary.getRange("A3:B3").format = { fill: navy, font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" } };
summary.getRange("A3:B15").format.borders = { preset: "all", style: "thin", color: "#D9D9D9" };
summary.getRange("A3:B15").format.font = font;
summary.getRange("A17:F20").values = [
  ["审核说明", "模型、规则和 Guard 已完成；所有候选仍为 PENDING_OWNER_REVIEW。", "", "", "", ""],
  ["人工操作", "只在 Field Review 的 owner_disposition 列填写审核结果；必要时先修改 proposed_zh。", "", "", "", ""],
  ["安全边界", "未写入 Master、Dictionary、SQLite；未执行训练或 Production Apply。", "", "", "", ""],
  ["审核值", "ACCEPT_AS_IS / ACCEPT_WITH_MINOR_EDIT / REQUIRES_MAJOR_EDIT / AMBIGUOUS / REJECT", "", "", "", ""],
];
summary.getRange("A17:A20").format.font = { name: "Arial", size: 10, bold: true, color: navy };
summary.getRange("B17:F20").format = { font, wrapText: true };
summary.getRange("A:A").format.columnWidth = 26; summary.getRange("B:F").format.columnWidth = 30;

function styleSheet(sheet, matrix, tableName, widths, wrapped) {
  sheet.showGridLines = false;
  const endCol = String.fromCharCode(64 + matrix[0].length), endRow = matrix.length;
  sheet.getRange(`A1:${endCol}${endRow}`).values = matrix;
  sheet.getRange(`A1:${endCol}${endRow}`).format = { font, verticalAlignment: "center", borders: { preset: "all", style: "thin", color: "#E6E6E6" } };
  sheet.getRange(`A1:${endCol}1`).format = { fill: navy, font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" }, wrapText: true, verticalAlignment: "center" };
  for (const [col, width] of widths) sheet.getRange(`${col}:${col}`).format.columnWidth = width;
  for (const col of wrapped) sheet.getRange(`${col}2:${col}${endRow}`).format.wrapText = true;
  sheet.getRange(`A1:${endCol}${endRow}`).format.rowHeight = 34;
  sheet.getRange(`A2:${endCol}${endRow}`).format.rowHeight = 46;
  sheet.tables.add(`A1:${endCol}${endRow}`, true, tableName);
  sheet.freezePanes.freezeRows(1);
}
styleSheet(fieldSheet, fieldMatrix, "Stage6FinalGoldFieldReview", [["A",66],["B",12],["C",22],["D",66],["E",14],["F",42],["G",34],["H",22],["I",30],["J",30],["K",26]], ["F","G","J"]);
fieldSheet.getRange("K2:K3001").dataValidation = { rule: { type: "list", values: ["PENDING_OWNER_REVIEW","ACCEPT_AS_IS","ACCEPT_WITH_MINOR_EDIT","REQUIRES_MAJOR_EDIT","AMBIGUOUS","REJECT"] } };
fieldSheet.getRange("K2:K3001").format.fill = amber;
fieldSheet.getRange("I2:I3001").conditionalFormats.add("containsText", { text: "REVIEW_REQUIRED", format: { fill: red, font: { color: "#9C0006", bold: true } } });
styleSheet(skuSheet, skuMatrix, "Stage6FinalGoldSkuReview", [["A",66],["B",12],["C",22],["D",66],["E",18],["F",34],["G",26],["H",24],["I",20],["J",24],["K",20],["L",34],["M",26],["N",42],["O",30],["P",54],["Q",30],["R",24],["S",16]], ["F","G","H","I","J","K","L","M","N","O","P","Q"]);
workbook.recalculate();
const preview = await workbook.render({ sheetName: "Summary", range: "A1:F20", scale: 1, format: "png" });
await fs.writeFile(`${dir}/stage6_final_review_summary.png`, new Uint8Array(await preview.arrayBuffer()));
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(`${dir}/stage6_gold_review_500_final.xlsx`);
console.log(`WROTE ${dir}/stage6_gold_review_500_final.xlsx`);
