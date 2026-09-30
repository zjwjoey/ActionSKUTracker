import fs from 'node:fs/promises';
import fsSync from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool';

const ROOT = 'F:/ActionSKUTracker';
const INPUT = process.argv[2] || 'D:/Users/Administrator/Downloads/Action_Stage5_候选数据_500条_修订版_446Gold.xlsx';
const OUTPUT = process.argv[3] || `${ROOT}/runtime/training/qwen3_8b/20260913/stage5_gold_ingestion_446_v1`;
const BUNDLE = `${ROOT}/runtime/training/qwen3_8b/20260913/qwen_incremental_stage5_candidate_bundle_500_v1.jsonl`;
const HISTORICAL = [
  `${ROOT}/runtime/training/qwen3_8b/20260911/combined_gold_incremental/qwen_combined_gold_incremental_train.jsonl`,
  `${ROOT}/runtime/training/qwen3_8b/20260911/combined_gold_incremental/qwen_combined_gold_incremental_validation.jsonl`,
  `${ROOT}/runtime/training/qwen3_8b/20260911/combined_gold_incremental/qwen_combined_gold_incremental_test.jsonl`,
];

const FIELDS = ['name', 'cat1', 'cat2', 'spec', 'description', 'details'];
const SOURCE_COLUMNS = {
  name: '西语品名', cat1: '西语一级类目', cat2: '西语二级类目', spec: '西语规格',
  description: '西语描述', details: '西语详情',
};
const TARGET_COLUMNS = {
  name: '中文品名', cat1: '中文一级类目', cat2: '中文二级类目', spec: '中文规格',
  description: '中文描述', details: '中文详情',
};

const sha256 = (value) => crypto.createHash('sha256').update(value).digest('hex');
const sha256File = async (file) => sha256(await fs.readFile(file));
const canonical = (value) => JSON.stringify(value, Object.keys(value).sort());
const norm = (value) => String(value ?? '').trim();
const sourceHash = (source) => {
  const parts = [source.name_es, source.cat1_es, source.cat2_es, source.spec_es, source.desc_es, source.details_es];
  const h = crypto.createHash('sha256');
  for (const part of parts) { h.update(norm(part), 'utf8'); h.update('\x1f', 'utf8'); }
  return h.digest('hex');
};
const readJsonl = async (file) => {
  const text = await fs.readFile(file, 'utf8');
  return text.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
};
const immutableWrite = async (file, content) => {
  try {
    const previous = await fs.readFile(file, 'utf8');
    if (previous !== content) throw new Error(`IMMUTABLE_ARTIFACT_CONFLICT: ${file}`);
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
    await fs.writeFile(file, content, 'utf8');
  }
};
const messageObject = (row, role) => {
  const found = row.messages?.filter((message) => message.role === role)?.[0];
  return found ? JSON.parse(found.content) : null;
};
const stableRow = (row) => JSON.stringify(row, Object.keys(row).sort());

