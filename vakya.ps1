# Vakya for Windows 10/11: one command to set up, start, stop and check the whole studio.
# Works in Windows PowerShell 5.1 and PowerShell 7. macOS and Linux use ./vakya instead.
#
#   powershell -ExecutionPolicy Bypass -File .\vakya.ps1 setup    install everything
#   ... start | stop | restart | status | doctor | logs [api|web] | test | warm | clean-web
#
# Ports: $env:VAKYA_API_PORT (default 8020) and $env:VAKYA_WEB_PORT (default 3010).
# This file is ASCII-only on purpose, so Windows PowerShell 5.1 reads it correctly.

param(
    [Parameter(Position = 0)]
    [string]$Command = 'help',
    [Parameter(Position = 1)]
    [string]$Target = '',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest = @()
)

$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Root     = $PSScriptRoot
$Backend  = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$VenvDir  = Join-Path $Backend '.venv'
$VenvPy   = Join-Path $VenvDir 'Scripts\python.exe'
$EnvFile  = Join-Path $Backend '.env'
$RunDir   = Join-Path $Root '.vakya'
$LogDir   = Join-Path $RunDir 'logs'

$ApiPort = if ($env:VAKYA_API_PORT) { [int]$env:VAKYA_API_PORT } else { 8020 }
$WebPort = if ($env:VAKYA_WEB_PORT) { [int]$env:VAKYA_WEB_PORT } else { 3010 }
$ApiUrl  = "http://localhost:$ApiPort"
$WebUrl  = "http://localhost:$WebPort"

$Tick  = [string][char]0x2713
$Cross = [string][char]0x2717
$Dot   = [string][char]0x00B7

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# ------------------------------------------------------------------------------------------- output helpers
function Ok   { param([string]$Text) Write-Host ("  {0} {1}" -f $Tick, $Text) -ForegroundColor Green }
function Warn { param([string]$Text) Write-Host ("  ! {0}" -f $Text) -ForegroundColor Yellow }
function Bad  { param([string]$Text) Write-Host ("  {0} {1}" -f $Cross, $Text) -ForegroundColor Red }
function Fix  { param([string]$Text) Write-Host ("    fix: {0}" -f $Text) -ForegroundColor DarkGray }
function Step { param([string]$Text) Write-Host ''; Write-Host $Text -ForegroundColor White }

