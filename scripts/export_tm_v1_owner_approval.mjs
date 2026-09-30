import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [, , stagingDirArg, queueArg, outputArg, decisionsArg] = process.argv;
if (!stagingDirArg || !queueArg || !outputArg) {
  throw new Error("Usage: node export_tm_v1_owner_approval.mjs <staging-dir> <queue-jsonl> <output-xlsx>");
}

const stagingDir = stagingDirArg;
const queuePath = queueArg;
const outputPath = outputArg;
const fontName = "Arial";
const readJsonl = async (path) => (await fs.readFile(path, "utf8"))
  .split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
const colName = (number) => {
  let value = number;
  let name = "";
  while (value > 0) {
    const rem = (value - 1) % 26;
    name = String.fromCharCode(65 + rem) + name;
    value = Math.floor((value - 1) / 26);
  }
  return name;
};
const applyHeader = (sheet, range) => {
  const header = sheet.getRange(range);
  header.format = {
    fill: "#1F4E78",
    font: { name: fontName, size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#FFFFFF" },
  };
};

const stagingRows = await readJsonl(`${stagingDir}/translation_memory_v1_staging.jsonl`);
const queueRows = await readJsonl(queuePath);
const decisionRows = decisionsArg ? await readJsonl(decisionsArg) : [];
const decisionsByTm = new Map(decisionRows.map((row) => [row.tm_id, row]));
if (decisionRows.length && (decisionRows.length !== stagingRows.length || stagingRows.some((row) => !decisionsByTm.has(row.tm_id)))) {
  throw new Error("Decision artifact must contain exactly one decision for every staging TM row");
}
const hasOwnerDecisions = decisionRows.length > 0;
const queueByPair = new Map(queueRows.map((row) => [row.pair_hash, row]));
const reviewRows = queueRows.filter((row) => row.candidate_status !== "TM_READY_CANDIDATE")
  .sort((a, b) => String(a.candidate_status).localeCompare(String(b.candidate_status)) || String(a.field).localeCompare(String(b.field)) || String(a.source_value_raw).localeCompare(String(b.source_value_raw)));

const workbook = Workbook.create();
const summary = workbook.worksheets.add("审批摘要");
const approved = workbook.worksheets.add("审批628");
const isolated = workbook.worksheets.add("隔离130");
const notes = workbook.worksheets.add("使用说明");

for (const sheet of [summary, approved, isolated, notes]) {
  sheet.showGridLines = false;
  sheet.getRange("A1:Z1000").format.font = { name: fontName, size: 10, color: "#1F1F1F" };
}
summary.tabColor = "#1F4E78";
approved.tabColor = "#5B9BD5";
isolated.tabColor = "#C55A11";
notes.tabColor = "#7F7F7F";

summary.getRange("A2:H2").merge();
summary.getRange("A2").values = [[hasOwnerDecisions ? "Translation Memory V1 — Owner 决定已完成" : "Translation Memory V1 — Owner 审批包"]];
summary.getRange("A2").format = { font: { name: fontName, size: 16, bold: true, color: "#1F1F1F" }, verticalAlignment: "center" };
summary.getRange("A3:H3").merge();
summary.getRange("A3").values = [[hasOwnerDecisions ? "本工作簿记录用户授权的 Owner 决定；下一步仅生成 Shadow Termbase 输入，不会写入 TM、Dictionary、SQLite 或 Master。" : "本工作簿只记录审批决定；保存后由独立导入程序生成批准清单，当前不会写入 TM、Dictionary、SQLite 或 Master。"]];
summary.getRange("A3").format = { font: { name: fontName, size: 10, italic: true, color: "#595959" } };
summary.getRange("A5:B9").values = [
  ["项目", "数量"],
  ["审计通过、待 Owner 决定", stagingRows.length],
  ["隔离待复核", reviewRows.length],
  ["其中：译文冲突", reviewRows.filter((row) => row.candidate_status === "CONFLICT_REVIEW").length],
  ["其中：规则/政策复核", reviewRows.filter((row) => row.candidate_status === "REVIEW_REQUIRED").length],
];
applyHeader(summary, "A5:B5");
summary.getRange("A6:B9").format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
summary.getRange("B6:B9").format.horizontalAlignment = "right";
summary.getRange("D5:E9").values = [
  ["审批状态", "计算结果"],
  ["批准", ""],
  ["上下文限定", ""],
  ["拒绝", ""],
  ["仍待决定", ""],
];
summary.getRange("E6:E9").formulas = [
  [`=COUNTIF('审批628'!$A$2:$A$${stagingRows.length + 1},"APPROVE")`],
  [`=COUNTIF('审批628'!$A$2:$A$${stagingRows.length + 1},"CONTEXT_ONLY")`],
  [`=COUNTIF('审批628'!$A$2:$A$${stagingRows.length + 1},"REJECT")`],
  [`=COUNTBLANK('审批628'!$A$2:$A$${stagingRows.length + 1})+COUNTIF('审批628'!$A$2:$A$${stagingRows.length + 1},"NEEDS_REVIEW")`],
];
applyHeader(summary, "D5:E5");
summary.getRange("D6:E9").format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
summary.getRange("E6:E9").format.horizontalAlignment = "right";
summary.getRange("A12:H16").values = [
  ["审批动作", "含义", "后续处理", "是否写生产"],
  ["APPROVE", "批准该字段级西语→中文 TM 对", "纳入下一份 Approved Manifest", "否"],
  ["CONTEXT_ONLY", "当前字段正确，但不得全局自动复用", "只允许同 SKU、字段和 source hash 的 Shadow 上下文命中", "否"],
  ["REJECT", "拒绝，不得导入", "保留审计记录", "否"],
  ["NEEDS_REVIEW", "仍需确认或修订中文值", "继续隔离", "否"],
];
applyHeader(summary, "A12:D12");
summary.getRange("A13:D16").format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
summary.getRange("A13:D16").format.wrapText = true;
summary.getRange("A13:D16").format.autofitRows();
summary.getRange("A1:H17").format.verticalAlignment = "center";
summary.getRange("A:A").format.columnWidth = 32;
summary.getRange("B:B").format.columnWidth = 26;
summary.getRange("C:C").format.columnWidth = 32;
summary.getRange("D:D").format.columnWidth = 24;
summary.getRange("E:E").format.columnWidth = 16;

const approvedHeaders = ["Owner 决定", "Owner 备注", "审计建议", "字段", "西语原文", "中文候选", "SKU 示例", "出现次数", "来源哈希", "TM ID", "Pair Hash", "来源记录数", "政策版本", "当前状态"];
const approvedMatrix = [approvedHeaders];
for (const row of stagingRows) {
  const source = queueByPair.get(row.pair_hash) || {};
  const decision = decisionsByTm.get(row.tm_id);
  approvedMatrix.push([
    decision?.owner_decision || "", decision?.owner_note || "", decision?.decision_reason_code || "建议批准", row.field_name, row.source_value_raw, row.target_value,
    row.sku_example, Number(source.occurrence_count || 0), row.source_hash, row.tm_id,
    row.pair_hash, Array.isArray(row.provenance) ? row.provenance.length : 0,
    row.policy_version, hasOwnerDecisions ? "OWNER_DECISION_RECORDED" : row.review_status,
  ]);
}
approved.getRangeByIndexes(0, 0, approvedMatrix.length, approvedHeaders.length).values = approvedMatrix;
applyHeader(approved, `A1:${colName(approvedHeaders.length)}1`);
approved.getRange(`A2:B${approvedMatrix.length}`).format.fill = "#FFF2CC";
approved.getRange(`C2:${colName(approvedHeaders.length)}${approvedMatrix.length}`).format.verticalAlignment = "top";
approved.getRange(`A1:${colName(approvedHeaders.length)}${approvedMatrix.length}`).format.wrapText = false;
approved.getRange(`E2:F${approvedMatrix.length}`).format.wrapText = true;
approved.getRange(`E2:F${approvedMatrix.length}`).format.verticalAlignment = "top";
approved.getRange(`A1:${colName(approvedHeaders.length)}${approvedMatrix.length}`).format.borders = { preset: "insideHorizontal", style: "thin", color: "#E7E6E6" };
approved.getRange(`A2:A${approvedMatrix.length}`).dataValidation = { rule: { type: "list", values: ["APPROVE", "CONTEXT_ONLY", "REJECT", "NEEDS_REVIEW"] } };
approved.getRange(`A2:A${approvedMatrix.length}`).conditionalFormats.add("containsText", { text: "APPROVE", format: { fill: "#E2F0D9", font: { bold: true, color: "#375623" } } });
approved.getRange(`A2:A${approvedMatrix.length}`).conditionalFormats.add("containsText", { text: "CONTEXT_ONLY", format: { fill: "#FFF2CC", font: { bold: true, color: "#7F6000" } } });
approved.getRange(`A2:A${approvedMatrix.length}`).conditionalFormats.add("containsText", { text: "REJECT", format: { fill: "#FCE4D6", font: { bold: true, color: "#9C0006" } } });
approved.getRange(`A2:A${approvedMatrix.length}`).conditionalFormats.add("containsText", { text: "NEEDS_REVIEW", format: { fill: "#FFF2CC", font: { bold: true, color: "#7F6000" } } });
approved.tables.add(`A1:${colName(approvedHeaders.length)}${approvedMatrix.length}`, true, "TmOwnerApprovalTable");
approved.freezePanes.freezeRows(1);
approved.freezePanes.freezeColumns(4);
[["A:A", 18], ["B:B", 28], ["C:C", 16], ["D:D", 14], ["E:E", 45], ["F:F", 45], ["G:G", 12], ["H:H", 10], ["I:I", 66], ["J:J", 66], ["K:K", 66], ["L:L", 12], ["M:M", 24], ["N:N", 32]].forEach(([range, width]) => { approved.getRange(range).format.columnWidth = width; });

const isolatedHeaders = ["Owner 决定", "Owner 备注", "隔离原因", "字段", "西语原文", "当前中文候选", "SKU 示例", "出现次数", "政策/Guard 标记", "来源哈希", "Pair Hash", "现有 Gold 状态"];
const isolatedMatrix = [isolatedHeaders];
for (const row of reviewRows) {
  isolatedMatrix.push([
    "", "", row.candidate_status === "CONFLICT_REVIEW" ? "多个已批准中文值冲突" : "需政策或规则复核",
    row.field, row.source_value_raw, row.target_value, row.sku, Number(row.occurrence_count || 0),
    Array.isArray(row.policy_flags) ? row.policy_flags.join("; ") : String(row.policy_flags || ""),
    row.source_hash, row.pair_hash, row.gold_status,
  ]);
}
isolated.getRangeByIndexes(0, 0, isolatedMatrix.length, isolatedHeaders.length).values = isolatedMatrix;
applyHeader(isolated, `A1:${colName(isolatedHeaders.length)}1`);
isolated.getRange(`A2:B${isolatedMatrix.length}`).format.fill = "#FFF2CC";
isolated.getRange(`A2:A${isolatedMatrix.length}`).dataValidation = { rule: { type: "list", values: ["APPROVE_WITH_CORRECTION", "REJECT", "NEEDS_REVIEW"] } };
isolated.getRange(`A1:${colName(isolatedHeaders.length)}${isolatedMatrix.length}`).format.borders = { preset: "insideHorizontal", style: "thin", color: "#E7E6E6" };
isolated.getRange(`E2:F${isolatedMatrix.length}`).format.wrapText = true;
isolated.getRange(`I2:I${isolatedMatrix.length}`).format.wrapText = true;
isolated.getRange(`A2:A${isolatedMatrix.length}`).conditionalFormats.add("containsText", { text: "REJECT", format: { fill: "#FCE4D6", font: { bold: true, color: "#9C0006" } } });
isolated.tables.add(`A1:${colName(isolatedHeaders.length)}${isolatedMatrix.length}`, true, "TmIsolatedReviewTable");
isolated.freezePanes.freezeRows(1);
isolated.freezePanes.freezeColumns(4);
[["A:A", 24], ["B:B", 28], ["C:C", 28], ["D:D", 14], ["E:E", 45], ["F:F", 45], ["G:G", 12], ["H:H", 10], ["I:I", 34], ["J:J", 66], ["K:K", 66], ["L:L", 34]].forEach(([range, width]) => { isolated.getRange(range).format.columnWidth = width; });

notes.getRange("A2:F2").merge();
notes.getRange("A2").values = [["审批填写说明"]];
notes.getRange("A2").format = { font: { name: fontName, size: 14, bold: true } };
notes.getRange("A4:B8").values = [
  ["工作表", "用途"],
  ["审批628", hasOwnerDecisions ? "628 条已记录 Owner 决定。APPROVE 可进入 Shadow TM；CONTEXT_ONLY 只能在同 SKU、同字段、同 source hash 下使用。" : "628 条已通过结构、来源和政策审计的 TM 候选。只填写 A、B 两列。"],
  ["隔离130", "130 条风险或冲突项。仅在确认或写明修订后才可进入下一轮 staging。"],
  ["审批摘要", "自动统计已填写决定，不构成生产导入。"],
  ["本文件", "不会自动改动 TM、Dictionary、SQLite、Master 或训练集。"],
];
applyHeader(notes, "A4:B4");
notes.getRange("A5:B8").format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
notes.getRange("B5:B8").format.wrapText = true;
notes.getRange("A:A").format.columnWidth = 22;
notes.getRange("B:B").format.columnWidth = 88;

workbook.recalculate();
const summaryCheck = await workbook.inspect({ kind: "table", range: "审批摘要!A2:E15", include: "values,formulas", tableMaxRows: 20, tableMaxCols: 8 });
if (!summaryCheck.ndjson.includes("628") || !summaryCheck.ndjson.includes("130")) {
  throw new Error("Summary counts missing from approval workbook");
}
const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 20 },
  summary: "owner approval workbook formula error scan",
});
if (/#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A|#NUM!|#NULL!|#SPILL!|#CALC!/.test(formulaErrors.ndjson)) {
  throw new Error(`Formula error scan failed: ${formulaErrors.ndjson}`);
}
for (const [sheetName, range, fileName] of [
  ["审批摘要", "A1:H17", "TM_V1_OWNER_APPROVAL_20260915.preview.png"],
  ["审批628", "A1:N12", "TM_V1_OWNER_APPROVAL_20260915.approval_preview.png"],
  ["隔离130", "A1:L12", "TM_V1_OWNER_APPROVAL_20260915.isolated_preview.png"],
  ["使用说明", "A1:B8", "TM_V1_OWNER_APPROVAL_20260915.notes_preview.png"],
]) {
  const render = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(`${stagingDir}/${fileName}`, new Uint8Array(await render.arrayBuffer()));
}
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({ outputPath, approvedRows: stagingRows.length, isolatedRows: reviewRows.length }));
