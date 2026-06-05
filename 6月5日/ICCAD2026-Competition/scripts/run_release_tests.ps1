param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseRoot,

    [string]$Config = "config.yaml",

    [string[]]$Cases = @(),

    [string]$OutDir = "run_outputs",

    [int]$CaseTimeoutSeconds = 1500,

    [switch]$OnlyFailed,

    [switch]$CleanOutput
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$mainPy = Join-Path $repoRoot "main.py"
$configPath = Resolve-Path -LiteralPath (Join-Path $repoRoot $Config)
$releasePath = Resolve-Path -LiteralPath $ReleaseRoot
$caseRoot = Join-Path $releasePath "testcase"
$outputRoot = Join-Path $repoRoot $OutDir
$summaryPath = Join-Path $outputRoot "summary.csv"
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

function Get-FailureType {
    param(
        [string]$Status,
        [string]$Stdout,
        [string]$Stderr
    )
    if ($Status -eq "ok") { return "" }
    if ($Status -eq "timeout") { return "TIMEOUT" }
    if ($Status -match "^exit") { return "PROCESS_EXIT" }
    if ($Status -match "^missing prompt") { return "MISSING_PROMPT" }
    if ($Status -match "^error:") { return "RUNNER_ERROR" }
    if ($Stdout -match "FAIL\[([^\]]+)\]") { return "APP_$($Matches[1])" }
    if ($Stdout -match "UNKNOWN\[([^\]]+)\]") { return "APP_UNKNOWN_$($Matches[1])" }
    if ($Stdout -match "(?is)<!doctype html|<html\b") { return "LLM_HTML_RESPONSE" }
    if ($Stdout -match "Internal error processing request") { return "APP_INTERNAL_ERROR" }
    if ($Stdout -match "Agent exceeded maximum tool-call rounds") { return "AGENT_MAX_ROUNDS" }
    if ($Stdout -match "Error code:\s*4\d\d|pre_consume_token_quota_failed") { return "LLM_API_ERROR" }
    if ($Stderr -match "Traceback") { return "PYTHON_TRACEBACK" }
    if ($Stderr -match "Error code:\s*4\d\d|pre_consume_token_quota_failed") { return "LLM_API_ERROR" }
    return "FAILED"
}

function Test-NoOptimizationEffect {
    param(
        [string]$Prompt,
        [string]$Stdout
    )
    $hasTransformRequest = $Prompt -match "(?i)\b(insert|optimi[sz]e|simplif|propagat|remove|prune|merge|convert|replace|buffer|remap|reduce|minimi[sz]e|cleanup|collapse|eliminate|rewrite|restructure)\b"
    if (-not $hasTransformRequest) { return $false }

    $hasPositiveTransform = $Stdout -match "(?i)(Eliminated|Removed|Inserted|Converted|Merged|Collapsed)\s+[1-9]\d*\b|Removed dangling gates:\s*[1-9]\d*\b|Merged gates:\s*[1-9]\d*\b|Dangling:0\s+\(was\s+[1-9]\d*\)|dangling=\s*[1-9]\d*\b|DupM:[1-9]\d*\b|FNB:[1-9]\d*\b|CNN:[1-9]\d*\b|ConstProp:\s*[1-9]\d*\b|Buf\w*[^:]*:\s*[1-9]\d*\s+inserted|XOR->NAND:\s*[1-9]\d*\b|XNOR->NOR:\s*[1-9]\d*\b|OR->NAND[^:]*:\s*[1-9]\d*\b|Replace\w*[^:]*:\s*[1-9]\d*\b|Remap\b.*=\s*[1-9]\d*\b|buf_added:\s*[1-9]\d*\b|constant_gates_eliminated:\s*[1-9]\d*\b|dangling_removed:\s*[1-9]\d*\b|merged_gates:\s*[1-9]\d*\b|nand_added:\s*[1-9]\d*\b|nor_added:\s*[1-9]\d*\b|not_not_collapsed:\s*[1-9]\d*\b"
    if ($hasPositiveTransform) { return $false }

    $noEffectMarkers = @(
        "(?i)design unchanged",
        "(?i)already satisfies .*depth bound",
        "(?i)already optimized",
        "(?i)contains no combinational gates to optimize",
        "(?i)No .* gates? found.*design unchanged",
        "(?i)No .* pairs? found.*design unchanged",
        "(?i)No .* simplifiable .* found.*design unchanged"
    )
    $zeroCountMarkers = @(
        "(?i)constant_gates_eliminated:\s*0\b",
        "(?i)dangling_removed:\s*0\b",
        "(?i)merged_gates:\s*0\b",
        "(?i)buf_added:\s*0\b",
        "(?i)not_not_collapsed:\s*0\b"
    )
    foreach ($pattern in $noEffectMarkers) {
        if ($Stdout -match $pattern) { return $true }
    }
    if (-not $hasPositiveTransform) {
        foreach ($pattern in $zeroCountMarkers) {
            if ($Stdout -match $pattern) { return $true }
        }
    }
    return $false
}