function Has {
    param([string]$Name)
    [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-DotEnv {
    # Reads KEY=value from backend\.env as plain text. The file is never executed.
    param([string]$Key)
    if (-not (Test-Path $EnvFile)) { return '' }
    $pattern = '^\s*' + [regex]::Escape($Key) + '\s*=\s*(.*?)\s*$'
    foreach ($line in (Get-Content -Path $EnvFile)) {
        if ($line -match $pattern) { return $Matches[1].Trim([char]'"', [char]"'") }
    }
    return ''
}

function Get-Setting {
    # The shell environment first, else backend\.env (the same rule as ./vakya and the API).
    param([string]$Key)
    $v = [Environment]::GetEnvironmentVariable($Key, 'Process')
    if ($v) { return $v }
    return (Get-DotEnv $Key)
}

function Show-Keys {
    # Gnani (speech), the hosted language model and the embeddings (knowledge search). Only whether a key is set is printed, never the key.
    if (Get-Setting 'GNANI_API_KEY') {
        Ok 'Gnani key set (speech: Prisma listening, Timbre speaking, voice cloning)'
    } else {
        Bad 'GNANI_API_KEY is not set: the agent can neither hear nor speak'
        Fix 'add GNANI_API_KEY=... to backend\.env'
    }
    if ((Get-Setting 'LLM_API_KEY') -and (Get-Setting 'LLM_BASE_URL') -and (Get-Setting 'LLM_MODEL')) {
        Ok 'Language model set (key, base URL and model)'
    } else {
        Bad 'The language model is not set up: the agent cannot answer'
        Fix 'add LLM_API_KEY=..., LLM_BASE_URL=... and LLM_MODEL=... to backend\.env'
    }
    if (((Get-Setting 'EMBEDDINGS_API_KEY') -or (Get-Setting 'EMBEDDINGS_API_KEYS')) -and (Get-Setting 'EMBEDDINGS_BASE_URL') -and (Get-Setting 'EMBEDDINGS_MODEL')) {
        Ok 'Embeddings set (knowledge search)'
    } else {
        Warn 'Embeddings not set: documents are searched by keyword only'
        Fix 'add EMBEDDINGS_API_KEY=..., EMBEDDINGS_BASE_URL=... and EMBEDDINGS_MODEL=... to backend\.env'
    }
}

# ------------------------------------------------------------------------------------------- python and processes
function Test-PythonCommand {
    # Asks an interpreter for its full path. Returns that path only when it is Python 3.10 or newer.
    param([string]$Exe, [string]$Version = '')
    $code = "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')"
    if ($Version) { $out = & $Exe $Version -c $code 2>$null } else { $out = & $Exe -c $code 2>$null }
    if ($LASTEXITCODE -ne 0) { return $null }
    $found = @($out) | Select-Object -First 1
    if ($found -and (Test-Path -LiteralPath ([string]$found))) { return [string]$found }
    return $null
}

function Find-Python {
    # Order: py -3.12, py -3.11, py -3.10, then python. Returns the full path of the interpreter, or $null.
    if (Has 'py') {
        foreach ($version in @('-3.12', '-3.11', '-3.10')) {
            $found = Test-PythonCommand -Exe 'py' -Version $version
            if ($found) { return $found }
        }
    }
    if (Has 'python') {
        $found = Test-PythonCommand -Exe 'python'
        if ($found) { return $found }
    }
    return $null
}

function Get-PidOf {
    param([string]$Name)
    $file = Join-Path $RunDir "$Name.pid"
    if (-not (Test-Path $file)) { return $null }
    $raw = Get-Content -Path $file -TotalCount 1
    $number = 0
    if ([int]::TryParse(("$raw").Trim(), [ref]$number)) { return $number }
    return $null
}

function Test-Alive {
    param([string]$Name)
    $savedPid = Get-PidOf $Name
    [bool]($savedPid -and (Get-Process -Id $savedPid -ErrorAction SilentlyContinue))
}

function Test-PortBusy {
    param([int]$Port)
    try { [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop) } catch { $false }
}

function Start-Hidden {
    # Runs a command through cmd.exe in a hidden window. cmd.exe is the root of the process tree,
    # so taskkill /T can stop the API or the web app together with its children.
    param([string]$Command, [string]$WorkDir, [hashtable]$Vars = @{})
    $saved = @{}
    foreach ($key in $Vars.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, [string]$Vars[$key], 'Process')
    }
    try {
        return Start-Process -FilePath 'cmd.exe' -ArgumentList ('/d /c "{0}"' -f $Command) -WorkingDirectory $WorkDir -WindowStyle Hidden -PassThru
    } finally {
        foreach ($key in $Vars.Keys) {
            [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process')
        }
    }
}

function Wait-Http {
    param([string]$Url, [int]$Seconds, $Proc)
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    while ($watch.Elapsed.TotalSeconds -lt $Seconds) {
        if ($Proc -and $Proc.HasExited) { return $false }
        try {
            Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 15 | Out-Null
            return $true
        } catch {
            Start-Sleep -Milliseconds 700
        }
    }
    return $false
}

function Save-Pid {
    param([string]$Name, $Proc)
    Set-Content -Path (Join-Path $RunDir "$Name.pid") -Value "$($Proc.Id)" -Encoding ASCII
}

# ------------------------------------------------------------------------------------------- setup
function Invoke-Setup {

    Step ("Checking the machine (Windows {0}, {1})" -f [Environment]::OSVersion.Version, $env:PROCESSOR_ARCHITECTURE)
    $basePy = Find-Python
    if (-not $basePy) {
        Bad 'Python 3.10 or newer is required'
        Fix 'winget install Python.Python.3.12   (then open a new PowerShell window)'
        exit 1
    }
    $pyVersion = (& $basePy -c 'import platform; print(platform.python_version())' 2>$null | Select-Object -First 1)
    Ok ("Python {0}  ({1})" -f $pyVersion, $basePy)

    if (Has 'node') {
        Ok ("Node {0}" -f (node -v))
    } else {
        Bad 'Node.js 20+ is required'
        Fix 'winget install OpenJS.NodeJS.LTS'
        exit 1
    }

    if (Has 'ffmpeg') { Ok 'ffmpeg' } else { Warn 'ffmpeg is missing (needed to read uploaded audio that is not plain WAV)'; Fix 'winget install Gyan.FFmpeg' }

    Step 'Backend environment (backend\.venv)'
    if (-not (Test-Path $VenvPy)) {
        & $basePy -m venv $VenvDir
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPy)) { Bad 'Could not create backend\.venv'; exit 1 }
    }
    & $VenvPy -m pip install -q --upgrade pip

    $reqFile        = Join-Path $Backend 'requirements-dev.txt'
    & $VenvPy -m pip install -q -r $reqFile
    if ($LASTEXITCODE -eq 0) { Ok 'Python packages installed' } else { Bad 'pip install failed (see the output above)'; exit 1 }

    if (-not (Test-Path $EnvFile)) {
        $example = Join-Path $Backend '.env.example'
        if (Test-Path $example) {
            Copy-Item -Path $example -Destination $EnvFile
            Ok 'Created backend\.env from the example (add keys there when you have them)'
        } else {
            Warn 'backend\.env.example is missing; create backend\.env by hand'
        }
    } else {
        Ok 'backend\.env exists (left unchanged)'
    }

    Step 'Web app (frontend\node_modules)'
    Push-Location $Frontend
    & npm.cmd install --no-audit --no-fund --loglevel=error
    $npmCode = $LASTEXITCODE
    Pop-Location
    if ($npmCode -eq 0) { Ok 'npm packages installed' } else { Bad 'npm install failed'; exit 1 }

    Step 'Keys'
    Show-Keys

    Step 'Done. Start everything with: .\vakya.ps1 start'
}

