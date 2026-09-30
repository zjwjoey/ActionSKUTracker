import fs from "node:fs/promises";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const root = "F:/ActionSKUTracker";
const input = `${root}/runtime/training/qwen3_8b/20260911/stage4_training_experiment_200_20260912.jsonl`;
const manifestPath = `${root}/runtime/training/qwen3_8b/20260911/stage4_training_experiment_200_20260912.json`;
const outputDir = `${root}/outputs/qwen-stage4-training-200-20260912`;
const output = `${outputDir}/stage4_training_experiment_200_20260912.xlsx`;
const previewPath = `${outputDir}/stage4_training_experiment_200_preview.png`;

const lines = (await fs.readFile(input, "utf8")).trim().split(/\r?\n/).filter(Boolean);
const rows = lines.map((line) => JSON.parse(line));
const parseMessage = (row, role) => {
  const msg = row.messages.find((m) => m.role === role && typeof m.content === "string" && m.content.trim().startsWith("{"));
  return msg ? JSON.parse(msg.content) : {};
};
const val = (obj, key) => obj[key] ?? "";

const headers = [
  "序号", "SKU", "数据层级", "训练用途", "Owner决策", "金标状态", "历史重叠", "正式发布资格",
  "西语一级类目", "西语二级类目", "西语品名", "西语规格", "西语描述", "西语产品详情",
  "中文一级类目", "中文二级类目", "中文品名", "中文规格", "中文描述", "中文产品详情",
  "source_hash", "审核理由", "训练说明"
];

const data = rows.map((row, i) => {
  const src = parseMessage(row, "user");
  const tgt = parseMessage(row, "assistant");
  const m = row.metadata ?? {};
  return [
    i + 1,
    val(m, "sku"),
    val(m, "training_package_tier"),
    val(m, "training_use"),
    val(m, "owner_decision"),
    val(m, "gold_status"),
    m.historical_overlap === true ? "是" : (m.historical_overlap === false ? "否" : ""),
    m.formal_release_eligible === true ? "是" : "否",
    val(src, "cat1"), val(src, "cat2"), val(src, "name"), val(src, "spec"), val(src, "description"), val(src, "details"),
    val(tgt, "cat1"), val(tgt, "cat2"), val(tgt, "name"), val(tgt, "spec"), val(tgt, "description"), val(tgt, "details"),
    val(m, "source_hash"), val(m, "ai_reason"), val(m, "training_note")
  ];
});

const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
const wb = Workbook.create();
const sheet = wb.worksheets.add("训练数据200");
sheet.showGridLines = false;
sheet.getRange("A1:W1").merge();
sheet.getRange("A1").values = [["Qwen Stage 4｜200 条增量修复训练数据（实验包）"]];
sheet.getRange("A2:W2").merge();
sheet.getRange("A2").values = [["用途：增量修复实验训练；全部标记 EXPERIMENT_ONLY，不具备正式发布或独立测试资格，不写入 Master。"]];
sheet.getRangeByIndexes(2, 0, 1, headers.length).values = [headers];
sheet.getRangeByIndexes(3, 0, data.length, headers.length).values = data;

sheet.getRange("A1:W1").format = { fill: "#17365D", font: { name: "Arial", bold: true, color: "#FFFFFF", size: 14 }, horizontalAlignment: "left", verticalAlignment: "center" };
sheet.getRange("A2:W2").format = { fill: "#EAF2F8", font: { name: "Arial", italic: true, color: "#334155", size: 10 }, wrapText: true, verticalAlignment: "center" };
sheet.getRange("A3:W3").format = { fill: "#1F4E78", font: { name: "Arial", bold: true, color: "#FFFFFF", size: 10 }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true };
sheet.getRange(`A4:W${data.length + 3}`).format = { font: { name: "Arial", size: 10, color: "#1F2937" }, verticalAlignment: "top", wrapText: true };
sheet.getRange(`A4:A${data.length + 3}`).format.horizontalAlignment = "center";
sheet.getRange(`B4:B${data.length + 3}`).format.horizontalAlignment = "center";
sheet.getRange(`D4:H${data.length + 3}`).format.horizontalAlignment = "center";
sheet.getRange(`A3:W${data.length + 3}`).format.borders = { preset: "inside", style: "thin", color: "#D9E2F3" };
sheet.getRange("A1:W1").format.rowHeight = 28;
sheet.getRange("A2:W2").format.rowHeight = 24;
sheet.getRange("A3:W3").format.rowHeight = 34;
sheet.getRange(`A4:W${data.length + 3}`).format.rowHeight = 58;