function Get-TokenUsage {
    param([string]$Stderr)
    $usage = [pscustomobject]@{
        PromptTokens = 0
        CompletionTokens = 0
        Tokens = 0
    }
    if ($Stderr -match "TOKEN_USAGE\s+prompt=(\d+)\s+completion=(\d+)\s+total=(\d+)") {
        $usage.PromptTokens = [int]$Matches[1]
        $usage.CompletionTokens = [int]$Matches[2]
        $usage.Tokens = [int]$Matches[3]
    }
    return $usage
}

function Get-PromptRequestCount {
    param([string]$Prompt)
    return @(
        Get-Content -LiteralPath $Prompt |
            Where-Object { $_.Trim().Length -gt 0 }
    ).Count
}

function Get-RunObservability {
    param([string]$Stdout)
    $responses = [regex]::Matches($Stdout, "(?m)^#RESPONSE\s+(\d+)\s*$")
    $ends = [regex]::Matches($Stdout, "(?m)^#END\s+(\d+)\s*$")
    $lastId = 0
    foreach ($m in $responses) {
        $id = [int]$m.Groups[1].Value
        if ($id -gt $lastId) { $lastId = $id }
    }
    return [pscustomobject]@{
        ResponseCount = $responses.Count
        EndCount = $ends.Count
        LastResponseId = $lastId
        UnknownCount = ([regex]::Matches($Stdout, "UNKNOWN\[")).Count
        FailCount = ([regex]::Matches($Stdout, "FAIL\[")).Count
        InternalErrorCount = ([regex]::Matches($Stdout, "Internal error processing request")).Count
    }
}

function Save-Summary {
    $summary = $summaryByCase.Values |
        Sort-Object @{ Expression = {
            if ($_.Case -match '^test(\d+)$') { [int]$Matches[1] } else { [int]::MaxValue }
        } }, Case
    $summary | Export-Csv -NoTypeInformation -Encoding UTF8 -Path $summaryPath
    return $summary
}

if ($Cases.Count -eq 0) {
    $Cases = Get-ChildItem -Directory -LiteralPath $caseRoot |
        Sort-Object Name |
        ForEach-Object { $_.Name }
}
else {
    $Cases = $Cases |
        ForEach-Object { $_ -split "," } |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
}

$summaryByCase = @{}
if (Test-Path -LiteralPath $summaryPath) {
    Import-Csv -LiteralPath $summaryPath | ForEach-Object {
        if ($_.Case) {
            $summaryByCase[$_.Case] = [pscustomobject]@{
                Case = $_.Case
                Status = $_.Status
                Seconds = $_.Seconds
                LastRun = $_.LastRun
                FailureType = $_.FailureType
                Tokens = if ($_.Tokens) { $_.Tokens } else { 0 }
                PromptTokens = if ($_.PromptTokens) { $_.PromptTokens } else { 0 }
                CompletionTokens = if ($_.CompletionTokens) { $_.CompletionTokens } else { 0 }
                RequestCount = if ($_.RequestCount) { $_.RequestCount } else { 0 }
                ResponseCount = if ($_.ResponseCount) { $_.ResponseCount } else { 0 }
                EndCount = if ($_.EndCount) { $_.EndCount } else { 0 }
                LastResponseId = if ($_.LastResponseId) { $_.LastResponseId } else { 0 }
                Complete = if ($_.Complete) { $_.Complete } else { "" }
                UnknownCount = if ($_.UnknownCount) { $_.UnknownCount } else { 0 }
                FailCount = if ($_.FailCount) { $_.FailCount } else { 0 }
                InternalErrorCount = if ($_.InternalErrorCount) { $_.InternalErrorCount } else { 0 }
            }
        }
    }
}

if ($OnlyFailed) {
    $Cases = $Cases | Where-Object {
        $prev = $summaryByCase[$_]
        $null -eq $prev -or $prev.Status -ne "ok"
    }
}

