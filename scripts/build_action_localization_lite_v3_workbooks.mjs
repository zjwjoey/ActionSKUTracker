import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = process.argv[2] || "F:/ActionSKUTracker/runtime/localization/lite_v3_150";

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
  return parseCsv((await fs.readFile(path.join(outputDir, name), "utf8")).replace(/^\uFEFF/, ""));
}

function styleSheet(sheet, widths) {
  const used = sheet.getUsedRange();
  used.format.font = { name: "Aptos", size: 10 };
  used.format.wrapText = true;
  used.format.verticalAlignment = "center";
  const end = String.fromCharCode(64 + widths.length);
  sheet.getRange(`A1:${end}1`).format = { fill: "#1F4E78", font: { name: "Aptos", size: 10, bold: true, color: "#FFFFFF" }, wrapText: true };
  sheet.freezePanes.freezeRows(1);
  widths.forEach((width, index) => { sheet.getRangeByIndexes(0, index, 1, 1).format.columnWidth = width; });
}

async function makeWorkbook(filename, sheetName, headers, rows, widths, preview) {
  const wb = Workbook.create();
  const sheet = wb.worksheets.add(sheetName);
  const matrix = [headers, ...rows.map((row) => headers.map((header) => row[header] ?? ""))];
  sheet.getRangeByIndexes(0, 0, matrix.length, headers.length).values = matrix;
  styleSheet(sheet, widths);
  wb.recalculate();
  const image = await wb.render({ sheetName, range: preview, scale: 1, format: "png" });
  await fs.writeFile(path.join(outputDir, `${filename.replace(/\.xlsx$/i, "")}.png`), new Uint8Array(await image.arrayBuffer()));
  const file = await SpreadsheetFile.exportXlsx(wb);
  await file.save(path.join(outputDir, filename));
}

const full = await readCsv("lite_translation_150_v3.csv");
const names = await readCsv("lite_name_review_150_v3.csv");
const owner = await readCsv("lite_owner_review_queue_v3.csv");

const fullHeaders = [
  "sku", "field_name", "priority", "source_es", "qwen_zh", "reviewed_zh_v1", "reviewed_zh_v2", "decision_v2",
  "reviewed_zh_v3", "decision_v3", "correction_type_v3", "review_note_v3", "cat1_es", "cat2_es", "spec_es", "description_es", "details_es",
];
const nameHeaders = [
  "sku", "name_es", "name_qwen_zh", "name_reviewed_zh_v2", "decision_v2", "name_reviewed_zh_v3", "decision_v3",
  "correction_type_v3", "review_note_v3", "cat1_es", "cat2_es", "spec_es",
];
const nameRows = names.map((row) => ({
  sku: row.sku, name_es: row.name_es, name_qwen_zh: row.name_qwen_zh, name_reviewed_zh_v2: row.name_reviewed_zh_v2,
  decision_v2: row.decision_v2, name_reviewed_zh_v3: row.name_reviewed_zh_v3, decision_v3: row.decision_v3,
  correction_type_v3: row.correction_type_v3, review_note_v3: row.review_note_v3,
  cat1_es: row.cat1_es, cat2_es: row.cat2_es, spec_es: row.spec_es,
}));

await makeWorkbook("lite_translation_150_v3.xlsx", "Lite V3 Full", fullHeaders, full,
  [12, 12, 10, 24, 24, 24, 24, 14, 24, 14, 28, 50, 20, 20, 25, 35, 40], "A1:Q21");
await makeWorkbook("lite_name_review_150_v3.xlsx", "Name P0 V3", nameHeaders, nameRows,
  [12, 30, 28, 28, 14, 28, 14, 28, 55, 20, 20, 25], "A1:L21");
await makeWorkbook("lite_owner_review_v3.xlsx", "Owner Review V3", fullHeaders, owner,
  [12, 12, 10, 24, 24, 24, 24, 14, 24, 14, 28, 50, 20, 20, 25, 35, 40], "A1:Q21");

console.log(JSON.stringify({ outputDir, fullRows: full.length, nameRows: nameRows.length, ownerRows: owner.length }));
