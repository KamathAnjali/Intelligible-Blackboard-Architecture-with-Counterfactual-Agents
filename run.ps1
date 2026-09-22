param(
    [ValidateSet('help', 'start', 'chat', 'query', 'gpu', 'latency', 'samples', 'pex', 'entry', 'personas', 'test', 'stop')]
    [string]$Task = 'help',
    [string]$Prompt = 'Explain a blackboard architecture in three sentences.',
    [ValidateRange(1, 100)][int]$Runs = 3,
    [ValidateSet('cautious_verifier', 'aggressive_proposer')]
    [string]$Persona = 'cautious_verifier',
    [ValidateRange(0, 5)][int]$Retries = 2
)
$ErrorActionPreference = 'Stop'
$model = 'qwen3:4b-instruct-2507-q4_K_M'
$env:OLLAMA_HOST = 'http://127.0.0.1:11434'
$ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
$ollamaExe = if ($ollamaCommand) { $ollamaCommand.Source } else { Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
if ($Task -eq 'help') {
    Write-Host 'Tasks: start | chat | query [-Prompt "..."] | gpu | latency [-Runs 3] | samples | pex | entry [-Persona cautious_verifier] [-Retries 2] [-Prompt "..."] | personas | test | stop'
    exit 0
}
if ($Task -ne 'test' -and -not (Test-Path -LiteralPath $ollamaExe)) { throw 'Install Ollama for Windows first.' }
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
if ($Task -ne 'test') {
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
    } else {
        $clientArgs = @('-m', 'agents.llm_client', $Task, '--runs', $Runs, '--persona', $Persona, '--retries', $Retries)
        if ($PSBoundParameters.ContainsKey('Prompt')) { $clientArgs += @('--prompt', $Prompt) }
    }
    & $pythonExe @clientArgs
    $resultCode = $LASTEXITCODE
} finally { Pop-Location }
exit $resultCode
