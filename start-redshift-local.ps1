# Docker-free Redshift stand-in: a native PostgreSQL 16 (from the `pgserver` wheel)
# on Redshift's port 5439, with Redshift's default database/user (dev / awsuser).
#   .\start-redshift-local.ps1          # init (first run) + start
#   .\start-redshift-local.ps1 -Stop    # stop
param(
    [switch]$Stop,
    [int]$Port = 5439,
    [string]$Password = $(if ($env:REDSHIFT_PASSWORD) { $env:REDSHIFT_PASSWORD } else { 'localdev' })
)
$ErrorActionPreference = 'Stop'

$bin = python -c "import pathlib, pgserver; print(pathlib.Path(pgserver.__file__).parent / 'pginstall' / 'bin')" 2>$null
if (-not $bin -or -not (Test-Path $bin)) { throw "pgserver not installed. Run: pip install pgserver" }
$data = Join-Path $PSScriptRoot 'redshift-data'
$log = Join-Path $data 'server.log'

if ($Stop) {
    & "$bin\pg_ctl.exe" -D $data stop -m fast
    return
}

if (-not (Test-Path (Join-Path $data 'PG_VERSION'))) {
    $pwfile = New-TemporaryFile
    Set-Content -Path $pwfile -Value $Password -NoNewline
    & "$bin\initdb.exe" -D $data -U awsuser --pwfile=$pwfile -A scram-sha-256 -E UTF8 --no-locale | Out-Null
    Remove-Item $pwfile
}

& "$bin\pg_isready.exe" -h 127.0.0.1 -p $Port -q
if ($LASTEXITCODE -ne 0) {
    # Launch detached: piping pg_ctl's output would let the server inherit the pipe and hang this script.
    $ctl = Start-Process -FilePath "$bin\pg_ctl.exe" -PassThru -WindowStyle Hidden `
        -ArgumentList @('-D', "`"$data`"", '-l', "`"$log`"", '-o', "`"-p $Port -c listen_addresses=localhost`"", '-w', 'start')
    $ctl.WaitForExit()
    if ($ctl.ExitCode -ne 0) { throw "pg_ctl start failed; see $log" }
}

$env:PGPASSWORD = $Password
$exists = & "$bin\psql.exe" -h localhost -p $Port -U awsuser -d postgres -tAc "select 1 from pg_database where datname='dev'"
if ($exists -ne '1') { & "$bin\createdb.exe" -h localhost -p $Port -U awsuser dev }

Write-Host "Redshift stand-in ready: host=localhost port=$Port dbname=dev user=awsuser"
Write-Host "psql: & '$bin\psql.exe' -h localhost -p $Port -U awsuser -d dev"
