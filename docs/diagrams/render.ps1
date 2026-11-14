# Renders every docs/diagrams/*.html to docs/img/<name>.png with headless Chrome or Edge.
# Each page declares its canvas size in <meta name="viewport-size" content="WIDTHxHEIGHT">.
#   .\docs\diagrams\render.ps1             # all diagrams
#   .\docs\diagrams\render.ps1 05-lineage  # one diagram
param([string]$Only)
$ErrorActionPreference = 'Stop'

$browser = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw 'Chrome or Edge is required to render the diagrams.' }

$out = Join-Path $PSScriptRoot '..\img'
New-Item -ItemType Directory -Force $out | Out-Null
$out = (Resolve-Path $out).Path
$userData = Join-Path ([IO.Path]::GetTempPath()) "minilake-diagrams-$PID"

Get-ChildItem $PSScriptRoot -Filter '*.html' | Where-Object { -not $Only -or $_.BaseName -eq $Only } | ForEach-Object {
    $size = [regex]::Match((Get-Content $_.FullName -Raw), 'name="viewport-size" content="(\d+)x(\d+)"')
    if (-not $size.Success) { throw "$($_.Name) has no viewport-size meta tag" }
    $png = Join-Path $out "$($_.BaseName).png"
    Remove-Item $png -ErrorAction SilentlyContinue
    $url = ([Uri]$_.FullName).AbsoluteUri
    $p = Start-Process -FilePath $browser -PassThru -Wait -WindowStyle Hidden -ArgumentList @(
        '--headless=new', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1',
        "--user-data-dir=`"$userData`"", "--window-size=$($size.Groups[1].Value),$($size.Groups[2].Value)",
        "--screenshot=`"$png`"", $url)
    if (-not (Test-Path $png)) { throw "render failed for $($_.Name) (exit $($p.ExitCode))" }
    Write-Host "  $($_.Name) -> docs/img/$($_.BaseName).png"
}
Remove-Item $userData -Recurse -Force -ErrorAction SilentlyContinue
