# STS2 Combat Lab

STS2 Combat Lab is a local, game-outside web interface for evaluating Slay the
Spire 2 decks, relic states, potions, saved-run snapshots, and encounter pools.
The battle rules and search remain in CombatSolver's native C# game models; the
Python service builds requests, starts isolated offline workers, and reports
progress and results in the browser.

## What is published

This repository contains the Combat Lab application, its web UI, the native
card-value exporter, and a reproducible patch for the pinned CombatSolver
offline host. It does **not** contain STS2, Steam files, extracted game text or
artwork, saves, decompiled assemblies, or personal run logs. Those files are
generated locally and ignored by Git.

The encounter grouping follows SeedOracle's weak/strong/elite/Boss pool model.
SeedOracle is a reference, not a runtime dependency.

## Requirements

Windows is the supported setup path in this release:

- Slay the Spire 2 installed through Steam (the setup targets game data 0.111.0).
- Python 3.11 or newer, .NET 9 SDK, Git, and Node.js/npm.
- RitsuLib Workshop item `3747602295`, version `0.111.0`.
- GDRE Tools for extracting the installed PCK. Download it from its official
  release and pass the executable path with `-GdreTools`.

No Steam game process is started by the setup script or the web service.

## Setup

From a PowerShell prompt in this repository:

```powershell
.\setup_combat_lab.ps1 -GdreTools 'D:\tools\gdre_tools.exe'
```

If Steam is installed somewhere else, provide explicit paths:

```powershell
.\setup_combat_lab.ps1 `
  -Sts2Dir 'D:\SteamLibrary\steamapps\common\Slay the Spire 2' `
  -RitsuWorkshopRoot 'D:\SteamLibrary\steamapps\workshop\content\2868840\3747602295' `
  -GdreTools 'D:\tools\gdre_tools.exe'
```

The script clones CombatSolver at the revision in `upstream.lock.json`, checks
and applies the local patch, writes an ignored `CombatSolver/local.props`,
builds the offline harness without copying a mod into the game directory, and
generates `combat_lab/game_catalog_zhs.json`, `combat_lab/card_values.json` and
local artwork. Use `-SkipArtwork` for a faster metadata-only setup; the browser
will then show text until artwork is generated.

Generated game data is tied to the installed game DLL/PCK version. Re-run setup
after a game update and keep the generated files together.

## Start the web service

```powershell
.\run_combat_lab_web.cmd
```

Open <http://127.0.0.1:8765/>. The service listens only on localhost. The web
form has four areas: state construction, encounter and sampling, solver
settings, and results. Cards are independent instances, so copies of the same
card can have different upgrades, enchantments, growth values, and saved
properties. Save snapshots retain card/relic/potion state and can be adjusted
to a selected floor before evaluation.

The solver settings expose the native language, profile, budget, beam/node
limits, potion policy, single-combat DOP, sample workers and progress. Results
show completed samples, elapsed time, search phase, expanded nodes, transitions,
average damage taken, and the raw per-sample JSON.

## Command line

The same evaluator can run without the browser:

```powershell
python .\combat_lab.py evaluate `
  --input .\combat_lab\examples\basic_build.json `
  --out .\combat_lab\runs\basic `
  --force
```

Plans use internal game IDs. The examples default to Ascension 10 and the first
act's Overgrowth branch. A plan can list exact encounters or a deterministic
pool with `poolSeed`; a pool can contain the weak/strong/elite/Boss groups
generated from the installed game catalog.

## Updating the upstream solver

The application intentionally pins a known CombatSolver commit. Review the
upstream changes first, update `upstream.lock.json`, refresh the patch against a
clean checkout, then run the setup build. Do not force-push to the upstream
CombatSolver repository from this project.

## License and attribution

Original Combat Lab code is MIT licensed; see [`LICENSE`](LICENSE). CombatSolver
and the Random Foreseer-derived portions retain their own attribution and
permission terms in [`third_party/licenses/`](third_party/licenses/). The game,
its text, images and assemblies remain the property of their respective owners.
See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