foreach ($case in $Cases) {
    $prompt = Join-Path (Join-Path $caseRoot $case) "prompt.txt"
    if (!(Test-Path -LiteralPath $prompt)) {
        $summaryByCase[$case] = [pscustomobject]@{
            Case = $case
            Status = "missing prompt"
            Seconds = 0
            LastRun = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
            FailureType = "MISSING_PROMPT"
            Tokens = 0
            PromptTokens = 0
            CompletionTokens = 0
            RequestCount = 0
            ResponseCount = 0
            EndCount = 0
            LastResponseId = 0
            Complete = "false"
            UnknownCount = 0
            FailCount = 0
            InternalErrorCount = 0
        }
        Save-Summary | Out-Null
        continue
    }

    $requestCount = Get-PromptRequestCount -Prompt $prompt
    $stdout = Join-Path $outputRoot "$case.stdout.txt"
    $stderr = Join-Path $outputRoot "$case.stderr.txt"
    if ($CleanOutput) {
        Remove-Item -LiteralPath $stdout, $stderr -ErrorAction SilentlyContinue
    }
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $failureType = ""
    $tokenUsage = [pscustomobject]@{
        PromptTokens = 0
        CompletionTokens = 0
        Tokens = 0
    }

    Push-Location $releasePath
    try {
        $job = Start-Job -ScriptBlock {
            param($Prompt, $MainPy, $ConfigPath, $ReleasePath, $Stdout, $Stderr)
            Set-Location -LiteralPath $ReleasePath
            $env:PYTHONIOENCODING = "utf-8"
            $env:PYTHONUTF8 = "1"
            Get-Content -LiteralPath $Prompt |
                python $MainPy -config $ConfigPath 1> $Stdout 2> $Stderr
            return $LASTEXITCODE
        } -ArgumentList $prompt, $mainPy, $configPath.Path, $releasePath.Path, $stdout, $stderr

        $completed = Wait-Job -Job $job -Timeout $CaseTimeoutSeconds
        if ($null -eq $completed) {
            Stop-Job -Job $job
            Remove-Job -Job $job -Force
            $status = "timeout"
        }
        else {
            $exitCode = Receive-Job -Job $job
            Remove-Job -Job $job
            if ($exitCode -ne 0) {
                $status = "exit $exitCode"
            }
            else {
                $outText = Get-Content -Raw -LiteralPath $stdout
                $errText = Get-Content -Raw -LiteralPath $stderr
                $observability = Get-RunObservability -Stdout $outText
                $complete = (
                    $observability.ResponseCount -eq $requestCount -and
                    $observability.EndCount -eq $requestCount -and
                    $observability.LastResponseId -eq $requestCount
                )
                if (-not $complete) {
                    $status = "failed"
                    $failureType = "RESPONSE_MISMATCH"
                }
                elseif ($outText -match "FAIL\[|UNKNOWN\[|Internal error processing request|Error code:\s*4\d\d|pre_consume_token_quota_failed|Agent exceeded maximum tool-call rounds|(?is)<!doctype html|<html\b" -or
                    $errText -match "Traceback|Error code:\s*4\d\d|pre_consume_token_quota_failed") {
                    $status = "failed"
                }
                else {
                    $status = "ok"
                }
                $failureType = Get-FailureType -Status $status -Stdout $outText -Stderr $errText
                $tokenUsage = Get-TokenUsage -Stderr $errText
            }
        }
    }
    catch {
        $status = "error: $($_.Exception.Message)"
        $failureType = "RUNNER_ERROR"
    }
    finally {
        Pop-Location
        $timer.Stop()
    }

    if (-not $failureType) {
        $outText = if (Test-Path -LiteralPath $stdout) { Get-Content -Raw -LiteralPath $stdout } else { "" }
        $errText = if (Test-Path -LiteralPath $stderr) { Get-Content -Raw -LiteralPath $stderr } else { "" }
        $failureType = Get-FailureType -Status $status -Stdout $outText -Stderr $errText
        $tokenUsage = Get-TokenUsage -Stderr $errText
    }
    $outText = if (Test-Path -LiteralPath $stdout) { Get-Content -Raw -LiteralPath $stdout } else { "" }
    $observability = Get-RunObservability -Stdout $outText
    $complete = (
        $observability.ResponseCount -eq $requestCount -and
        $observability.EndCount -eq $requestCount -and
        $observability.LastResponseId -eq $requestCount
    )
    if ($status -eq "ok" -and -not $complete) {
        $status = "failed"
        $failureType = "RESPONSE_MISMATCH"
    }
    if ($status -eq "ok") {
        $promptText = Get-Content -Raw -LiteralPath $prompt
        if (Test-NoOptimizationEffect -Prompt $promptText -Stdout $outText) {
            $status = "failed"
            $failureType = "NO_OPT_EFFECT"
        }
    }

    $summaryByCase[$case] = [pscustomobject]@{
        Case = $case
        Status = $status
        Seconds = [math]::Round($timer.Elapsed.TotalSeconds, 2)
        LastRun = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        FailureType = $failureType
        Tokens = $tokenUsage.Tokens
        PromptTokens = $tokenUsage.PromptTokens
        CompletionTokens = $tokenUsage.CompletionTokens
        RequestCount = $requestCount
        ResponseCount = $observability.ResponseCount
        EndCount = $observability.EndCount
        LastResponseId = $observability.LastResponseId
        Complete = if ($complete) { "true" } else { "false" }
        UnknownCount = $observability.UnknownCount
        FailCount = $observability.FailCount
        InternalErrorCount = $observability.InternalErrorCount
    }
    Save-Summary | Out-Null
}

$summary = Save-Summary

$summary | Format-Table -AutoSize