async function main() {
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(INPUT));
  const sheet = workbook.worksheets.getItem('候选数据');
  const values = sheet.getUsedRange(true).values;
  const headers = values[0].map((value) => norm(value));
  const rows = values.slice(1).filter((row) => row.some((value) => norm(value) !== ''))
    .map((row, index) => Object.fromEntries(headers.map((header, i) => [header, row[i]]).filter(([header]) => header)));
  const skuSet = new Set();
  const workbookRows = rows.map((row, index) => {
    const sku = norm(row['SKU']);
    if (!sku) throw new Error(`EMPTY_SKU row=${index + 2}`);
    if (skuSet.has(sku)) throw new Error(`DUPLICATE_SKU ${sku}`);
    skuSet.add(sku);
    const source = Object.fromEntries(FIELDS.map((field) => [field, norm(row[SOURCE_COLUMNS[field]])]));
    const target = Object.fromEntries(FIELDS.map((field) => [field, norm(row[TARGET_COLUMNS[field]])]));
    return {
      sku, row_number: index + 2, source, target,
      source_hash: norm(row['source_hash']),
      computed_source_hash: sourceHash({ name_es: source.name, cat1_es: source.cat1, cat2_es: source.cat2,
        spec_es: source.spec, desc_es: source.description, details_es: source.details }),
      gold_status: norm(row['Gold状态']), gold_conclusion: norm(row['Gold审核结论']),
      approval_status: norm(row['审核状态']), isolation_status: norm(row['隔离状态']),
      review_note: norm(row['审核/修订说明']), source_tier: norm(row['来源层级']),
    };
  });

  const bundleRows = await readJsonl(BUNDLE);
  const bundleBySku = new Map(bundleRows.map((row) => [norm(row.metadata?.sku), row]));
  const sourceMismatches = [];
  const hashMismatches = [];
  for (const row of workbookRows) {
    const bundle = bundleBySku.get(row.sku);
    if (!bundle) { sourceMismatches.push({ sku: row.sku, reason: 'NOT_IN_STAGE5_BUNDLE' }); continue; }
    const bundleSource = messageObject(bundle, 'user');
    const expected = Object.fromEntries(FIELDS.map((field) => [field, norm(bundleSource?.[field])]));
    if (JSON.stringify(expected) !== JSON.stringify(row.source)) sourceMismatches.push({ sku: row.sku, reason: 'SOURCE_TEXT_MISMATCH' });
    if (norm(bundle.metadata?.source_hash) !== row.computed_source_hash || row.source_hash !== row.computed_source_hash) {
      hashMismatches.push({ sku: row.sku, workbook: row.source_hash, computed: row.computed_source_hash, bundle: norm(bundle.metadata?.source_hash) });
    }
  }
  const historicalRows = (await Promise.all(HISTORICAL.map(readJsonl))).flat();
  const historicalSkuSet = new Set(historicalRows.map((row) => norm(row.metadata?.sku)).filter(Boolean));
  const goldRows = workbookRows.filter((row) => row.gold_status === 'GOLD' && row.gold_conclusion === 'ACCEPT_AS_GOLD');
  const pendingRows = workbookRows.filter((row) => row.gold_status === 'REVISE_THEN_GOLD' || row.gold_conclusion === 'REVISED_PENDING_OWNER');
  const heldRows = workbookRows.filter((row) => row.gold_status === 'NOT_GOLD' || row.gold_conclusion === 'HOLD_NOT_GOLD');
  const goldHistoricalOverlap = goldRows.filter((row) => historicalSkuSet.has(row.sku));
  const goldDisjoint = goldRows.filter((row) => !historicalSkuSet.has(row.sku));
  const makeTrainingRow = (row) => ({
    messages: [
      { role: 'system', content: '将 Action 西语商品六字段忠实标准化为简体中文；保持数字、单位、数量、尺寸和品牌/型号，不臆造。该记录为人工确认 Gold。' },
      { role: 'user', content: JSON.stringify(row.source, Object.keys(row.source).sort(), 0) },
      { role: 'assistant', content: JSON.stringify(row.target, Object.keys(row.target).sort(), 0) },
    ],
    metadata: {
      sku: row.sku, source_hash: row.computed_source_hash, gold_tier: 'HUMAN_REVIEWED_GOLD',
      review_status: row.approval_status, gold_status: row.gold_status, source_row: row.row_number,
      leakage_status: historicalSkuSet.has(row.sku) ? 'HISTORICAL_OVERLAP_BLOCKED' : 'DISJOINT_ELIGIBLE',
      production_write: false,
    },
  });
  const allGoldJsonl = goldRows.map(makeTrainingRow).sort((a, b) => a.metadata.sku.localeCompare(b.metadata.sku)).map((row) => JSON.stringify(row)).join('\n') + '\n';
  const disjointJsonl = goldDisjoint.map(makeTrainingRow).sort((a, b) => a.metadata.sku.localeCompare(b.metadata.sku)).map((row) => JSON.stringify(row)).join('\n') + (goldDisjoint.length ? '\n' : '');
  const excludedJsonl = [...pendingRows, ...heldRows].sort((a, b) => a.sku.localeCompare(b.sku)).map((row) => JSON.stringify({ ...row, exclusion_reason: row.gold_status === 'NOT_GOLD' ? 'HOLD_NOT_GOLD' : 'REVISED_PENDING_OWNER' })).join('\n') + '\n';
  const overlapJsonl = goldHistoricalOverlap.sort((a, b) => a.sku.localeCompare(b.sku)).map((row) => JSON.stringify({ sku: row.sku, source_hash: row.computed_source_hash, historical_split: historicalRows.find((x) => norm(x.metadata?.sku) === row.sku)?.metadata?.split ?? 'combined_gold_incremental', reason: 'DO_NOT_DUPLICATE_IN_NEW_SPLIT' })).join('\n') + (goldHistoricalOverlap.length ? '\n' : '');

  await fs.mkdir(OUTPUT, { recursive: true });
  const outputs = {
    gold_evidence: `${OUTPUT}/stage5_gold_evidence_446.jsonl`,
    source_only_input: `${OUTPUT}/stage5_gold_source_only_446.jsonl`,
    source_only_disjoint: `${OUTPUT}/stage5_gold_source_only_disjoint_4.jsonl`,
    disjoint_eligible: `${OUTPUT}/stage5_gold_disjoint_eligible_4.jsonl`,
    excluded: `${OUTPUT}/stage5_gold_excluded_54.jsonl`,
    historical_overlap: `${OUTPUT}/stage5_gold_historical_overlap_442.jsonl`,
  };
  const sourceOnlyJsonl = goldRows.map((row) => ({
    messages: [{ role: 'user', content: JSON.stringify(row.source, Object.keys(row.source).sort(), 0) }],
    metadata: {
      batch_id: 'stage5-gold-20260913', run_id: 'stage5-gold-workbook-20260913', observation_date: '2026-09-13',
      sku: row.sku, canonical_id: row.sku, source_hash: row.computed_source_hash,
      selection_reasons: ['NEEDS_REVIEW'], source_row: row.row_number,
    },
  })).sort((a, b) => a.metadata.sku.localeCompare(b.metadata.sku)).map((row) => JSON.stringify(row)).join('\n') + '\n';
  const sourceOnlyDisjointJsonl = goldDisjoint.map((row) => ({
    messages: [{ role: 'user', content: JSON.stringify(row.source, Object.keys(row.source).sort(), 0) }],
    metadata: {
      batch_id: 'stage5-gold-disjoint-20260913', run_id: 'stage5-gold-disjoint-20260913', observation_date: '2026-09-13',
      sku: row.sku, canonical_id: row.sku, source_hash: row.computed_source_hash,
      selection_reasons: ['NEEDS_REVIEW'], source_row: row.row_number,
    },
  })).sort((a, b) => a.metadata.sku.localeCompare(b.metadata.sku)).map((row) => JSON.stringify(row)).join('\n') + '\n';
  await immutableWrite(outputs.gold_evidence, allGoldJsonl);
  await immutableWrite(outputs.source_only_input, sourceOnlyJsonl);
  await immutableWrite(outputs.source_only_disjoint, sourceOnlyDisjointJsonl);
  await immutableWrite(outputs.disjoint_eligible, disjointJsonl);
  await immutableWrite(outputs.excluded, excludedJsonl);
  await immutableWrite(outputs.historical_overlap, overlapJsonl);

  const manifest = {
    artifact_type: 'STAGE5_HUMAN_GOLD_INGESTION', artifact_version: 1,
    status: sourceMismatches.length || hashMismatches.length ? 'FAIL_SOURCE_RECONCILIATION' : 'GOLD_EVIDENCE_INGESTED_OFFLINE',
    input_workbook: { path: path.resolve(INPUT), sha256: await sha256File(INPUT) },
    candidate_bundle: { path: path.resolve(BUNDLE), sha256: await sha256File(BUNDLE), rows: bundleRows.length },
    counts: { workbook_rows: workbookRows.length, unique_skus: skuSet.size, gold_confirmed: goldRows.length,
      revised_pending_owner: pendingRows.length, hold_not_gold: heldRows.length, historical_gold_overlap: goldHistoricalOverlap.length,
      disjoint_gold_eligible: goldDisjoint.length, historical_rows_checked: historicalRows.length },
    reconciliation: { source_mismatch_count: sourceMismatches.length, hash_mismatch_count: hashMismatches.length, source_mismatches: sourceMismatches, hash_mismatches: hashMismatches },
    leakage_policy: { historical_splits: HISTORICAL.map((p) => path.resolve(p)), overlap_rows_blocked_from_new_training: goldHistoricalOverlap.length, disjoint_rows_eligible_for_future_split: goldDisjoint.length, split_membership_changed: false },
    outputs: Object.fromEntries(Object.entries(outputs).map(([key, value]) => [key, { path: path.resolve(value), sha256: sha256(fsSync.readFileSync(value)), rows: key === 'gold_evidence' || key === 'source_only_input' ? goldRows.length : key === 'source_only_disjoint' || key === 'disjoint_eligible' ? goldDisjoint.length : key === 'excluded' ? pendingRows.length + heldRows.length : goldHistoricalOverlap.length }])),
    production_writes: { master: false, sqlite: false, dictionary: false, localization: false, lifecycle: false },
    acceptance: { stage5_offline_gold_ingestion: sourceMismatches.length === 0 && hashMismatches.length === 0 ? 'PASS' : 'FAIL', formal_stage5_release: 'BLOCKED_BY_STAGE4_FULL_RELEASE_FALSE' },
  };
  const manifestText = JSON.stringify(manifest, null, 2) + '\n';
  await immutableWrite(`${OUTPUT}/stage5_gold_ingestion_446_v1.manifest.json`, manifestText);
  console.log(JSON.stringify(manifest, null, 2));
}

await main();