# ------------------------------------------------------------------------------------------- start / stop / status
# next dev keeps its build in frontend\.next. A cache built from older code can serve old pages after many merges, so a start
# removes it when HEAD is not the commit the last web start recorded (.vakya\web.head). Same rule as ./vakya.
function Clear-StaleWebCache {
    if (-not (Has 'git')) { return }
    $head = (& git -C $Root rev-parse HEAD 2>$null | Select-Object -First 1)
    if (-not $head) { return }
    $headFile = Join-Path $RunDir 'web.head'
    $recorded = ''
    if (Test-Path $headFile) { $recorded = (Get-Content -Path $headFile -TotalCount 1).Trim() }
    $cache = Join-Path $Frontend '.next'
    if ((Test-Path $cache) -and ($head -ne $recorded)) {
        try {
            Remove-Item -Path $cache -Recurse -Force -ErrorAction Stop
            Ok 'Cleared the web build cache (code changed since the last start)'
        } catch { Warn 'Could not remove frontend\.next' }
    }
    Set-Content -Path $headFile -Value $head
}

function Invoke-CleanWeb {
    if (Test-Alive 'web') {
        Bad ("The web app is running (pid {0}), so its build cache stays" -f (Get-PidOf 'web'))
        Fix '.\vakya.ps1 stop, then .\vakya.ps1 clean-web'
        exit 1
    }
    $cache = Join-Path $Frontend '.next'
    if (Test-Path $cache) {
        Remove-Item -Path $cache -Recurse -Force
        Ok 'Deleted frontend\.next (the web build cache; the next start rebuilds it)'
    } else {
        Write-Host '  No web build cache (frontend\.next) to delete'
    }
}

