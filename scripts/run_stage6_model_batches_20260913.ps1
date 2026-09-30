$ErrorActionPreference = 'Stop'
$root = 'F:\ActionSKUTracker'
$py = Join-Path $root 'runtime\venv_qwen_cuda\Scripts\python.exe'
$script = Join-Path $root 'scripts\qwen_offline_stage5.py'
$inputRoot = Join-Path $root 'runtime\stage6\20260913\gold_model_inputs_500'
$outputRoot = Join-Path $root 'runtime\stage6\20260913\gold_model_batches'
$model = Join-Path $root 'runtime\models\Qwen3-8B'
$adapter = Join-Path $root 'runtime\training\qwen3_8b\20260911\combined_gold_incremental\formal_qlora_200_earlystop\adapter'
$log = Join-Path $outputRoot 'run.log'
$batches = @(
  '2026-09-07_025601',
  '2026-09-08_035740',
  '2026-09-09_032720',
  '2026-09-10_030315',
  '2026-09-12_024403'
)
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
Add-Content -Path $log -Value ("START {0}" -f (Get-Date -Format o))
foreach ($id in $batches) {
  $in = Join-Path $inputRoot ("stage6_input_{0}.jsonl" -f $id)
  $out = Join-Path $outputRoot $id
  $candidate = Join-Path $out 'candidates.jsonl'
  $manifest = Join-Path $out 'candidates.manifest.json'
  New-Item -ItemType Directory -Force -Path $out | Out-Null
  if (Test-Path $manifest) {
    Add-Content -Path $log -Value ("SKIP_EXISTING {0}" -f $id)
    continue
  }
  Add-Content -Path $log -Value ("START_BATCH {0}" -f $id)
  & $py $script --input $in --output $candidate --model-path $model --adapter-path $adapter --batch-size 1 --max-input-length 1024 --max-new-tokens 256 *>> $log
  if ($LASTEXITCODE -ne 0) {
    Add-Content -Path $log -Value ("FAILED_BATCH {0} exit={1}" -f $id, $LASTEXITCODE)
    exit $LASTEXITCODE
  }
  Add-Content -Path $log -Value ("DONE_BATCH {0}" -f $id)
}
Add-Content -Path $log -Value ("COMPLETE {0}" -f (Get-Date -Format o))