const widths = [6, 12, 24, 16, 18, 38, 10, 14, 18, 22, 28, 18, 44, 56, 18, 22, 28, 18, 44, 56, 66, 54, 54];
for (let i = 0; i < widths.length; i++) {
  const col = String.fromCharCode(65 + i);
  sheet.getRange(`${col}:${col}`).format.columnWidth = widths[i];
}
sheet.freezePanes.freezeRows(3);
sheet.tables.add(`A3:W${data.length + 3}`, true, "Stage4TrainingExperiment200");

const note = wb.worksheets.add("说明");
note.showGridLines = false;
note.getRange("A1:D1").merge();
note.getRange("A1").values = [["文件说明"]];
const noteRows = [
  ["项目", "ActionSKUTracker｜Qwen Stage 4"],
  ["数据用途", "200 条增量修复训练实验数据，不是正式发布包"],
  ["数据构成", `Owner 确认修复 ${manifest.owner_confirmed ?? 177} 条；Model Reviewed Silver ${manifest.silver ?? 23} 条`],
  ["训练限制", "training_use=EXPERIMENT_ONLY；formal_release_eligible=false；不得用于正式 release test / Core / Hard / Temporal / OOD 测试"],
  ["安全限制", "不写入 Master、State、Dictionary；不覆盖旧 adapter；不设置 FULL_STAGE4_RELEASE"],
  ["来源 JSONL", input],
  ["来源 manifest", manifestPath],
  ["生成日期", "2026-09-12"],
];
note.getRangeByIndexes(2, 0, noteRows.length, 2).values = noteRows;
note.getRange("A1:D1").format = { fill: "#17365D", font: { name: "Arial", bold: true, color: "#FFFFFF", size: 14 }, horizontalAlignment: "left", verticalAlignment: "center" };
note.getRange(`A3:B${noteRows.length + 2}`).format = { font: { name: "Arial", size: 10, color: "#1F2937" }, verticalAlignment: "top", wrapText: true };
note.getRange(`A3:A${noteRows.length + 2}`).format = { fill: "#EAF2F8", font: { name: "Arial", bold: true, size: 10, color: "#1F2937" }, verticalAlignment: "top", wrapText: true };
note.getRange(`A3:B${noteRows.length + 2}`).format.borders = { preset: "inside", style: "thin", color: "#D9E2F3" };
note.getRange("A:A").format.columnWidth = 18;
note.getRange("B:B").format.columnWidth = 100;
note.getRange("A1:D1").format.rowHeight = 28;
note.getRange(`A3:B${noteRows.length + 2}`).format.rowHeight = 32;
note.freezePanes.freezeRows(2);

await fs.mkdir(outputDir, { recursive: true });
wb.recalculate();
const preview = await wb.render({ sheetName: "训练数据200", autoCrop: "all", scale: 0.5, format: "png" });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
const file = await SpreadsheetFile.exportXlsx(wb);
await file.save(output);
const check = await wb.inspect({ kind: "table", range: `训练数据200!A1:W8`, include: "values,formulas", tableMaxRows: 8, tableMaxCols: 23 });
console.log(check.ndjson);
console.log(`EXPORTED=${output}`);
console.log(`PREVIEW=${previewPath}`);