function Start-Vakya {
    if (-not (Test-Path $VenvPy)) { Bad 'Not set up yet'; Fix '.\vakya.ps1 setup'; exit 1 }
    Step 'Starting Vakya'

    if (Test-Alive 'api') {
        Ok ("API already running (pid {0})" -f (Get-PidOf 'api'))
    } elseif (Test-PortBusy $ApiPort) {
        Warn "Port $ApiPort is already in use by another program"
        Fix 'stop that program, or set $env:VAKYA_API_PORT to another port (for example 8012) and start again'
    } else {
        $apiLog = Join-Path $LogDir 'api.log'
        $apiCmd = '"{0}" -m uvicorn backend:app --host 127.0.0.1 --port {1} > "{2}" 2>&1' -f $VenvPy, $ApiPort, $apiLog
        $proc = Start-Hidden -Command $apiCmd -WorkDir $Backend
        Save-Pid -Name 'api' -Proc $proc
        if (Wait-Http -Url "http://127.0.0.1:$ApiPort/api/health" -Seconds 90 -Proc $proc) {
            Ok "API     $ApiUrl"
        } else {
            Bad 'API did not start'
            Fix '.\vakya.ps1 logs api'
        }
    }

    if (Test-Alive 'web') {
        Ok ("Web app already running (pid {0})" -f (Get-PidOf 'web'))
    } elseif (Test-PortBusy $WebPort) {
        Warn "Port $WebPort is already in use by another program"
        Fix 'stop that program, or set $env:VAKYA_WEB_PORT to another port and start again'
    } else {
        Clear-StaleWebCache
        $webLog = Join-Path $LogDir 'web.log'
        # --webpack: Turbopack on Windows fails to resolve next/font/google ("queries have exactly one entry")
        $webCmd = 'npx.cmd next dev --webpack -p {0} > "{1}" 2>&1' -f $WebPort, $webLog
        $proc = Start-Hidden -Command $webCmd -WorkDir $Frontend -Vars @{ NEXT_PUBLIC_API_URL = $ApiUrl }
        Save-Pid -Name 'web' -Proc $proc
        if (Wait-Http -Url "http://127.0.0.1:$WebPort" -Seconds 150 -Proc $proc) {
            Ok "Web app $WebUrl"
        } else {
            Bad 'Web app did not start'
            Fix '.\vakya.ps1 logs web'
        }
    }

    Write-Host ''
    Write-Host "  Open $WebUrl  $Dot  check everything with .\vakya.ps1 doctor  $Dot  stop with .\vakya.ps1 stop"
    Write-Host "  Run .\vakya.ps1 warm before a demo: it checks the keys and prepares the hold lines." -ForegroundColor DarkGray
}

function Invoke-Warm {
    if (-not (Test-Path $VenvPy)) { Bad 'Not set up yet'; Fix '.\vakya.ps1 setup'; exit 1 }
    Step 'Keys'
    Show-Keys
    try { Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/api/health" -TimeoutSec 5 | Out-Null }
    catch { Bad "The API is not running on port $ApiPort"; Fix '.\vakya.ps1 start'; exit 1 }
    Step 'Preparing the hold lines for a live demo'
    $base = "http://127.0.0.1:$ApiPort"
    $headers = @{ 'storage-mode' = 'local' }
    # The warm-up is best effort: a missing endpoint or a failed call is reported, never fatal.
    $state = $null
    try { $state = Invoke-RestMethod -Method Post -Uri "$base/api/warmup" -Headers $headers -TimeoutSec 30 }
    catch {
        $code = 0
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        if ($code -eq 404 -or $code -eq 405) { Warn 'This version of the API has no warm-up step: nothing to prepare' }
        else { Warn ("The warm-up was not run: {0}" -f $_.Exception.Message) }
    }

    $failedCount = 0
    if ($state) {
        $shown = @{}
        $watch = [Diagnostics.Stopwatch]::StartNew()
        while ($true) {
            foreach ($c in @($state.components)) {
                if (-not $c -or $shown.ContainsKey($c.id) -or $c.state -eq 'pending' -or $c.state -eq 'warming') { continue }
                $shown[$c.id] = $true
                $secs = if ($null -ne $c.seconds) { ('{0:N1}s' -f [double]$c.seconds) } else { '' }
                $line = '{0,-32} {1,7}  {2}' -f $c.label, $secs, $c.detail
                if ($c.state -eq 'ready') { Ok $line }
                elseif ($c.state -eq 'skipped') { Warn $line }
                else { Bad $line }
            }
            if ($state.state -ne 'running' -or $watch.Elapsed.TotalSeconds -gt 300) { break }
            Start-Sleep -Seconds 2
            try { $state = Invoke-RestMethod -Uri "$base/api/warmup" -Headers $headers -TimeoutSec 30 }
            catch { Warn ("Lost the warm-up while it was running: {0}" -f $_.Exception.Message); break }
        }
        $failedCount = @(@($state.components) | Where-Object { $_ -and $_.state -eq 'failed' }).Count
    }

    try {  # what is still missing, as the API sees it (this call never spends a Gnani credit)
        $ready = Invoke-RestMethod -Uri "$base/api/ready" -TimeoutSec 8
        foreach ($problem in @($ready.problems)) { if ($problem) { Warn $problem } }
        if ($ready.ok -and @($ready.problems).Count -eq 0) { Ok 'The API reports itself ready' }
    } catch { }

    Write-Host ''
    if ($failedCount -gt 0) {
        Bad ("Partly ready: {0} step(s) failed" -f $failedCount)
        Fix '.\vakya.ps1 logs api'
        exit 1
    }
    Ok 'Done. Run .\vakya.ps1 doctor for the full check.'
}

function Stop-Named {
    param([string]$Name)
    if (Test-Alive $Name) {
        $targetPid = Get-PidOf $Name
        & taskkill.exe /T /F /PID $targetPid 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Ok "Stopped $Name"
        } else {
            Bad "Could not stop $Name (pid $targetPid)"
            Fix 'end that process in Task Manager, then run .\vakya.ps1 stop again'
        }
    } else {
        Write-Host "  $Name was not running"
    }
    Remove-Item -Path (Join-Path $RunDir "$Name.pid") -Force -ErrorAction SilentlyContinue
}

