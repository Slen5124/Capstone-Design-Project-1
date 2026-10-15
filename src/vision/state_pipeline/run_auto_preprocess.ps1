[CmdletBinding()]
param(
    [ValidateSet('Run', 'Smoke')]
    [string]$Mode = 'Run',
    [ValidateSet('zip', 'files')]
    [string]$Layout = 'zip',
    [ValidateRange(0, 2147483647)]
    [int]$Limit = 0,
    [ValidateRange(0, 2147483647)]
    [int]$Start = 0,
    [ValidateRange(1, 8)]
    [int]$Workers = 2,
    [string]$Resume = '',
    [ValidateRange(1, 10000)]
    [int]$CheckpointEvery = 20,
    [ValidateRange(0.1, 60)]
    [double]$ProgressSeconds = 2,
    [ValidateRange(0, 2147483647)]
    [int]$MinFreeMB = 1024,
    [ValidateRange(0, 2147483647)]
    [int]$MaxMinutes = 0,
    [switch]$Menu,
    [switch]$Status,
    [switch]$Watch,
    [switch]$Stop,
    [string]$RunDir = '',
    [switch]$KeepOpen
)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$programPath = Join-Path $PSScriptRoot 'qa_v3\auto_preprocess.py'
$statusPath = Join-Path $PSScriptRoot 'qa_v3\run_status.py'
$workspacePath = Join-Path $PSScriptRoot 'automatic_reference_v3'
$resultsPath = Join-Path $PSScriptRoot 'automatic_results'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw "Python environment is missing: $pythonPath" }
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = '1'

function Invoke-Status {
    param([string]$Target = '', [bool]$Watching = $false, [bool]$Stopping = $false)
    $statusArguments = @($statusPath, '--project-root', $PSScriptRoot)
    if ($Target) { $statusArguments += @('--run', $Target) }
    if ($Watching) { $statusArguments += '--watch' }
    if ($Stopping) { $statusArguments += '--stop' }
    & $pythonPath -X utf8 @statusArguments | Out-Host
    return $LASTEXITCODE
}

function Invoke-Batch {
    param([string]$RunMode, [string]$OutputLayout, [int]$PhotoLimit,
          [int]$PhotoStart, [string]$ResumePath)
    $qaArguments = @($programPath, '--workspace', $workspacePath, '--out', $resultsPath,
        '--workers', "$Workers", '--progress-seconds', $ProgressSeconds.ToString([Globalization.CultureInfo]::InvariantCulture),
        '--min-free-mb', "$MinFreeMB", '--max-minutes', "$MaxMinutes")
    if ($ResumePath) {
        if (-not (Test-Path -LiteralPath $ResumePath -PathType Container)) { throw "Resume folder is missing: $ResumePath" }
        $qaArguments += @('--resume', $ResumePath)
    } else {
        $qaArguments += @('--layout', $OutputLayout, '--start', "$PhotoStart", '--checkpoint-every', "$CheckpointEvery")
        if ($RunMode -eq 'Smoke') {
            if ($PhotoLimit -gt 0 -or $PhotoStart -gt 0) { throw 'Smoke uses the fixed 13-photo set; do not combine it with Limit or Start.' }
            $qaArguments += @('--entries-file', (Join-Path $workspacePath 'validation_entries.json'))
        } elseif ($PhotoLimit -gt 0) {
            $qaArguments += @('--limit', "$PhotoLimit")
        }
    }
    Write-Host '실행 중인 창에서 진행률, 속도, 예상 남은 시간, 분류 수를 확인할 수 있습니다.'
    Write-Host '암호는 입력 프롬프트 또는 QA_ZIP_PASSWORD에서 받으며 파일에 저장하지 않습니다.'
    # Python emits live progress; never capture its output or persist the password.
    & $pythonPath -X utf8 @qaArguments | Out-Host
    return $LASTEXITCODE
}

if ($Status -or $Stop) {
    $resultCode = Invoke-Status -Target $RunDir -Watching ([bool]$Watch) -Stopping ([bool]$Stop)
    if ($KeepOpen) { [void](Read-Host '창을 닫으려면 Enter') }
    exit $resultCode
}
if ($Watch -or $RunDir) { throw 'Watch and RunDir require Status or Stop.' }
if ($Resume -and ($PSBoundParameters.ContainsKey('Mode') -or $PSBoundParameters.ContainsKey('Layout') -or $PSBoundParameters.ContainsKey('CheckpointEvery') -or $Limit -gt 0 -or $Start -gt 0)) {
    throw 'Resume restores the saved selection/layout/checkpoint; do not combine it with Mode, Layout, Limit, Start, or CheckpointEvery.'
}

if (-not $Menu) {
    $resultCode = Invoke-Batch -RunMode $Mode -OutputLayout $Layout -PhotoLimit $Limit -PhotoStart $Start -ResumePath $Resume
    if ($KeepOpen) { [void](Read-Host '창을 닫으려면 Enter') }
    exit $resultCode
}

while ($true) {
    Write-Host ''
    Write-Host '자동 전처리 실행 메뉴'
    Write-Host '1. 대표 13장 실행'
    Write-Host '2. 처음 100장 실행'
    Write-Host '3. 전체 실행'
    Write-Host '4. 중단한 실행 이어하기'
    Write-Host '5. 최근 실행 상태 보기'
    Write-Host '6. 진행 중인 실행 안전 중단 요청'
    Write-Host '7. 지정 구간 실행'
    Write-Host '0. 종료'
    $choice = Read-Host '번호'
    if ($choice -eq '0') { exit 0 }
    try {
        switch ($choice) {
            '1' { $resultCode = Invoke-Batch -RunMode Smoke -OutputLayout zip -PhotoLimit 0 -PhotoStart 0 -ResumePath '' }
            '2' { $resultCode = Invoke-Batch -RunMode Run -OutputLayout zip -PhotoLimit 100 -PhotoStart 0 -ResumePath '' }
            '3' { $resultCode = Invoke-Batch -RunMode Run -OutputLayout zip -PhotoLimit 0 -PhotoStart 0 -ResumePath '' }
            '4' {
                $resumeFolder = (Read-Host '이어할 run 폴더의 전체 경로').Trim().Trim('"')
                $resultCode = Invoke-Batch -RunMode Run -OutputLayout zip -PhotoLimit 0 -PhotoStart 0 -ResumePath $resumeFolder
            }
            '5' { $resultCode = Invoke-Status }
            '6' { $resultCode = Invoke-Status -Stopping $true }
            '7' {
                $startText = Read-Host '건너뛸 사진 수 (처음부터면 0)'
                $limitText = Read-Host '이번에 처리할 사진 수'
                $rangeStart = 0; $rangeLimit = 0
                if (-not [int]::TryParse($startText, [ref]$rangeStart) -or $rangeStart -lt 0 -or
                    -not [int]::TryParse($limitText, [ref]$rangeLimit) -or $rangeLimit -lt 1) { throw '0 이상의 시작 위치와 1 이상의 사진 수를 입력하세요.' }
                $resultCode = Invoke-Batch -RunMode Run -OutputLayout zip -PhotoLimit $rangeLimit -PhotoStart $rangeStart -ResumePath ''
            }
            default { Write-Host '메뉴 번호를 입력하세요.'; continue }
        }
        if ($resultCode -ne 0) { Write-Host "실행 종료 코드: $resultCode. 진행 파일과 결과 폴더를 확인하세요." }
    } catch {
        Write-Host ("실행 오류: " + $_.Exception.Message)
    }
    [void](Read-Host '메뉴로 돌아가려면 Enter')
}
