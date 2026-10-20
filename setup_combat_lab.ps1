[CmdletBinding()]
param(
    [string]$Sts2Dir,
    [string]$RitsuWorkshopRoot,
    [string]$RitsuLibDir,
    [string]$GdreTools,
    [string]$CombatSolverDir = (Join-Path $PSScriptRoot 'CombatSolver'),
    [switch]$SkipArtwork
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-Existing([string]$Value, [string]$Label) {
    if (-not $Value) { throw "$Label is required." }
    $resolved = (Resolve-Path -LiteralPath $Value -ErrorAction Stop).Path
    return $resolved
}

if (-not $Sts2Dir) {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Steam/steamapps/common/Slay the Spire 2'),
        (Join-Path $env:ProgramFiles 'Steam/steamapps/common/Slay the Spire 2')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    $Sts2Dir = $candidates | Select-Object -First 1
}
$Sts2Dir = Resolve-Existing $Sts2Dir 'Sts2Dir'
$Sts2Pck = Join-Path $Sts2Dir 'SlayTheSpire2.pck'
$Sts2DataDir = Join-Path $Sts2Dir 'data_sts2_windows_x86_64'
if (-not (Test-Path -LiteralPath $Sts2Pck)) { throw "Missing $Sts2Pck" }
if (-not (Test-Path -LiteralPath (Join-Path $Sts2DataDir 'sts2.dll'))) { throw "Missing $Sts2DataDir/sts2.dll" }

if (-not $RitsuWorkshopRoot) {
    $RitsuWorkshopRoot = Join-Path ${env:ProgramFiles(x86)} 'Steam/steamapps/workshop/content/2868840/3747602295'
}
$RitsuWorkshopRoot = Resolve-Existing $RitsuWorkshopRoot 'RitsuWorkshopRoot'
if (-not $RitsuLibDir) { $RitsuLibDir = Join-Path $RitsuWorkshopRoot 'lib/0.111.0' }
$RitsuLibDir = Resolve-Existing $RitsuLibDir 'RitsuLibDir'
if (-not $GdreTools) { $GdreTools = Join-Path $PSScriptRoot 'tools/gdre_tools.exe' }
$GdreTools = Resolve-Existing $GdreTools 'GdreTools'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'git is required.' }
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { throw '.NET 9 SDK is required.' }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python 3.11+ is required.' }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'Node.js/npm is required for the artwork renderer.' }

$solverUrl = 'https://github.com/Torch1230/CombatSolver.git'
$solverCommit = '4533f6bb8c9b049aa27693e38da0f2854b31dd36'
$solverPath = (Resolve-Path -LiteralPath $CombatSolverDir -ErrorAction SilentlyContinue).Path
if (-not $solverPath) {
    git clone $solverUrl $CombatSolverDir
    $solverPath = (Resolve-Path -LiteralPath $CombatSolverDir).Path
}
if (-not (Test-Path -LiteralPath (Join-Path $solverPath '.git'))) { throw "$solverPath is not a git checkout." }
$head = (git -C $solverPath rev-parse HEAD).Trim()
if ($head -ne $solverCommit) {
    throw "CombatSolver is at $head; expected pinned $solverCommit. Use a clean checkout of the pinned revision."
}
$patch = Join-Path $PSScriptRoot 'patches/combat-solver-combat-lab.patch'
$alreadyApplied = $false
git -C $solverPath apply --check $patch 2>$null
if ($LASTEXITCODE -ne 0) {
    git -C $solverPath apply --reverse --check $patch 2>$null
    if ($LASTEXITCODE -eq 0) { $alreadyApplied = $true }
    else { throw 'CombatSolver has local changes that conflict with the Combat Lab patch.' }
}
if (-not $alreadyApplied) { git -C $solverPath apply $patch }

$props = @"
<Project>
  <PropertyGroup>
    <SteamRoot>$((Split-Path (Split-Path (Split-Path $Sts2Dir -Parent) -Parent) -Parent).Replace('&','&amp;'))</SteamRoot>
    <Sts2Dir>$($Sts2Dir.Replace('&','&amp;'))</Sts2Dir>
    <Sts2DataDir>$($Sts2DataDir.Replace('&','&amp;'))</Sts2DataDir>
    <RitsuWorkshopRoot>$($RitsuWorkshopRoot.Replace('&','&amp;'))</RitsuWorkshopRoot>
    <RitsuLibDir>$($RitsuLibDir.Replace('&','&amp;'))</RitsuLibDir>
    <CopyModOnBuild>false</CopyModOnBuild>
  </PropertyGroup>
</Project>
"@
Set-Content -LiteralPath (Join-Path $solverPath 'local.props') -Value $props -Encoding utf8
$env:GDRE_TOOLS = $GdreTools

python -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
Push-Location (Join-Path $PSScriptRoot 'tools/combat_lab_art')
try { npm ci } finally { Pop-Location }

dotnet build (Join-Path $solverPath 'CombatSolver.csproj') -c Release -p:CopyModOnBuild=false
dotnet build (Join-Path $solverPath 'tools/search/OfflineSearchHarness/OfflineSearchHarness.csproj') -c Release

$harness = Join-Path $solverPath '.local/tool-build/OfflineSearchHarness/bin/Release/net9.0/OfflineSearchHarness.dll'
if ($SkipArtwork) {
    python (Join-Path $PSScriptRoot 'tools/combat_lab_art/catalog.py') --pck $Sts2Pck --harness $harness --metadata-only
} else {
    python (Join-Path $PSScriptRoot 'tools/combat_lab_art/catalog.py') --pck $Sts2Pck --harness $harness
}
Write-Host "Combat Lab is ready at http://127.0.0.1:8765/"
Write-Host "Run .\run_combat_lab_web.cmd to start the local web service."
