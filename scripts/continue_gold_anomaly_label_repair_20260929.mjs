import fs from 'node:fs/promises';
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool';

const input = 'F:/ActionSKUTracker/outputs/20260929_gold_repair/20260929_Action中文研究版_Gold异常字段中文标注候选_去除SKU3224622.xlsx';
const output = 'F:/ActionSKUTracker/outputs/20260929_gold_repair/20260929_Action中文研究版_Gold异常清理版_去除SKU3224622.xlsx';

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(input));
const sheet = workbook.worksheets.getItem('商品全量');
const used = sheet.getUsedRange(true);
const original = used.values;
const headers = original[0].map((v) => String(v ?? '').trim());
const skuCol = headers.indexOf('编号');
const descriptionCol = headers.indexOf('描述');
const detailCol = headers.indexOf('产品详情');
const remarkCol = headers.indexOf('备注');
if ([skuCol, descriptionCol, detailCol, remarkCol].some((i) => i < 0)) throw new Error('REQUIRED_HEADERS_NOT_FOUND');

const allowedRemark = (part) => part === '在售状态：在售'
  || part === '新品'
  || part === '促销'
  || part === '可持续'
  || part.startsWith('官网官方标签：');
let detailsCleaned = 0;
let remarksCleaned = 0;
let rawDescriptionExceptionCount = 0;
const cleanDetails = [];
const cleanRemarks = [];
const cleanedSkus = [];

for (const row of original.slice(1)) {
  const sku = String(row[skuCol] ?? '').trim();
  const description = String(row[descriptionCol] ?? '');
  let details = String(row[detailCol] ?? '');
  const remark = String(row[remarkCol] ?? '');

  if (/异常/i.test(description)) rawDescriptionExceptionCount += 1;
  const oldDetails = details;
  details = details.replace(/；?官网异常原字段（(?:Sustancia|Incluye oído)）：(?:Válido(?:, Lavado)?|Sí|No)(?=；|$)/g, '');
  details = details.replace(/；{2,}/g, '；').replace(/^；|；$/g, '');
  if (details !== oldDetails) {
    detailsCleaned += 1;
    cleanedSkus.push(sku);
  }
  cleanDetails.push([details]);

  const parts = remark.split('；').map((part) => part.trim()).filter(Boolean);
  const retained = /异常/.test(remark) ? parts.filter(allowedRemark) : parts;
  const newRemark = retained.join('；');
  if (newRemark !== remark) remarksCleaned += 1;
  cleanRemarks.push([newRemark]);
}

if (detailsCleaned !== 71) throw new Error(`EXPECTED_71_DETAIL_ROWS_CLEANED:${detailsCleaned}`);
if (remarksCleaned < 75 || remarksCleaned > 100) throw new Error(`UNEXPECTED_REMARK_ROW_COUNT_CLEANED:${remarksCleaned}`);
if (new Set(cleanedSkus).size !== cleanedSkus.length) throw new Error('DUPLICATE_CLEANED_SKU');
if (cleanedSkus.includes('3224622')) throw new Error('EXCLUDED_SKU_PRESENT');

const colLetter = (index) => {
  let n = index + 1; let out = '';
  while (n) { const rem = (n - 1) % 26; out = String.fromCharCode(65 + rem) + out; n = Math.floor((n - 1) / 26); }
  return out;
};
const lastRow = original.length;
sheet.getRange(`${colLetter(detailCol)}2:${colLetter(detailCol)}${lastRow}`).values = cleanDetails;
sheet.getRange(`${colLetter(remarkCol)}2:${colLetter(remarkCol)}${lastRow}`).values = cleanRemarks;
workbook.recalculate();

const finalValues = sheet.getUsedRange(true).values;
let remainingMarkedFields = 0;
let remainingAnomalyRemarks = 0;
let descriptionsChanged = 0;
let nonTargetChanges = 0;
for (let i = 1; i < finalValues.length; i += 1) {
  const after = finalValues[i];
  const before = original[i];
  const detail = String(after[detailCol] ?? '');
  const remark = String(after[remarkCol] ?? '');
  remainingMarkedFields += (detail.match(/官网异常原字段|(?:^|[;；])\s*(?:Incluye oído|Sustancia)\s*:/g) ?? []).length;
  if (/异常/.test(remark)) remainingAnomalyRemarks += 1;
  if (after[descriptionCol] !== before[descriptionCol]) descriptionsChanged += 1;
  for (let c = 0; c < after.length; c += 1) {
    if (c !== detailCol && c !== remarkCol && after[c] !== before[c]) nonTargetChanges += 1;
  }
}
if (remainingMarkedFields || remainingAnomalyRemarks || descriptionsChanged || nonTargetChanges) {
  throw new Error(JSON.stringify({ remainingMarkedFields, remainingAnomalyRemarks, descriptionsChanged, nonTargetChanges }));
}

await fs.mkdir('F:/ActionSKUTracker/outputs/20260929_gold_repair', { recursive: true });
const check = await workbook.inspect({ kind: 'table', range: '商品全量!B1:N8', include: 'values', tableMaxRows: 8, tableMaxCols: 13, maxChars: 5000 });
const preview = await workbook.render({ sheetName: '商品全量', range: 'J3093:N3093', scale: 1, format: 'png' });
await fs.writeFile('F:/ActionSKUTracker/.tmp/gold_anomaly_clean_sample.png', new Uint8Array(await preview.arrayBuffer()));
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(output);
console.log(JSON.stringify({ output, detailsCleaned, remarksCleaned, rawDescriptionExceptionCount, remainingMarkedFields, remainingAnomalyRemarks, descriptionsChanged, nonTargetChanges }));
console.log(check.ndjson);
