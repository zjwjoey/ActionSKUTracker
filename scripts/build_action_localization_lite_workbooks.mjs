import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = process.argv[2] || "F:/ActionSKUTracker/runtime/localization/lite_v1_150";
const rows = JSON.parse(await fs.readFile(path.join(outputDir, "lite_review_rows.json"), "utf8"));
const fields = ["name", "cat1", "cat2", "spec", "description", "details"];
const bySku = new Map();
for (const row of rows) {
  if (!bySku.has(row.sku)) bySku.set(row.sku, { sku: row.sku });
  const item = bySku.get(row.sku);
  item[`${row.field_name}_es`] = row.source_es || "";
  item[`${row.field_name}_qwen_zh`] = row.qwen_zh || "";
  item[`${row.field_name}_reviewed_zh`] = row.reviewed_zh || "";
  item[`${row.field_name}_decision`] = row.review_decision || "";
}

const columns = ["sku"];
for (const field of fields) columns.push(`${field}_es`, `${field}_qwen_zh`, `${field}_reviewed_zh`, `${field}_decision`);
const matrix = [columns, ...[...bySku.values()].map((row) => columns.map((column) => row[column] ?? ""))];

function styleSheet(sheet, headerEnd) {
  const used = sheet.getUsedRange();
  used.format.font = { name: "Aptos", size: 10 };
  sheet.getRange(`A1:${headerEnd}1`).format = { fill: "#1F4E78", font: { name: "Aptos", size: 10, bold: true, color: "#FFFFFF" }, wrapText: true };
  used.format.wrapText = true;
  sheet.freezePanes.freezeRows(1);
  sheet.getRange("A:A").format.columnWidth = 12;
  sheet.getRange("B:Z").format.columnWidth = 20;
  sheet.getRange("A1:Z200").format.verticalAlignment = "center";
}

async function makeWorkbook(rowsForWorkbook, filename, title) {
  const wb = Workbook.create();
  const sheet = wb.worksheets.add(title);
  const chosen = [columns, ...rowsForWorkbook.map((row) => columns.map((column) => row[column] ?? ""))];
  sheet.getRangeByIndexes(0, 0, chosen.length, columns.length).values = chosen;
  styleSheet(sheet, "Z");
  wb.recalculate();
  const preview = await wb.render({ sheetName: title, range: "A1:Y20", scale: 1, format: "png" });
  await fs.writeFile(path.join(outputDir, `${filename.replace(/\.xlsx$/i, "")}.png`), new Uint8Array(await preview.arrayBuffer()));
  const out = await SpreadsheetFile.exportXlsx(wb);
  await out.save(path.join(outputDir, filename));
}

const all = [...bySku.values()];
const queueSkus = new Set(rows.filter((row) => row.review_decision === "REVIEW_REQUIRED").map((row) => row.sku));
const correctedSkus = new Set(rows.filter((row) => row.review_decision === "CORRECTED").map((row) => row.sku));
const keepSkus = new Set(rows.filter((row) => row.review_decision === "KEEP").map((row) => row.sku));
const sampledCorrected = [...correctedSkus].slice(0, 10);
const sampledKeep = [...keepSkus].slice(0, 10);
const ownerSkus = new Set([...queueSkus, ...sampledCorrected, ...sampledKeep]);
const owner = all.filter((row) => ownerSkus.has(row.sku));
const gold = all.filter((row) => !queueSkus.has(row.sku));
await makeWorkbook(all, "lite_translation_150.xlsx", "Lite Translation");
await makeWorkbook(owner, "lite_owner_review.xlsx", "Owner Review");
await makeWorkbook(gold, "gold_candidate_150.xlsx", "Gold Candidate");
