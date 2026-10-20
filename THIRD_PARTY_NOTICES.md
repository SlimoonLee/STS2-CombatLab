# Third-party notices

Combat Lab is an independent client for the local STS2 game installation. It
does not redistribute the game, game DLLs, Steam Workshop files, extracted
localization, portraits, save files, or evaluation logs.

## CombatSolver

The offline search host is built from the pinned upstream revision shown in
[`upstream.lock.json`](upstream.lock.json). The local headless-worker and import
adaptations are distributed as [`patches/combat-solver-combat-lab.patch`](patches/combat-solver-combat-lab.patch).
Apply that patch to the upstream checkout before building. Preserve the
upstream license and notice in `third_party/licenses/`.

Source: <https://github.com/Torch1230/CombatSolver>

## SeedOracle

The encounter-pool grouping and save-analysis workflow were informed by
SeedOracle. SeedOracle is not loaded into the runtime and is not a dependency of
the offline harness.

Source: <https://github.com/SlimoonLee/SeedOracle>

## Spine runtime

The optional static monster renderer installs `@esotericsoftware/spine-webgl`
from npm. Its license is included by the package and is available from
<https://esotericsoftware.com/spine-runtime-license>.

## Slay the Spire 2

Slay the Spire 2 and its game data are owned by Mega Crit. Users must own and
install the game through Steam. The setup script reads data from that local
installation and writes generated files to ignored paths.
