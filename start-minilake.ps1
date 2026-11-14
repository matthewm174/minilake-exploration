# Starts minilake with persistent Unity Catalog state rooted in this folder.
# The embedded JupyterLab (UI > Notebooks) is on unless -NoNotebook is given; it needs
# the notebook extra: pip install "minilake[notebook]".
param(
    [int]$Port = 8000,
    [switch]$NoNotebook
)

$env:MINILAKE_DATA_DIR = Join-Path $PSScriptRoot 'data'
$env:MINILAKE_PERSIST = '1'
$env:MINILAKE_NOTEBOOK = if ($NoNotebook) { '0' } else { '1' }

if (-not $NoNotebook) {
    python -c "import jupyterlab" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'jupyterlab is not installed, so Notebooks will be unavailable. Run: pip install "minilake[notebook]"'
    }
}

$cmd = Get-Command minilake -ErrorAction SilentlyContinue
if ($cmd) {
    $exe = $cmd.Source
} else {
    $scripts = python -c "import os, sysconfig; print(sysconfig.get_path('scripts', os.name + '_user'))"
    $exe = Join-Path $scripts 'minilake.exe'
    if (-not (Test-Path $exe)) { throw "minilake not found. Run: pip install minilake" }
}

Write-Host "minilake data dir: $env:MINILAKE_DATA_DIR"
Write-Host "API: http://localhost:$Port   SQL editor: http://localhost:$Port/ui/"
if (-not $NoNotebook) { Write-Host "Notebooks: http://localhost:$Port/ui/notebooks/  (files in data\notebooks)" }
& $exe --port $Port
