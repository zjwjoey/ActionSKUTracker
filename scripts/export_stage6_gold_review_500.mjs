import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = "F:/ActionSKUTracker";
const input = `${root}/runtime/stage6/20260913/gold_review_500/stage6_gold_review_500.jsonl`;
const outDir = `${root}/runtime/stage6/20260913/gold_review_500`;
const output = `${outDir}/stage6_gold_review_500.xlsx`;

const text = await fs.readFile(input, "utf8");
const rows = text.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
if (rows.length !== 500) throw new Error(`EXPECTED_500_ROWS:${rows.length}`);

const fieldRows = rows.flatMap((row) => row.field_reviews.map((item) => ({
  review_id: row.review_id,
  sku: row.sku,
  source_run_id: row.source_run_id,
  source_hash: row.source_hash,
  field: item.field,
  source_es: item.source_es,
  proposed_zh: item.proposed_zh,
  resolution_source: item.resolution_source,
  field_status: item.field_status,
  guard_reasons: item.guard_reasons.join(";"),
  owner_disposition: item.owner_disposition,
})));
if (fieldRows.length !== 3000) throw new Error(`EXPECTED_3000_FIELD_ROWS:${fieldRows.length}`);

const fieldHeaders = ["review_id", "sku", "source_run_id", "source_hash", "field", "source_es", "proposed_zh", "resolution_source", "field_status", "guard_reasons", "owner_disposition"];
const skuHeaders = ["review_id", "sku", "source_run_id", "source_hash", "name_es", "name_zh_candidate", "cat1_es", "cat1_zh_candidate", "cat2_es", "cat2_zh_candidate", "spec_es", "spec_zh_candidate", "description_es", "description_zh_candidate", "details_es", "details_zh_candidate", "candidate_status", "training_eligible"];
const fieldMatrix = [fieldHeaders, ...fieldRows.map((row) => fieldHeaders.map((key) => row[key] ?? ""))];
const skuMatrix = [skuHeaders, ...rows.map((row) => {
  const source = row.source;
  const proposed = row.proposed_zh;
  return [row.review_id, row.sku, row.source_run_id, row.source_hash,
    source.name, proposed.name ?? "", source.cat1, proposed.cat1 ?? "",
    source.cat2, proposed.cat2 ?? "", source.spec, proposed.spec ?? "",
    source.description, proposed.description ?? "", source.details, proposed.details ?? "",
    row.candidate_status, row.training_eligible ? "YES" : "NO"];
})];

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Summary");
const fieldSheet = workbook.worksheets.add("Field Review");
const skuSheet = workbook.worksheets.add("SKU Review");

const navy = "#1F4E78";
const lightBlue = "#D9EAF7";
const amber = "#FFF2CC";
const red = "#FCE4D6";
const font = { name: "Arial", size: 10, color: "#1F1F1F" };

