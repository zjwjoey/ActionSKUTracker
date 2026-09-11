import fs from "node:fs/promises";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const root = "F:/ActionSKUTracker";
const runId = "2026-08-11_050908";
const source = `${root}/runtime/snapshots/2026-08-11/${runId}/sku_delta.csv`;
const outputDir = `${root}/outputs/2026-08-11`;
const output = `${outputDir}/Action_ES_2026-08-11_在售SKU清单.xlsx`;

const text = (await fs.readFile(source, "utf8")).replace(/^\uFEFF/, "");
const [header, ...lines] = text.trim().split(/\r?\n/);
const indexes = Object.fromEntries(header.split(",").map((name, i) => [name, i]));
const rows = lines.map((line) => line.split(",")).filter((row) =>
  ["ACTIVE", "NEW", "REAPPEARED"].includes(row[indexes.status])
).map((row) => [
  row[indexes.sku], row[indexes.canonical_id], row[indexes.status], row[indexes.source_flag], row[indexes.event] || ""
]);

const wb = Workbook.create();
const sheet = wb.worksheets.add("今日在售 SKU");
sheet.showGridLines = false;
sheet.getRange("A1:E1").merge();
sheet.getRange("A1").values = [["Action 西班牙官网｜2026-08-11 在售 SKU 清单"]];
sheet.getRange("A2:E2").merge();
sheet.getRange("A2").values = [["来源：已完成的 dry-run snapshot；仅包含 ACTIVE、NEW、REAPPEARED。未写入正式总表。"]];
sheet.getRange("A3:B3").values = [["在售 SKU 总数", rows.length]];
sheet.getRange("D3:E3").values = [["Run ID", runId]];
sheet.getRange("A5:E5").values = [["SKU", "Canonical ID", "状态", "存在证据", "事件"]];
sheet.getRangeByIndexes(5, 0, rows.length, 5).values = rows;
sheet.getRange(`A5:E${rows.length + 5}`).format.borders = { preset: "inside", style: "thin", color: "#E2E8F0" };
sheet.getRange("A1:E1").format = { fill: "#0F4C5C", font: { bold: true, color: "#FFFFFF", size: 14 }, horizontalAlignment: "center" };
sheet.getRange("A2:E2").format = { fill: "#EAF4F4", font: { color: "#334155", italic: true }, wrapText: true };
sheet.getRange("A3:B3").format = { fill: "#DCEFE2", font: { bold: true, color: "#14532D" } };
sheet.getRange("D3:E3").format = { fill: "#E2E8F0", font: { bold: true, color: "#334155" } };
sheet.getRange("A5:E5").format = { fill: "#1F6F8B", font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center" };
sheet.getRange(`A6:B${rows.length + 5}`).format.numberFormat = "@";
sheet.getRange(`A5:E${rows.length + 5}`).format.wrapText = false;
sheet.getRange("A:A").format.columnWidth = 14;
sheet.getRange("B:B").format.columnWidth = 18;
sheet.getRange("C:C").format.columnWidth = 14;
sheet.getRange("D:D").format.columnWidth = 16;
sheet.getRange("E:E").format.columnWidth = 16;
sheet.getRange("A1:E1").format.rowHeight = 28;
sheet.getRange("A2:E2").format.rowHeight = 24;
sheet.freezePanes.freezeRows(5);
sheet.tables.add(`A5:E${rows.length + 5}`, true, "TodayOnSaleSkus");

await fs.mkdir(outputDir, { recursive: true });
const file = await SpreadsheetFile.exportXlsx(wb);
await file.save(output);
const verification = await wb.inspect({ kind: "table", range: "今日在售 SKU!A1:E10", include: "values,formulas", tableMaxRows: 10, tableMaxCols: 5 });
console.log(verification.ndjson);
console.log(`EXPORTED=${output}`);
