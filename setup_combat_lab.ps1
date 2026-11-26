[CmdletBinding()]
param(
    [string]$Sts2Dir,
    [string]$RitsuWorkshopRoot,
    [string]$RitsuLibDir,
    [string]$GdreTools,
    # Resolved in the body: Windows PowerShell 5.1 leaves $PSScriptRoot empty here
    # when the script is started with `powershell -File` and arguments.
    [string]$CombatSolverDir,
    [switch]$SkipArtwork
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$script:nativeExitCode = 0

function Resolve-Existing([string]$Value, [string]$Label) {
    if (-not $Value) { throw "$Label is required." }
    $resolved = (Resolve-Path -LiteralPath $Value -ErrorAction Stop).Path
    return $resolved
}

# Windows PowerShell 5.1 reports anything a native command writes to stderr as an
# error. git, pip, npm and dotnet all use stderr for ordinary messages, and the
# patch check below relies on a command that is expected to fail, so every native
# call goes through here: stderr is collected instead of shown, and the helper only
# throws when the exit code is non-zero and the caller did not allow a failure.
function Invoke-Native([string]$Program, [string[]]$Arguments, [switch]$AllowFailure) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try {
        $lines = & $Program @Arguments 2>&1 | ForEach-Object { "$_" }
        $script:nativeExitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    if ($script:nativeExitCode -ne 0 -and -not $AllowFailure) {
        throw "$Program $($Arguments -join ' ') failed with exit code $script:nativeExitCode.`n$($lines -join "`n")"
    }
    return ($lines -join "`n")
}

# A project virtualenv is used when present. Otherwise, on Windows `python` can
# resolve to the Microsoft Store alias stub, which exits without running
# anything, so prefer the py launcher and keep only an interpreter that works.
$pythonExe = 'python'
$pythonArguments = @()
$venvPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (Test-Path -LiteralPath $venvPython) {
    $pythonExe = $venvPython
}
else {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        Invoke-Native py @('-3', '-c', 'import sys') -AllowFailure
        if ($script:nativeExitCode -eq 0) { $pythonExe = 'py'; $pythonArguments = @('-3') }
    }
    if ($pythonExe -eq 'python') {
        if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python 3.11+ is required.' }
        Invoke-Native python @('-c', 'import sys') -AllowFailure
        if ($script:nativeExitCode -ne 0) { throw 'Python 3.11+ is required.' }
    }
}
function Invoke-Python([string[]]$Arguments) {
    return Invoke-Native $pythonExe ($pythonArguments + $Arguments)
}

# upstream.lock.json is the single source of truth for the solver revision, the
# game data version and the RitsuLib reference target this build was ported to.
$lock = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'upstream.lock.json') -Raw | ConvertFrom-Json
$solverUrl = [string]$lock.combatSolver.repository
$solverCommit = [string]$lock.combatSolver.commit
$gameVersion = [string]$lock.gameVersion
$ritsuTarget = [string]$lock.ritsuLibReferenceTarget

if (-not $CombatSolverDir) { $CombatSolverDir = Join-Path $PSScriptRoot 'CombatSolver' }
if (-not $Sts2Dir) {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Steam/steamapps/common/Slay the Spire 2'),
        (Join-Path $env:ProgramFiles 'Steam/steamapps/common/Slay the Spire 2'),
        'D:/Steam/steamapps/common/Slay the Spire 2'
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    $Sts2Dir = $candidates | Select-Object -First 1
}
$Sts2Dir = Resolve-Existing $Sts2Dir 'Sts2Dir'
$Sts2Pck = Join-Path $Sts2Dir 'SlayTheSpire2.pck'
$Sts2DataDir = Join-Path $Sts2Dir 'data_sts2_windows_x86_64'
if (-not (Test-Path -LiteralPath $Sts2Pck)) { throw "Missing $Sts2Pck" }
if (-not (Test-Path -LiteralPath (Join-Path $Sts2DataDir 'sts2.dll'))) { throw "Missing $Sts2DataDir/sts2.dll" }

# Generated data is tied to the installed game. A different version usually still
# builds, but the catalog and native card values must then be regenerated for it.
$releaseInfo = Join-Path $Sts2Dir 'release_info.json'
if (Test-Path -LiteralPath $releaseInfo) {
    $installed = [string]((Get-Content -LiteralPath $releaseInfo -Raw | ConvertFrom-Json).version)
    $installed = $installed.TrimStart('v')
    if ($installed -ne $gameVersion) {
        Write-Warning "Installed game is $installed; Combat Lab was ported to game data $gameVersion. Re-run setup after reviewing the differences."
    }
}