function Stop-Vakya {
    Step 'Stopping Vakya'
    Stop-Named -Name 'web'
    Stop-Named -Name 'api'
}

function Show-Status {
    Step 'Status'
    if (Test-Alive 'api') { Ok ("API running (pid {0}) {1}" -f (Get-PidOf 'api'), $ApiUrl) } else { Write-Host '  API stopped' }
    if (Test-Alive 'web') { Ok ("Web app running (pid {0}) {1}" -f (Get-PidOf 'web'), $WebUrl) } else { Write-Host '  Web app stopped' }
}

function Show-Logs {
    param([string]$Which)
    if (-not $Which) { $Which = 'api' }
    if ($Which -notin @('api', 'web')) {
        Write-Host 'usage: .\vakya.ps1 logs [api|web]'
        exit 1
    }
    $log = Join-Path $LogDir "$Which.log"
    if (-not (Test-Path $log)) {
        Warn "No log yet: $log"
        Fix 'start Vakya first with .\vakya.ps1 start'
        return
    }
    Get-Content -Path $log -Tail 80 -Wait
}

# ------------------------------------------------------------------------------------------- test
function Invoke-Tests {
    if (-not (Test-Path $VenvPy)) { Bad 'Not set up yet'; Fix '.\vakya.ps1 setup'; exit 1 }

    Step 'Backend tests'
    Push-Location $Backend
    & $VenvPy -m pytest -q
    $backendCode = $LASTEXITCODE
    Pop-Location
    if ($backendCode -ne 0) { exit 1 }

    Step 'Frontend tests'
    Push-Location $Frontend
    & npx.cmd jest
    $frontendCode = $LASTEXITCODE
    Pop-Location
    if ($frontendCode -ne 0) { exit 1 }
}

