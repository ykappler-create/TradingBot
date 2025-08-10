param(
  [Parameter(Mandatory=$true)]
  [ValidateSet("setup","lint","format","test","backtest","run","hook","clean")]
  [string]$Task,
  [string]$Csv = "bridge_out/reports/equity_curve.csv",
  [string]$Out = "bridge_out/reports/equity_curve.png"
)

function Ensure-Venv {
  if (-not (Test-Path ".\.venv")) { py -3 -m venv .venv }
  .\.venv\Scripts\Activate.ps1 | Out-Null
  python -m pip install --upgrade pip
  if (Test-Path ".\requirements.txt") { pip install -r requirements.txt }
}

switch ($Task) {
  "setup" {
    Ensure-Venv
    pip install pre-commit ruff black pytest mypy pip-audit
    pre-commit install
    Write-Host "Setup fertig."
  }
  "lint"   { Ensure-Venv; ruff check .; black --check .; mypy . }
  "format" { Ensure-Venv; ruff format .; black . }
  "test"   { Ensure-Venv; pytest -q }
  "backtest" {
    Ensure-Venv
    python scripts/backtest.py --csv $Csv --out $Out
  }
  "run"    { Ensure-Venv; if (!(Test-Path .env)) { Write-Warning ".env fehlt"; }; python bot.py }
  "hook"   { Ensure-Venv; pre-commit run --all-files --show-diff-on-failure }
  "clean"  { Remove-Item -Recurse -Force .pytest_cache, .ruff_cache, .mypy_cache, "bridge_out" -ErrorAction SilentlyContinue }
}