if (-not $RitsuWorkshopRoot) {
    $RitsuWorkshopRoot = Join-Path ${env:ProgramFiles(x86)} 'Steam/steamapps/workshop/content/2868840/3747602295'
}
$RitsuWorkshopRoot = Resolve-Existing $RitsuWorkshopRoot 'RitsuWorkshopRoot'
if (-not $RitsuLibDir) { $RitsuLibDir = Join-Path $RitsuWorkshopRoot "compat/$ritsuTarget" }
$RitsuLibDir = Resolve-Existing $RitsuLibDir 'RitsuLibDir'
if (-not $GdreTools) { $GdreTools = Join-Path $PSScriptRoot 'tools/gdre_tools.exe' }
$GdreTools = Resolve-Existing $GdreTools 'GdreTools'

foreach ($tool in @('git', 'dotnet', 'npm')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool is required." }
}

$solverPath = (Resolve-Path -LiteralPath $CombatSolverDir -ErrorAction SilentlyContinue).Path
if (-not $solverPath) {
    Invoke-Native git @('clone', $solverUrl, $CombatSolverDir) | Out-Null
    $solverPath = (Resolve-Path -LiteralPath $CombatSolverDir).Path
}
if (-not (Test-Path -LiteralPath (Join-Path $solverPath '.git'))) { throw "$solverPath is not a git checkout." }
$head = (Invoke-Native git @('-C', $solverPath, 'rev-parse', 'HEAD')).Trim()
if ($head -ne $solverCommit) {
    throw "CombatSolver is at $head; expected pinned $solverCommit. Use a clean checkout of the pinned revision."
}
$patch = Join-Path $PSScriptRoot 'patches/combat-solver-combat-lab.patch'
$alreadyApplied = $false
Invoke-Native git @('-C', $solverPath, 'apply', '--check', $patch) -AllowFailure
if ($script:nativeExitCode -ne 0) {
    Invoke-Native git @('-C', $solverPath, 'apply', '--reverse', '--check', $patch) -AllowFailure
    if ($script:nativeExitCode -eq 0) { $alreadyApplied = $true }
    else { throw 'CombatSolver has local changes that conflict with the Combat Lab patch.' }
}
if (-not $alreadyApplied) {
    Invoke-Native git @('-C', $solverPath, 'apply', $patch)
}

$props = @"
<Project>
  <PropertyGroup>
    <SteamRoot>$((Split-Path (Split-Path (Split-Path $Sts2Dir -Parent) -Parent) -Parent).Replace('&','&amp;'))</SteamRoot>
    <Sts2Dir>$($Sts2Dir.Replace('&','&amp;'))</Sts2Dir>
    <Sts2DataDir>$($Sts2DataDir.Replace('&','&amp;'))</Sts2DataDir>
    <RitsuWorkshopRoot>$($RitsuWorkshopRoot.Replace('&','&amp;'))</RitsuWorkshopRoot>
    <RitsuLibDir>$($RitsuLibDir.Replace('&','&amp;'))</RitsuLibDir>
    <RitsuLibReferenceTarget>$ritsuTarget</RitsuLibReferenceTarget>
    <CopyModOnBuild>false</CopyModOnBuild>
  </PropertyGroup>
</Project>
"@
Set-Content -LiteralPath (Join-Path $solverPath 'local.props') -Value $props -Encoding utf8
$env:GDRE_TOOLS = $GdreTools

Invoke-Python @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $PSScriptRoot 'requirements.txt')) | Out-Null
$art = Join-Path $PSScriptRoot 'tools/combat_lab_art'
Push-Location $art
try { Invoke-Native npm @('ci', '--no-audit', '--no-fund') | Out-Null } finally { Pop-Location }

# CopyModOnBuild=false keeps the build out of the game's mods directory: this
# harness runs beside the game and must not replace the installed mod.
Invoke-Native dotnet @('build', (Join-Path $solverPath 'CombatSolver.csproj'), '-c', 'Release', '-p:CopyModOnBuild=false') | Out-Null
Write-Host 'Built CombatSolver'
$harness = Join-Path $solverPath '.local/tool-build/OfflineSearchHarness/bin/Release/net9.0/OfflineSearchHarness.dll'
Invoke-Native dotnet @('build', (Join-Path $solverPath 'tools/search/OfflineSearchHarness/OfflineSearchHarness.csproj'), '-c', 'Release') | Out-Null
Write-Host 'Built OfflineSearchHarness'

# The catalog schema follows the installed game: model lists and image paths are
# read from it, so cards and encounters added by an update appear automatically.
Write-Host (Invoke-Python @((Join-Path $art 'build_schema.py'), '--pck', $Sts2Pck, '--harness', $harness))
$catalogArguments = @((Join-Path $art 'catalog.py'), '--pck', $Sts2Pck, '--harness', $harness)
if ($SkipArtwork) { $catalogArguments += '--metadata-only' }
Write-Host (Invoke-Python $catalogArguments)
Write-Host "Combat Lab is ready at http://127.0.0.1:8765/"
Write-Host "Run .\run_combat_lab_web.cmd to start the local web service."