# ------------------------------------------------------------------------------------------- doctor
function Invoke-Doctor {
    Step 'Machine'
    Write-Host ("  Windows {0} ({1})" -f [Environment]::OSVersion.Version, $env:PROCESSOR_ARCHITECTURE)

    $basePy = Find-Python
    if ($basePy) { Ok "Python 3.10+ ($basePy)" } else { Bad 'Python 3.10+ missing'; Fix 'winget install Python.Python.3.12' }
    if (Has 'node') { Ok ("Node {0}" -f (node -v)) } else { Bad 'Node.js missing'; Fix 'winget install OpenJS.NodeJS.LTS' }
    if (Has 'ffmpeg') { Ok 'ffmpeg' } else { Warn 'ffmpeg missing (needed to read uploaded audio that is not plain WAV)'; Fix 'winget install Gyan.FFmpeg' }
    if (Test-Path $VenvPy) { Ok 'backend\.venv' } else { Bad 'backend\.venv missing'; Fix '.\vakya.ps1 setup' }
    if (Test-Path (Join-Path $Frontend 'node_modules')) { Ok 'frontend\node_modules' } else { Bad 'frontend packages missing'; Fix '.\vakya.ps1 setup' }
    if (Test-Path $EnvFile) { Ok 'backend\.env' } else { Warn 'backend\.env missing'; Fix 'copy backend\.env.example backend\.env' }

    Step 'Keys'
    Show-Keys

    Step 'Ports'
    if (Test-Alive 'api') { Ok ("API port {0} in use by the Vakya API (pid {1})" -f $ApiPort, (Get-PidOf 'api')) }
    elseif (Test-PortBusy $ApiPort) { Warn "API port $ApiPort is in use by another program"; Fix 'stop it, or set $env:VAKYA_API_PORT to another port (for example 8012)' }
    else { Ok "API port $ApiPort is free" }
    if (Test-Alive 'web') { Ok ("Web port {0} in use by the Vakya web app (pid {1})" -f $WebPort, (Get-PidOf 'web')) }
    elseif (Test-PortBusy $WebPort) { Warn "Web port $WebPort is in use by another program"; Fix 'stop it, or set $env:VAKYA_WEB_PORT to another port' }
    else { Ok "Web port $WebPort is free" }

    Step 'Running app'
    $health = $null
    try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/api/health" -TimeoutSec 5 } catch { $health = $null }
    if (-not $health) {
        Bad "API is not running on port $ApiPort"
        Fix '.\vakya.ps1 start'
        return
    }
    Ok "API answers on port $ApiPort"

    $llm = $health.llm
    if ($llm -and $llm.active) {
        Ok ("Language model: {0} ({1})" -f $llm.active, $llm.model)
    } elseif ($llm) {
        Warn 'No language model: agents cannot answer'
        Fix 'add LLM_API_KEY, LLM_BASE_URL and LLM_MODEL to backend\.env, then .\vakya.ps1 restart'
    }

    $sb = $health.supabase
    if ($sb -and $sb.configured) {
        if ($sb.reachable) {
            Ok 'Supabase connected'
        } else {
            Warn 'Supabase configured but unreachable'
            Fix 'check NEXT_PUBLIC_SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in backend\.env'
        }
    } else {
        Ok 'Local storage (no Supabase needed; data lives in backend\data)'
    }

    try {
        $ready = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/api/ready" -TimeoutSec 8
        if (@($ready.problems).Count -gt 0) {
            foreach ($problem in @($ready.problems)) { Warn $problem; Fix '.\vakya.ps1 warm prepares the hold lines; the keys go in backend\.env' }
        } else {
            Ok 'Gnani side ready (key set, usage ledger writable, no paused lane)'
        }
    } catch { }
}

# ------------------------------------------------------------------------------------------- help and dispatch
function Show-Help {
    Write-Host @'
Vakya for Windows: one command to set up, start, stop and check the studio.

  .\vakya.ps1 setup            install everything (Python env, npm packages)
  .\vakya.ps1 start            start the API (port 8020) and the web app (port 3010) in the background
  .\vakya.ps1 stop             stop both
  .\vakya.ps1 restart          stop, then start
  .\vakya.ps1 status           what is running
  .\vakya.ps1 doctor           check the machine and the running app, with a fix for every problem
  .\vakya.ps1 logs [api|web]   follow the logs (Ctrl+C to stop)
  .\vakya.ps1 test             run the backend and frontend test suites
  .\vakya.ps1 warm            check the Gnani and language model keys, then prepare the hold lines
  .\vakya.ps1 clean-web        delete frontend\.next (the web build cache) when the web app is not running

If PowerShell blocks the script, run it like this:
  powershell -ExecutionPolicy Bypass -File .\vakya.ps1 setup

Ports: set $env:VAKYA_API_PORT and $env:VAKYA_WEB_PORT before start.
macOS and Linux use ./vakya instead.
'@
}

# Options after the command are ignored; setup and warm take none.
$extra = @($Target) + @($Rest)
if (@('setup', 'warm') -contains $Command.ToLowerInvariant()) {
    $unknown = @($extra | Where-Object { $_ })
    if ($unknown.Count -gt 0) { Bad ("{0} takes no options (got: {1})" -f $Command.ToLowerInvariant(), ($unknown -join ' ')); exit 1 }
    $Target = ''
}

switch ($Command.ToLowerInvariant()) {
    'setup'   { Invoke-Setup }
    'start'   { Start-Vakya }
    'stop'    { Stop-Vakya }
    'restart' { Stop-Vakya; Start-Vakya }
    'status'  { Show-Status }
    'doctor'  { Invoke-Doctor }
    'logs'    { Show-Logs -Which $Target }
    'test'    { Invoke-Tests }
    'warm'    { Invoke-Warm }
    'clean-web' { Invoke-CleanWeb }
    'help'    { Show-Help }
    default   { Show-Help; exit 1 }
}
