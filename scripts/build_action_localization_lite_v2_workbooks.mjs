import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = process.argv[2] || "F:/ActionSKUTracker/runtime/localization/lite_v2_150";

function parseCsv(text) {
  const rows = [];
  let row = [], value = "", quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { value += '"'; i += 1; }
      else if (ch === '"') quoted = false;
      else value += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") { row.push(value); value = ""; }
    else if (ch === "\n") { row.push(value.replace(/\r$/, "")); rows.push(row); row = []; value = ""; }
    else value += ch;
  }
  if (value.length || row.length) { row.push(value.replace(/\r$/, "")); rows.push(row); }
  const headers = rows.shift() || [];
  return rows.filter((r) => r.length || r.some(Boolean)).map((r) => Object.fromEntries(headers.map((h, i) => [h, r[i] ?? ""])));
}

async function readCsv(name) {
  const text = await fs.readFile(path.join(outputDir, name), "utf8");
  return parseCsv(text.replace(/^\uFEFF/, ""));
}

function styleSheet(sheet, columnWidths) {
  const used = sheet.getUsedRange();
  used.format.font = { name: "Aptos", size: 10 };
  used.format.wrapText = true;
  used.format.verticalAlignment = "center";
  sheet.getRange(`A1:${String.fromCharCode(64 + Math.min(columnWidths.length, 26))}1`).format = {
    fill: "#1F4E78", font: { name: "Aptos", size: 10, bold: true, color: "#FFFFFF" }, wrapText: true,
  };
  sheet.freezePanes.freezeRows(1);
  columnWidths.forEach((width, index) => { sheet.getRangeByIndexes(0, index, 1, 1).format.columnWidth = width; });
}

async function makeWorkbook(filename, sheetName, headers, rows, widths, previewRange) {
  const wb = Workbook.create();
  const sheet = wb.worksheets.add(sheetName);
  const matrix = [headers, ...rows.map((row) => headers.map((header) => row[header] ?? ""))];
  sheet.getRangeByIndexes(0, 0, matrix.length, headers.length).values = matrix;
  styleSheet(sheet, widths);
  wb.recalculate();
  const preview = await wb.render({ sheetName, range: previewRange, scale: 1, format: "png" });
  await fs.writeFile(path.join(outputDir, `${filename.replace(/\.xlsx$/i, "")}.png`), new Uint8Array(await preview.arrayBuffer()));
  const file = await SpreadsheetFile.exportXlsx(wb);
  await file.save(path.join(outputDir, filename));
}

const full = await readCsv("lite_translation_150_v2.csv");
const names = await readCsv("lite_name_review_150_v2.csv");
const owner = await readCsv("lite_owner_review_queue_v2.csv");

const fullHeaders = [
  "sku", "field_name", "priority", "source_es", "qwen_zh", "reviewed_zh_v1", "reviewed_zh_v2",
  "decision_v1", "decision_v2", "correction_type", "review_note", "cat1_es", "cat2_es", "spec_es", "description_es", "details_es",
];
const nameHeaders = [
  "sku", "name_es", "name_qwen_zh", "name_reviewed_zh_v1", "name_reviewed_zh_v2", "decision_v1", "decision_v2",
  "correction_type", "review_note", "cat1_es", "cat2_es", "spec_es",
];

await makeWorkbook("lite_translation_150_v2.xlsx", "Lite V2 Full", fullHeaders, full,
  [12, 12, 10, 24, 24, 24, 24, 14, 14, 20, 45, 20, 20, 25, 35, 40], "A1:P21");
await makeWorkbook("lite_name_review_150_v2.xlsx", "Name P0 Review", nameHeaders, names,
  [12, 28, 28, 28, 28, 14, 14, 24, 50, 20, 20, 25], "A1:L21");
await makeWorkbook("lite_owner_review_v2.xlsx", "Owner Review", fullHeaders, owner,
  [12, 12, 10, 24, 24, 24, 24, 14, 14, 20, 45, 20, 20, 25, 35, 40], "A1:P21");

const decisions = Object.fromEntries([...new Set(full.map((row) => row.decision_v2))].map((decision) => [decision, full.filter((row) => row.decision_v2 === decision).length]));
const nameDecisions = Object.fromEntries([...new Set(names.map((row) => row.decision_v2))].map((decision) => [decision, names.filter((row) => row.decision_v2 === decision).length]));
await fs.writeFile(path.join(outputDir, "lite_v2_review_report.md"), [
  "# Action Localization Lite V2 Review",
  "",
  "- Sample: 150 unchanged SKUs / 900 translation units",
  "- Qwen calls: 0 (original qwen_zh reused)",
  "- Master writes: 0",
  `- P0 name decisions: ${JSON.stringify(nameDecisions)}`,
  `- All-field decisions: ${JSON.stringify(decisions)}`,
  `- Owner queue: ${owner.length} rows (P0=${owner.filter((row) => row.priority === "P0").length}, P1=${owner.filter((row) => row.priority === "P1").length}, P2=${owner.filter((row) => row.priority === "P2").length})`,
  "- Status: READY_FOR_OWNER_REVIEW; no Master/production apply",
  "",
  "## Outputs",
  "",
  "- `lite_name_review_150_v2.xlsx`: one row per SKU, P0 name review columns",
  "- `lite_translation_150_v2.xlsx`: all six fields with V1/V2 decisions",
  "- `lite_owner_review_v2.xlsx`: only REVIEW_REQUIRED rows",
].join("\n"), "utf8");

console.log(JSON.stringify({ outputDir, fullRows: full.length, nameRows: names.length, ownerRows: owner.length }));