summary.showGridLines = false;
summary.getRange("A1").values = [["Stage 6 中文 Gold 审核包"]];
summary.getRange("A1:F1").format = { font: { name: "Arial", size: 14, bold: true, color: navy } };
summary.getRange("A3:B10").values = [
  ["指标", "结果"],
  ["源候选 SKU", 500],
  ["字段审核行", 3000],
  ["规则/字典 Guard 通过", 1808],
  ["模型或人工审核缺口", 1170],
  ["规则 Guard 拒绝", 22],
  ["自动 Gold 晋级", 0],
  ["生产写入 / 训练运行", "0 / 0"],
];
summary.getRange("A3:B3").format = { fill: navy, font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" } };
summary.getRange("A4:B10").format.font = font;
summary.getRange("A3:B10").format.borders = { preset: "all", style: "thin", color: "#D9D9D9" };
summary.getRange("A12:F15").values = [[
  "审核说明", "本文件只提供规则安全候选和原始西语事实。所有记录均为 PENDING_OWNER_REVIEW。",
  "", "", "", "",
], [
  "安全边界", "未闭合字段不会用西语 fallback 或模型臆造值填充；本文件不具备生产 Apply 或训练授权。",
  "", "", "", "",
], [
  "下一步", "在冻结模型环境生成模型候选，再重新执行 Guard，最后由 Owner 决定 Gold disposition。",
  "", "", "", "",
], [
  "审查列", "Field Review 的 owner_disposition 可填写 ACCEPT_AS_IS / ACCEPT_WITH_MINOR_EDIT / REQUIRES_MAJOR_EDIT / AMBIGUOUS / REJECT。",
  "", "", "", "",
]];
summary.getRange("A12:A15").format.font = { name: "Arial", size: 10, bold: true, color: navy };
summary.getRange("B12:F15").format.font = font;
summary.getRange("B12:F15").format.wrapText = true;
summary.getRange("A1:F15").format.verticalAlignment = "center";
summary.getRange("A:A").format.columnWidth = 22;
summary.getRange("B:F").format.columnWidth = 28;

function styleDataSheet(sheet, matrix, tableName, widths, wrappedColumns) {
  sheet.showGridLines = false;
  const endCol = String.fromCharCode(64 + matrix[0].length);
  const endRow = matrix.length;
  const range = sheet.getRange(`A1:${endCol}${endRow}`);
  range.values = matrix;
  range.format.font = font;
  range.format.verticalAlignment = "center";
  range.format.borders = { preset: "all", style: "thin", color: "#E6E6E6" };
  sheet.getRange(`A1:${endCol}1`).format = { fill: navy, font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" }, wrapText: true, verticalAlignment: "center" };
  for (const [col, width] of widths) sheet.getRange(`${col}:${col}`).format.columnWidth = width;
  for (const col of wrappedColumns) sheet.getRange(`${col}2:${col}${endRow}`).format.wrapText = true;
  sheet.getRange(`A1:${endCol}${endRow}`).format.rowHeight = 30;
  sheet.getRange(`A2:${endCol}${endRow}`).format.rowHeight = 42;
  sheet.tables.add(`A1:${endCol}${endRow}`, true, tableName);
  sheet.freezePanes.freezeRows(1);
}

styleDataSheet(fieldSheet, fieldMatrix, "Stage6GoldFieldReview", [
  ["A", 66], ["B", 12], ["C", 22], ["D", 66], ["E", 14], ["F", 42], ["G", 34], ["H", 20], ["I", 28], ["J", 28], ["K", 24],
], ["F", "G", "J"]);
fieldSheet.getRange("K2:K3001").dataValidation = { rule: { type: "list", values: ["PENDING_OWNER_REVIEW", "ACCEPT_AS_IS", "ACCEPT_WITH_MINOR_EDIT", "REQUIRES_MAJOR_EDIT", "AMBIGUOUS", "REJECT"] } };
fieldSheet.getRange("K2:K3001").format.fill = amber;
fieldSheet.getRange("I2:I3001").conditionalFormats.add("containsText", { text: "REVIEW_REQUIRED", format: { fill: red, font: { color: "#9C0006", bold: true } } });

styleDataSheet(skuSheet, skuMatrix, "Stage6GoldSkuReview", [
  ["A", 66], ["B", 12], ["C", 22], ["D", 66], ["E", 34], ["F", 26], ["G", 24], ["H", 20], ["I", 24], ["J", 20], ["K", 34], ["L", 26], ["M", 42], ["N", 30], ["O", 54], ["P", 30], ["Q", 24], ["R", 16],
], ["E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P"]);

workbook.recalculate();
const inspect = await workbook.inspect({ kind: "table", sheetId: "Summary", range: "A1:F15", include: "values", tableMaxRows: 15, tableMaxCols: 6, maxChars: 4000 });
console.log(inspect.ndjson);
const preview = await workbook.render({ sheetName: "Summary", range: "A1:F15", scale: 1, format: "png" });
await fs.writeFile(`${outDir}/stage6_gold_review_summary.png`, new Uint8Array(await preview.arrayBuffer()));
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(output);
console.log(`WROTE ${output}`);
