param(
    [ValidateSet('help', 'start', 'chat', 'query', 'gpu', 'latency', 'samples', 'pex', 'entry', 'conversation', 'disagreement', 'demo', 'personas', 'sessions', 'deadlocks', 'deadlock-ab', 'counterbench-setup', 'counterbench', 'bbh-setup', 'bbh-check', 'bbh', 'bbh-fallacies', 'bbh-causal', 'test', 'stop')]
    [string]$Task = 'help',
    [string]$Prompt = 'Explain a blackboard architecture in three sentences.',
    [ValidateRange(1, 100)][int]$Runs = 3,
    [ValidateSet('cautious_verifier', 'aggressive_proposer', 'evidence_auditor', 'counterexample_challenger')]
    [string]$Persona = 'cautious_verifier',
    [ValidateRange(0, 5)][int]$Retries = 2,
    [ValidateRange(1, 12)][int]$Turns = 6,
    [ValidateSet(2, 3)][int]$Agents = 2,
    [ValidateSet('pilot', 'extended')][string]$CounterBenchSet = 'pilot',
    [ValidateRange(1, 250)][int]$BbhTasks = 5,
    [ValidateRange(0, 249)][int]$BbhStart = 0,
    [ValidateSet('off', 'on', 'both')][string]$BbhCondition = 'off'
)
$ErrorActionPreference = 'Stop'
$model = 'qwen3:4b-instruct-2507-q4_K_M'
$env:OLLAMA_HOST = 'http://127.0.0.1:11434'
$ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
$ollamaExe = if ($ollamaCommand) { $ollamaCommand.Source } else { Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
if ($Task -eq 'help') {
    Write-Host 'Tasks: start | chat | query [-Prompt "..."] | gpu | latency [-Runs 3] | samples | pex | entry [-Persona cautious_verifier] [-Retries 2] [-Prompt "..."] | conversation [-Agents 2|3] [-Turns 6] [-Prompt "..."] | disagreement [-Turns 6] | demo | personas | sessions | deadlocks | deadlock-ab [-Turns 10] | counterbench-setup | counterbench [-Turns 10] | test | stop'
    Write-Host 'BBH: bbh-setup | bbh-check | bbh | bbh-fallacies | bbh-causal [-BbhTasks 5] [-BbhStart 0] [-BbhCondition off|on|both] [-Turns 12]'
    exit 0
}
$needsOllama = $Task -notin @('test', 'deadlocks', 'counterbench-setup', 'bbh-setup', 'bbh-check')
if ($needsOllama -and -not (Test-Path -LiteralPath $ollamaExe)) { throw 'Install Ollama for Windows first.' }
if ($Task -eq 'start') {
    try {
        Invoke-RestMethod "$env:OLLAMA_HOST/api/version" -TimeoutSec 3 | Out-Host
        Write-Host 'Ollama is already running.'
    } catch {
        $app = Join-Path (Split-Path $ollamaExe) 'ollama app.exe'
        if (Test-Path -LiteralPath $app) { Start-Process -FilePath $app -WindowStyle Hidden }
        else { Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden }
        Write-Host 'Ollama is starting. Run .\run.ps1 query after a few seconds.'
    }
    exit 0
}
if ($needsOllama) {
    try { $null = Invoke-RestMethod "$env:OLLAMA_HOST/api/version" -TimeoutSec 3 }
    catch { throw 'Open Ollama from Start, or run .\run.ps1 start, then try again.' }
}
switch ($Task) {
    'chat' { & $ollamaExe run $model; exit $LASTEXITCODE }
    'stop' { & $ollamaExe stop $model; exit $LASTEXITCODE }
    'gpu' {
        Write-Host 'Windows display adapters (presence alone does not prove inference use):'
        Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion | Format-Table
        Write-Host 'Loaded model placement (run query first if empty):'
        & $ollamaExe ps
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Host '100% GPU = fully loaded on GPU; CPU/GPU = partial offload; 100% CPU = CPU only.'
        Write-Host 'Confirm the selected device is the RX 6800S in the latest load/compute log lines below.'
        $log = Join-Path $env:LOCALAPPDATA 'Ollama\server.log'
        if (Test-Path -LiteralPath $log) {
            Select-String -LiteralPath $log -Pattern 'inference compute|offload|too old|dropping integrated|library=(Vulkan|ROCm|CPU|cpu)|using device|Vulkan[0-9]' |
                Select-Object -Last 20 | ForEach-Object { $_.Line }
        } else {
            Write-Host "Server log not found at $log. Use Ollama's server console if started manually."
        }
        exit 0
    }
}
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) { $pythonExe = $venvPython }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $pythonExe = (Get-Command py).Source }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $pythonExe = (Get-Command python).Source }
else { throw 'Install Python 3.10 or newer, then reopen PowerShell.' }
Push-Location $PSScriptRoot
try {
    if ($Task -eq 'personas') {
        $clientArgs = @('-m', 'pytest', '-q', '-s', '--run-ollama', 'tests/test_personas_live.py')
    } elseif ($Task -eq 'test') {
        $clientArgs = @('-m', 'pytest', '-q')
    } elseif ($Task -eq 'deadlock-ab') {
        $clientArgs = @('-m', 'agents.deadlock_ab', '--turns', $Turns, '--retries', $Retries)
    } elseif ($Task -eq 'counterbench-setup' -or $Task -eq 'counterbench') {
        $clientArgs = @('-m', 'bench.counterbench_pilot', '--case-set', $CounterBenchSet)
        if ($Task -eq 'counterbench-setup') { $clientArgs += '--setup' }
        elseif ($PSBoundParameters.ContainsKey('Turns')) { $clientArgs += @('--turns', $Turns) }
        $clientArgs += @('--retries', $Retries)
    } elseif ($Task -in @('bbh-setup', 'bbh-check', 'bbh', 'bbh-fallacies', 'bbh-causal')) {
        $bbhSuite = if ($Task -eq 'bbh-fallacies') { 'fallacies' } elseif ($Task -eq 'bbh-causal') { 'causal' } else { 'all' }
        $bbhTurns = if ($PSBoundParameters.ContainsKey('Turns')) { $Turns } else { 12 }
        $clientArgs = @('-m', 'bench.bbh_pilot', '--suite', $bbhSuite, '--tasks', $BbhTasks, '--start', $BbhStart, '--condition', $BbhCondition, '--turns', $bbhTurns, '--retries', $Retries)
        if ($Task -eq 'bbh-setup') { $clientArgs += '--setup' }
        if ($Task -eq 'bbh-check') { $clientArgs += '--check' }
    } elseif ($Task -eq 'sessions' -or $Task -eq 'deadlocks') {
        $clientArgs = @('-m', 'agents.session_suite')
        if ($Task -eq 'deadlocks') { $clientArgs += '--deadlock-fixtures' }
        else { $clientArgs += @('--turns', $Turns, '--retries', $Retries) }
    } elseif ($Task -eq 'conversation' -or $Task -eq 'disagreement' -or $Task -eq 'demo') {
        $clientArgs = @('-m', 'agents.conversation', '--turns', $Turns, '--retries', $Retries)
        if ($Task -eq 'demo') { $clientArgs += '--demo' }
        if ($Task -eq 'disagreement') { $clientArgs += @('--scenario', 'disagreement') }
        if ($PSBoundParameters.ContainsKey('Agents')) { $clientArgs += @('--agents', $Agents) }
        if ($PSBoundParameters.ContainsKey('Prompt')) { $clientArgs += @('--prompt', $Prompt) }
    } else {
        $clientArgs = @('-m', 'agents.llm_client', $Task, '--runs', $Runs, '--persona', $Persona, '--retries', $Retries)
        if ($PSBoundParameters.ContainsKey('Prompt')) { $clientArgs += @('--prompt', $Prompt) }
    }
    $previousPythonIoEncoding = $env:PYTHONIOENCODING
    $env:PYTHONIOENCODING = 'ascii:backslashreplace'
    & $pythonExe @clientArgs
    $resultCode = $LASTEXITCODE
} finally {
    if ($null -eq $previousPythonIoEncoding) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }
    else { $env:PYTHONIOENCODING = $previousPythonIoEncoding }
    Pop-Location
}
exit $resultCode
