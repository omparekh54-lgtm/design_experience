param(
  [string]$DataRoot = "C:\DE_Data_Dump\deex_10y",
  [string]$PriceStart = "2014-10-24",
  [string]$FinancialStart = "2013-10-24",
  [string]$End = "2026-10-23",
  [int]$Shards = 8
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repo

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  py -3.12 -m venv .venv
}
$python = Join-Path $repo ".venv\Scripts\python.exe"

& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt

New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null

Write-Host "[1/4] Fetching market-wide NSE prices and benchmark/VIX history to $DataRoot"
& $python scripts/fetch_deex_local_market_data.py --start $PriceStart --end $End --root $DataRoot
if ($LASTEXITCODE -ne 0) { throw "Market-data stage failed with exit code $LASTEXITCODE" }

Write-Host "[2/4] Fetching global Finstack reference/index/calendar datasets"
& $python scripts/fetch_deex_local_finstack.py --start $FinancialStart --end $End --root $DataRoot --global-only
if ($LASTEXITCODE -ne 0) { throw "Finstack global stage failed with exit code $LASTEXITCODE" }

Write-Host "[3/4] Fetching point-in-time company financial filings directly from NSE"
for ($i = 0; $i -lt $Shards; $i++) {
  Write-Host "  financial shard $i / $Shards"
  & $python scripts/fetch_deex_local_financials.py --start $FinancialStart --end $End --root $DataRoot --shard $i --shards $Shards
}

Write-Host "[4/4] Fetching Finstack corporate actions, shareholding, result filings, annual reports, profiles and normalized statements"
for ($i = 0; $i -lt $Shards; $i++) {
  Write-Host "  finstack shard $i / $Shards"
  & $python scripts/fetch_deex_local_finstack.py --start $FinancialStart --end $End --root $DataRoot --shard $i --shards $Shards
}

Write-Host ""
Write-Host "DEEX local dataset build pass finished. Re-run this same script to resume only missing/incomplete items."
Write-Host "Data root: $DataRoot"
Write-Host "Manifests: $DataRoot\manifests"
Write-Host "Historical universe: $DataRoot\reference\historical_universe.parquet"
Write-Host "Prices: $DataRoot\normalized\prices"
Write-Host "Raw NSE source files: $DataRoot\raw"
Write-Host "Finstack normalized supporting data: $DataRoot\finstack"
