#!/usr/bin/env python3
"""Game-outside CombatSolver runner for Slay the Spire 2.

This is intentionally a small orchestration layer.  Combat semantics, card and
relic injection, encounter construction, RNG and search all remain in the
game's C# assemblies through OfflineSearchHarness.  The Python process only
expands an evaluation plan, starts capped case-level worker hosts and aggregates
their JSON output.  Each warm host reuses the game's immutable bootstrap for a
batch of cases while the native run is cleaned up between cases.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from combat_lab_saves import SaveImportError, materialize_plan, model_entry
from combat_lab_relics import validate_relic_props
from combat_lab_cards import mad_science_members


ROOT = Path(__file__).resolve().parent
_HARNESS_CANDIDATES = (
    # The upstream repository now builds tools/search into this shared output.
    ROOT / "CombatSolver" / ".local" / "tool-build" / "OfflineSearchHarness" / "bin" / "Release" / "net9.0" / "OfflineSearchHarness.dll",
    ROOT / "CombatSolver" / "tools" / "search" / "OfflineSearchHarness" / "bin" / "Release" / "net9.0" / "OfflineSearchHarness.dll",
    # Compatibility with pre-update local builds.
    ROOT / "CombatSolver" / "tools" / "OfflineSearchHarness" / "bin" / "Release" / "net9.0" / "OfflineSearchHarness.dll",
)
DEFAULT_HARNESS = next((path for path in _HARNESS_CANDIDATES if path.exists()), _HARNESS_CANDIDATES[0])
DEFAULT_ACT_COUNT = 3


def _configure_output() -> None:
    """Use UTF-8 from the native harness through the web host and saved logs."""

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="strict")
        except (AttributeError, ValueError):
            pass


_configure_output()


class CombatLabError(RuntimeError):
    """An invalid plan or an unavailable local harness."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CombatLabError(f"找不到输入文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise CombatLabError(f"JSON 无法解析：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CombatLabError(f"输入根节点必须是 JSON 对象：{path}")
    return value


def _as_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CombatLabError(f"{label} 必须是对象")
    return value


def _as_list(value: Any, label: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CombatLabError(f"{label} 必须是数组")
    return value


def _first(mapping: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return default


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CombatLabError(f"{label} 必须是非空字符串")
    return value.strip()


def _optional_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CombatLabError(f"{label} 必须是整数")
    return value


def _normalise_card(item: Any, *, deck: bool) -> dict[str, Any]:
    if isinstance(item, str):
        raw: dict[str, Any] = {"id": item}
    else:
        raw = _as_dict(item, "卡牌项")
    card_id = _require_string(_first(raw, "id", "cardId"), "卡牌 id")
    count = _optional_int(_first(raw, "count", default=1), f"卡牌 {card_id} 的 count")
    if count is None or count < 1:
        raise CombatLabError(f"卡牌 {card_id} 的 count 必须大于 0")
    upgrade = _optional_int(
        _first(raw, "upgrade", "upgradeLevel", "upgradeLevels", default=0),
        f"卡牌 {card_id} 的 upgrade",
    )
    if upgrade is None or upgrade < 0:
        raise CombatLabError(f"卡牌 {card_id} 的 upgrade 必须不小于 0")
    injection: dict[str, Any] = {
        "cardId": card_id,
        "count": count,
        "upgradeLevels": upgrade,
    }
    for source, target in (
        ("enchantmentId", "enchantmentId"),
        ("enchantmentAmount", "enchantmentAmount"),
        ("afflictionId", "afflictionId"),
        ("afflictionAmount", "afflictionAmount"),
        ("dynamicVars", "dynamicVars"),
        ("enumMembers", "enumMembers"),
    ):
        if source in raw:
            value = raw[source]
            if source.endswith("Id") and not str(value or "").strip():
                continue
            if source.endswith("Amount") and value in (None, ""):
                continue
            injection[target] = value
    if model_entry(card_id) == "MAD_SCIENCE":
        try:
            injection["enumMembers"] = mad_science_members(raw)
        except ValueError as exc:
            raise CombatLabError(str(exc)) from exc
    if deck:
        injection["treatAsDeckCard"] = True
    else:
        pile = str(_first(raw, "pile", default="Hand"))
        if pile not in {"Hand", "Draw", "Discard", "Exhaust"}:
            raise CombatLabError(
                f"战斗内卡牌 {card_id} 的 pile 必须是 Hand/Draw/Discard/Exhaust"
            )
        injection["pile"] = pile
    return injection


def _normalise_relic(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        raw: dict[str, Any] = {"id": item}
    else:
        raw = _as_dict(item, "遗物项")
    relic_id = _require_string(_first(raw, "id", "relicId"), "遗物 id")
    injection: dict[str, Any] = {"relicId": relic_id}
    for source, target in (
        ("addWithoutObtainedEffects", "addWithoutObtainedEffects"),
        ("integerMembers", "integerMembers"),
        ("booleanMembers", "booleanMembers"),
    ):
        if source in raw:
            injection[target] = raw[source]
    if "savedProps" in raw:
        props = raw["savedProps"]
        try:
            validate_relic_props(model_entry(relic_id), props)
        except ValueError as exc:
            raise CombatLabError(str(exc)) from exc
        for group, target in (("ints", "integerMembers"), ("bools", "booleanMembers")):
            injection[target] = {**injection.get(target, {}), **{entry["name"]: entry["value"] for entry in props.get(group, [])}}
        unsupported = [group for group, values in props.items() if group not in {"ints", "bools"} and values]
        if unsupported:
            raise CombatLabError(f"遗物 {relic_id} 的 {', '.join(unsupported)} 属性需通过存档导入恢复")
    return injection


def _normalise_potion(item: Any) -> dict[str, str]:
    potion_id = item if isinstance(item, str) else _first(_as_dict(item, "药水项"), "id", "potionId")
    return {"potionId": _require_string(potion_id, "药水 id")}


def _normalise_encounter(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        return {"id": _require_string(item, "遭遇 id")}
    raw = _as_dict(item, "遭遇项")
    encounter_id = _require_string(_first(raw, "id", "encounterId"), "遭遇 id")
    result = {"id": encounter_id}
    for key in ("kind", "label", "room", "mapPoint"):
        if key in raw:
            result[key] = raw[key]
    return result


def _expand_pool(plan: dict[str, Any]) -> list[dict[str, Any]]:
    encounters = [_normalise_encounter(item) for item in _as_list(plan.get("encounters"), "encounters")]
    pool_value = plan.get("pool")
    if pool_value is not None:
        pool_items = _as_list(pool_value, "pool")
        encounters.extend(_normalise_encounter(item) for item in pool_items)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in encounters:
        if item["id"].casefold() not in seen:
            seen.add(item["id"].casefold())
            unique.append(item)
    if not unique:
        raise CombatLabError("至少提供 encounters 或 pool 中的一项遭遇 id")
    return unique


def _sample_cases(plan: dict[str, Any]) -> list[dict[str, Any]]:
    encounter_catalog = _expand_pool(plan)
    samples = _optional_int(plan.get("samples", 1), "samples") or 1
    if samples < 1:
        raise CombatLabError("samples 必须大于 0")
    pool = plan.get("pool")
    is_random_pool = pool is not None and len(_as_list(pool, "pool")) > 0
    pool_seed = str(plan.get("poolSeed", plan.get("seed", "COMBATLAB")))
    rng = random.Random(pool_seed)
    cases: list[dict[str, Any]] = []
    if is_random_pool:
        candidates = [_normalise_encounter(item) for item in _as_list(pool, "pool")]
        if not candidates:
            raise CombatLabError("pool 不能为空")
        for index in range(samples):
            selected = candidates[rng.randrange(len(candidates))]
            cases.append({**selected, "sample": index + 1, "poolSample": True})
    else:
        for item in encounter_catalog:
            for sample in range(samples):
                cases.append({**item, "sample": sample + 1, "poolSample": False})
    return cases


def _stable_seed(value: str, index: int) -> int:
    digest = hashlib.sha256(f"{value}:{index}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little", signed=False)


def _room_type(case: dict[str, Any], default: str = "Monster") -> str:
    raw_value = case.get("room", case.get("kind"))
    if raw_value is None:
        encounter_id = str(case.get("id", "")).casefold()
        raw = "Boss" if "_boss" in encounter_id else "Elite" if "_elite" in encounter_id else default
    else:
        raw = str(raw_value)
    aliases = {
        "normal": "Monster",
        "monster": "Monster",
        "elite": "Elite",
        "boss": "Boss",
    }
    return aliases.get(raw.casefold(), raw)


def _build_request(plan: dict[str, Any], case: dict[str, Any], index: int) -> dict[str, Any]:
    mode = str(plan.get("mode", "build")).casefold()
    if mode not in {"build", "save", "snapshot"}:
        raise CombatLabError("mode 只支持 build 或 save")
    character = _require_string(plan.get("character", "IRONCLAD"), "character")
    encounter = _require_string(case["id"], "encounter id")
    seed = str(plan.get("seed", "COMBATLAB"))
    initial = _as_dict(plan.get("initial", {}), "initial")
    try:
        ascension = int(plan.get("ascension", 0))
    except (TypeError, ValueError) as exc:
        raise CombatLabError("ascension 必须是整数，范围为 0 到 10") from exc
    if not 0 <= ascension <= 10:
        raise CombatLabError("ascension 必须在 0 到 10 之间（游戏当前最高为进阶 10）")
    try:
        act_index = int(plan.get("act", plan.get("actIndex", 0)))
    except (TypeError, ValueError) as exc:
        raise CombatLabError(
            f"act 必须是整数，范围为游戏内部阶段索引 0 到 {DEFAULT_ACT_COUNT - 1}"
        ) from exc
    if not 0 <= act_index < DEFAULT_ACT_COUNT:
        raise CombatLabError(
            f"act 必须在 0 到 {DEFAULT_ACT_COUNT - 1} 之间（游戏当前有 {DEFAULT_ACT_COUNT} 个默认阶段）"
        )
    act_id_value = _first(plan, "actId", "actModelId")
    act_id = None if act_id_value is None else _require_string(act_id_value, "actId")
    if act_id is not None and mode != "build":
        raise CombatLabError("存档模式不能覆盖阶段分支；请让存档提供当前阶段模型")
    request: dict[str, Any] = {
        "schemaVersion": 1,
        "runId": f"combat-lab-{index:04d}",
        "scenarioId": f"COMBAT-LAB-{index:04d}",
        "characterId": character,
        "encounterId": encounter,
        "modifierIds": [str(x) for x in _as_list(plan.get("modifiers"), "modifiers")],
        "seed": seed,
        "targetRoomType": _room_type(case),
        "targetMapPointType": str(case.get("mapPoint", "Unassigned")),
        "ascension": ascension,
        "actIndexForTest": act_index,
        "cards": [],
        "combatRelics": [],
        "powers": [],
        "orbs": [],
        "clearPlayerHand": False,
        "clearPlayerPiles": False,
        "clearAllPowers": False,
        "preCombatSimulationSeed": _stable_seed(seed, index),
    }
    if act_id is not None:
        request["actIdForTest"] = act_id
    if mode in {"save", "snapshot"}:
        save_path = _first(plan, "savePath", "snapshotPath")
        if not save_path:
            raise CombatLabError("save 模式需要 savePath")
        save = Path(str(save_path)).expanduser().resolve()
        if not save.exists():
            raise CombatLabError(f"找不到存档快照：{save}")
        save_metadata = _read_json(save)
        if "character" not in plan:
            players = save_metadata.get("players")
            if isinstance(players, list) and players and isinstance(players[0], dict):
                inferred_character = _first(players[0], "character_id", "characterId")
                if isinstance(inferred_character, str) and inferred_character.strip():
                    character = model_entry(inferred_character.strip())
                    request["characterId"] = character
        if "act" not in plan and "actIndex" not in plan:
            inferred_act = _first(save_metadata, "current_act_index", "currentActIndex")
            if isinstance(inferred_act, int):
                if not 0 <= inferred_act < DEFAULT_ACT_COUNT:
                    raise CombatLabError(
                        f"存档中的当前阶段索引必须在 0 到 {DEFAULT_ACT_COUNT - 1} 之间（游戏当前有 {DEFAULT_ACT_COUNT} 个默认阶段）"
                    )
                request["actIndexForTest"] = inferred_act
        request.update(
            {
                "runSnapshotPath": str(save),
                "loadRunSnapshotDirectly": True,
                "targetActFloor": int(
                    _first(plan, "actFloor", "targetActFloor", default=1)
                ),
                "targetMapColumn": int(
                    _first(plan, "mapColumn", "targetMapColumn", default=0)
                ),
                "preserveNativeCombatStateForTest": True,
            }
        )
        if "preCombatPlayerCurrentHpOverride" in plan:
            request["preCombatPlayerCurrentHpOverride"] = _optional_int(
                plan["preCombatPlayerCurrentHpOverride"], "preCombatPlayerCurrentHpOverride"
            )
    else:
        serialized_inventory = bool(plan.get("saveImport") and plan.get("_playerStatePath"))
        deck = [] if serialized_inventory else [_normalise_card(item, deck=True) for item in _as_list(plan.get("deck"), "deck")]
        if not deck and not serialized_inventory:
            raise CombatLabError("build 模式需要至少一张 deck 卡牌")
        request.update(
            {
                "clearRunDeck": True,
                "clearStartingRelics": bool(plan.get("clearStartingRelics", True)),
                "runCards": deck,
                "relics": [] if serialized_inventory else [_normalise_relic(item) for item in _as_list(plan.get("relics"), "relics")],
                "potions": [] if serialized_inventory else [_normalise_potion(item) for item in _as_list(plan.get("potions"), "potions")],
                "preserveNativeCombatStateForTest": not bool(initial),
            }
        )
    if plan.get("_playerStatePath"):
        request["combatLabPlayerStatePath"] = plan["_playerStatePath"]
        if plan.get("saveImport"):
            request.update(runCards=[], relics=[], potions=[], clearRunDeck=False, clearStartingRelics=False)
        if initial:
            raise CombatLabError("导入状态不能与固定战斗夹具 initial 混用")
    if initial:
        request.update(
            {
                "preserveNativeCombatStateForTest": False,
                "enemyCurrentHp": int(initial.get("enemyHp", initial.get("enemyCurrentHp", 1))),
                "initialEnemyMaxHps": list(initial.get("enemyMaxHps", [])),
                "initialEnemyCurrentHps": list(initial.get("enemyCurrentHps", [])),
                "initialEnemyBlocks": list(initial.get("enemyBlocks", [])),
                "initialPlayerHp": initial.get("playerHp"),
                "initialPlayerMaxHp": initial.get("playerMaxHp"),
                "initialPlayerBlock": initial.get("playerBlock"),
                "initialPlayerEnergy": initial.get("playerEnergy"),
                "initialPlayerStars": initial.get("playerStars"),
                "clearPlayerHand": bool(initial.get("clearHand", False)),
                "clearPlayerPiles": bool(initial.get("clearPiles", False)),
                "cards": [_normalise_card(item, deck=False) for item in _as_list(initial.get("cards"), "initial.cards")],
                "combatRelics": [_normalise_relic(item) for item in _as_list(initial.get("combatRelics"), "initial.combatRelics")],
                "potions": [_normalise_potion(item) for item in _as_list(initial.get("potions"), "initial.potions")],
            }
        )
    return request


def _search_args(search: dict[str, Any]) -> list[str]:
    args: list[str] = ["--profile", str(search.get("profile", "Medium"))]
    # 旧计划可能沿用 CombatSolver 0.49 的策略名 Disallow；1.0 起该策略改名为 Disabled。
    if str(search.get("potionPolicy", "")).casefold() == "disallow":
        search = {**search, "potionPolicy": "Disabled"}
    mapping = {
        "beam": "--beam",
        "nodes": "--nodes",
        "cardBranches": "--card-branches",
        "pileBranches": "--pile-branches",
        "handBranches": "--hand-branches",
        "dop": "--dop",
        "budgetMs": "--budget-ms",
        "potionPolicy": "--potion-policy",
        "searchMode": "--search-mode",
        "language": "--language",
    }
    for key, option in mapping.items():
        if key in search:
            args.extend([option, str(search[key])])
    # CombatSolver 1.0 的 HotSearch 引擎删除了自适应新颖性与无基线开关；旧计划里的这两个键
    # 不再传递，否则宿主会以“未知选项”退出。组合策略与 NoGC 区域仍被上游当作兼容空操作接受。
    bool_options = {
        "usePortfolio": "--use-portfolio",
        "stopAtZeroLoss": "--stop-at-zero-loss",
        "productionBudget": "--production-budget",
        "measurePhases": "--measure-phases",
        "enableNoGcRegion": "--enable-no-gc-region",
    }
    for key, option in bool_options.items():
        if bool(search.get(key, False)):
            args.append(option)
    if search.get("enableNoGcRegion") and "noGcRegionBudgetGigabytes" in search:
        args.extend(["--no-gc-region-budget-gigabytes", str(search["noGcRegionBudgetGigabytes"])])
    return args


def _find_metric(value: Any, *keys: str) -> Any:
    if isinstance(value, dict):
        lowered = {str(k).casefold(): v for k, v in value.items()}
        for key in keys:
            if key.casefold() in lowered:
                return lowered[key.casefold()]
    return None


def _case_metrics(result_dir: Path) -> dict[str, Any]:
    result_path = result_dir / "result.json"
    harness_path = result_dir / "harness-result.json"
    result = _read_json(result_path) if result_path.exists() else {}
    harness = _read_json(harness_path) if harness_path.exists() else {}
    metrics = result.get("solverMetrics") or harness.get("solverMetrics") or {}
    status = str(result.get("status", "Failed"))
    return {
        "status": status,
        "boundary": _find_metric(metrics, "boundary"),
        "projectedHpLoss": _find_metric(metrics, "projectedBattleHpLost", "projectedHpLoss"),
        "finalHp": _find_metric(metrics, "finalHp"),
        "finalEnemyHp": _find_metric(metrics, "finalEnemyHp"),
        "combatEndedTurn": _find_metric(metrics, "combatEndedTurn"),
        "potionCount": _find_metric(metrics, "potionCount"),
        "potionUses": len(_find_metric(metrics, "potionUses") or []),
        "onlyDeathRoutes": _find_metric(metrics, "onlyDeathRoutes", "onlyDeathRoutesFound"),
        "totalExpanded": _find_metric(metrics, "totalExpanded"),
        "totalTransitions": _find_metric(metrics, "totalTransitions"),
        "error": result.get("error") or harness.get("error"),
    }


def _emit_progress(event: str, **values: Any) -> None:
    """Emit a small machine-readable progress record for the local web host.

    The harness remains the authority for search metrics.  These records only
    describe orchestration state (which sample is running and when it ends),
    so the web UI never has to invent a percentage for an in-flight search.
    """

    payload = {"event": event, **values}
    print(
        "LAB_PROGRESS "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )


def _resolve_case_workers(search: dict[str, Any], case_count: int, *, stop_on_error: bool) -> int:
    """Return a conservative number of independent harness processes.

    A pool sample is assigned to a small number of isolated, persistent
    OfflineSearchHarness processes.  Running two such processes concurrently
    removes most of the repeated process/static initialization from the wall
    clock while preserving the solver's per-case state isolation.  ``caseWorkers`` is deliberately separate from the
    solver's inner DOP; the product is capped by the logical CPU count and a
    small hard limit so a large sample request cannot fork an unbounded number
    of game hosts.  ``--stop-on-error`` remains strictly serial.
    """

    if case_count <= 1 or stop_on_error:
        return 1
    requested = _optional_int(search.get("caseWorkers", 1), "caseWorkers") or 1
    if requested < 1:
        raise CombatLabError("caseWorkers 必须大于 0")
    dop = _optional_int(search.get("dop", 1), "dop") or 1
    if dop < 1:
        raise CombatLabError("dop 必须大于 0")
    cpu_count = os.cpu_count() or 2
    cpu_cap = max(1, cpu_count // dop)
    return min(case_count, requested, cpu_cap, 4)


def _run_case(
    prepared: dict[str, Any],
    *,
    args: argparse.Namespace,
    total: int,
) -> dict[str, Any]:
    """Run one already-prepared isolated harness request.

    The worker owns only its case directory and child process.  The caller
    collects records and publishes ordered aggregate progress, so concurrent
    workers never share game state or mutate the report list.
    """

    index = int(prepared["index"])
    case = prepared["case"]
    label = str(prepared["label"])
    case_dir = Path(prepared["caseDir"])
    request_path = Path(prepared["requestPath"])
    command = list(prepared["command"])
    if args.verbose:
        print("$ " + " ".join(command), flush=True)
    _emit_progress(
        "case-start",
        phase="准备搜索",
        completed=0,
        total=total,
        current=index,
        encounter=case["id"],
        label=case.get("label"),
        percent=0,
    )
    case_started = time.monotonic()
    process_output: list[str] = []
    return_code = -1
    try:
        process_env = os.environ.copy()
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=process_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            clean = line.rstrip("\r\n")
            process_output.append(clean)
            print(clean, flush=True)
        return_code = process.wait()
    except OSError as exc:
        process_output.append(f"启动 OfflineSearchHarness 失败：{exc}")
        print(process_output[-1], flush=True)
    stdout_text = "\n".join(process_output)
    (case_dir / "stdout.txt").write_text(stdout_text, encoding="utf-8")
    (case_dir / "stderr.txt").write_text("", encoding="utf-8")
    metrics = _case_metrics(case_dir)
    return {
        "index": index,
        "encounter": case["id"],
        "label": case.get("label"),
        "sample": case.get("sample", 1),
        "poolSample": case.get("poolSample", False),
        "returnCode": return_code,
        "request": str(request_path),
        "resultDirectory": str(case_dir),
        "elapsedMs": round((time.monotonic() - case_started) * 1000),
        "metrics": metrics,
    }


def _run_warm_worker(
    prepared_cases: list[dict[str, Any]],
    *,
    worker_index: int,
    dotnet: str,
    harness: Path,
    search: dict[str, Any],
    out: Path,
    args: argparse.Namespace,
    total: int,
) -> list[dict[str, Any]]:
    """Run a batch in one persistent OfflineSearchHarness process.

    Static game/model initialization is expensive but immutable for all samples
    in one evaluation.  The C# worker keeps that state alive and emits explicit
    case markers; this function routes each marker-delimited log back to the
    original case directory and turns the native result into the same record
    shape as the cold-process fallback.
    """

    if not prepared_cases:
        return []
    ordered = sorted(prepared_cases, key=lambda item: int(item["index"]))
    worker_root = out / "workers" / f"{worker_index:03d}"
    worker_root.mkdir(parents=True, exist_ok=True)
    manifest_path = worker_root / "manifest.json"
    manifest = {
        "cases": [
            {
                "requestPath": str(Path(item["requestPath"]).resolve()),
                "label": str(item["label"]),
                "outputDirectory": str(Path(item["caseDir"]).resolve()),
                "index": int(item["index"]),
            }
            for item in ordered
        ]
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    command = [
        dotnet,
        str(harness),
        "--worker-manifest",
        str(manifest_path),
        "--label",
        f"warm-worker-{worker_index:03d}",
        "--out",
        str(worker_root),
        "--workspace",
        str(worker_root / "workspace"),
        "--milestone",
        "M2",
        *_search_args(search),
    ]
    if args.stop_on_error:
        command.append("--stop-on-error")
    if args.verbose:
        print("$ " + " ".join(command), flush=True)

    prepared_by_index = {int(item["index"]): item for item in ordered}
    output_by_index: dict[int, list[str]] = {index: [] for index in prepared_by_index}
    started_at: dict[int, float] = {}
    completed_at: dict[int, float] = {}
    native_codes: dict[int, int] = {}
    preamble: list[str] = []
    all_output: list[str] = []
    current_index: int | None = None
    process_return_code = -1
    process_started = time.monotonic()
    start_pattern = re.compile(r"^WARM_CASE_START index=(\d+) label=(.*)$")
    complete_pattern = re.compile(r"^WARM_CASE_COMPLETE index=(\d+) returnCode=(-?\d+)$")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            clean = line.rstrip("\r\n")
            all_output.append(clean)
            print(clean, flush=True)
            now = time.monotonic()
            start_match = start_pattern.match(clean)
            complete_match = complete_pattern.match(clean)
            if start_match:
                current_index = int(start_match.group(1))
                started_at[current_index] = now
                output_by_index.setdefault(current_index, []).append(clean)
                item = prepared_by_index.get(current_index)
                if item is not None:
                    case = item["case"]
                    _emit_progress(
                        "case-start",
                        phase="准备搜索",
                        completed=0,
                        total=total,
                        current=current_index,
                        encounter=case["id"],
                        label=case.get("label"),
                        percent=0,
                    )
                continue
            if complete_match:
                current_index = int(complete_match.group(1))
                native_codes[current_index] = int(complete_match.group(2))
                completed_at[current_index] = now
                output_by_index.setdefault(current_index, []).append(clean)
                current_index = None
                continue
            if current_index is None:
                preamble.append(clean)
            else:
                output_by_index.setdefault(current_index, []).append(clean)
        process_return_code = process.wait()
    except OSError as exc:
        message = f"启动 OfflineSearchHarness 常驻宿主失败：{exc}"
        all_output.append(message)
        preamble.append(message)
        print(message, flush=True)

    (worker_root / "stdout.txt").write_text("\n".join(all_output), encoding="utf-8")
    (worker_root / "stderr.txt").write_text("", encoding="utf-8")

    records: list[dict[str, Any]] = []
    completed_indices = set(native_codes)
    # A clean worker exit with a missing marker is still a failed case.  When
    # --stop-on-error intentionally stops the batch, leave unvisited cases out
    # just like the previous serial orchestration path did.
    include_missing = not (args.stop_on_error and any(code != 0 for code in native_codes.values()))
    if process_return_code != 0 and not args.stop_on_error:
        include_missing = True
    indices = sorted(completed_indices)
    if include_missing:
        indices = sorted(set(indices) | set(prepared_by_index))
    for index in indices:
        item = prepared_by_index.get(index)
        if item is None:
            continue
        case_dir = Path(item["caseDir"])
        case_log = list(preamble)
        case_log.extend(output_by_index.get(index, []))
        (case_dir / "stdout.txt").write_text("\n".join(case_log), encoding="utf-8")
        (case_dir / "stderr.txt").write_text("", encoding="utf-8")
        native_code = native_codes.get(index)
        return_code = native_code if native_code is not None else (process_return_code or 1)
        started = started_at.get(index, process_started)
        ended = completed_at.get(index, time.monotonic())
        metrics = _case_metrics(case_dir)
        records.append(
            {
                "index": index,
                "encounter": item["case"]["id"],
                "label": item["case"].get("label"),
                "sample": item["case"].get("sample", 1),
                "poolSample": item["case"].get("poolSample", False),
                "returnCode": return_code,
                "request": str(item["requestPath"]),
                "resultDirectory": str(case_dir),
                "elapsedMs": round((ended - started) * 1000),
                "metrics": metrics,
            }
        )
    return records


def _numeric(values: Iterable[Any]) -> list[float]:
    result: list[float] = []
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            result.append(float(value))
    return result


def _aggregate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [case for case in cases if case["metrics"]["status"].casefold() == "passed"]
    hp_loss = _numeric(case["metrics"]["projectedHpLoss"] for case in completed)
    final_hp = _numeric(case["metrics"]["finalHp"] for case in completed)
    potions = _numeric(case["metrics"]["potionUses"] for case in completed)
    turns = _numeric(case["metrics"]["combatEndedTurn"] for case in completed)
    only_death = sum(bool(case["metrics"].get("onlyDeathRoutes")) for case in completed)
    def stats(values: list[float]) -> dict[str, float] | None:
        if not values:
            return None
        return {
            "min": min(values),
            "mean": statistics.fmean(values),
            "max": max(values),
            "stdev": statistics.pstdev(values) if len(values) > 1 else 0.0,
        }
    total = len(cases)
    return {
        "cases": total,
        "completed": len(completed),
        "failed": total - len(completed),
        "completionRate": len(completed) / total if total else 0.0,
        "onlyDeathRouteRate": only_death / len(completed) if completed else None,
        "projectedHpLoss": stats(hp_loss),
        "finalHp": stats(final_hp),
        "potionUses": stats(potions),
        "combatEndedTurn": stats(turns),
        "interpretation": (
            "同一搜索预算和遭遇样本下，战损越低、完成率越高、最终生命越高通常代表牌组更稳；"
            "这不是跨场景的绝对分数。"
        ),
    }


def evaluate(args: argparse.Namespace) -> int:
    plan_path = Path(args.input).expanduser().resolve()
    plan = _read_json(plan_path)
    if plan.get("mods"):
        raise CombatLabError(
            "当前离线宿主不会自动加载第三方 Workshop Mod；请把 Mod 接入 headless 游戏环境，"
            "或移除 mods 字段后只评估原版/当前程序集中的内容。"
        )
    harness = Path(args.harness).expanduser().resolve() if args.harness else DEFAULT_HARNESS
    if not harness.exists():
        raise CombatLabError(
            f"找不到 OfflineSearchHarness：{harness}\n"
            "先构建 CombatSolver 和 tools/search/OfflineSearchHarness，或用 --harness 指定 DLL。"
        )
    if os.path.sep in args.dotnet or (os.path.altsep and os.path.altsep in args.dotnet):
        dotnet = str(Path(args.dotnet).expanduser().resolve())
        if not Path(dotnet).exists():
            raise CombatLabError(f"找不到 dotnet：{args.dotnet}")
    else:
        dotnet = shutil.which(args.dotnet)
        if not dotnet:
            raise CombatLabError(f"找不到 dotnet：{args.dotnet}")
    out = Path(args.out).expanduser().resolve() if args.out else plan_path.with_suffix("").parent / "runs"
    if out.exists() and any(out.iterdir()) and not args.force:
        raise CombatLabError(f"输出目录非空，使用 --force 才会覆盖：{out}")
    out.mkdir(parents=True, exist_ok=True)
    try:
        plan = materialize_plan(plan, out)
    except (SaveImportError, ValueError, TypeError) as exc:
        raise CombatLabError(f"无法准备评估状态：{exc}") from exc
    cases = _sample_cases(plan)
    search = _as_dict(plan.get("search", {}), "search")
    records: list[dict[str, Any]] = []
    workers = _resolve_case_workers(search, len(cases), stop_on_error=args.stop_on_error)
    _emit_progress(
        "plan",
        phase="准备评估",
        completed=0,
        total=len(cases),
        percent=0,
        elapsedMs=0,
        caseWorkers=workers,
        dop=search.get("dop", 1),
    )
    prepared_cases: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        label = re.sub(r"[<>:\"/\\|?*]", "_", str(case.get("label", case["id"])))
        case_dir = out / "cases" / f"{index:03d}-{label}"
        case_dir.mkdir(parents=True, exist_ok=True)
        request = _build_request(plan, case, index)
        request_path = case_dir / "request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
        command = [
            dotnet,
            str(harness),
            "--request",
            str(request_path),
            "--label",
            f"{index:03d}-{label}",
            "--out",
            str(case_dir),
            "--milestone",
            "M2",
            *_search_args(search),
        ]
        prepared_cases.append(
            {
                "index": index,
                "case": case,
                "label": label,
                "caseDir": str(case_dir),
                "requestPath": str(request_path),
                "command": command,
            }
        )

    def collect(record: dict[str, Any]) -> None:
        records.append(record)
        metrics = record["metrics"]
        index = record["index"]
        print(
            f"[{index}/{len(cases)}] {record['encounter']} status={metrics['status']} "
            f"hp_loss={metrics['projectedHpLoss']} final_hp={metrics['finalHp']} "
            f"potions={metrics['potionUses']}",
            flush=True,
        )
        _emit_progress(
            "case-complete",
            phase="场次完成" if record["returnCode"] == 0 else "场次失败",
            completed=len(records),
            total=len(cases),
            current=index,
            encounter=record["encounter"],
            label=record.get("label"),
            percent=len(records) / len(cases) * 100 if cases else 100,
            elapsedMs=record.get("elapsedMs", 0),
            expanded=metrics.get("totalExpanded"),
            transitions=metrics.get("totalTransitions"),
        )

    print(
        f"使用 {workers} 个常驻场次宿主；每个宿主复用游戏初始化，"
        f"场次之间清理跑局，单场内部 DOP={search.get('dop', 1)}。",
        flush=True,
    )
    batches: list[list[dict[str, Any]]] = [[] for _ in range(workers)]
    for position, prepared in enumerate(prepared_cases):
        batches[position % workers].append(prepared)
    warm_kwargs = {
        "dotnet": dotnet,
        "harness": harness,
        "search": search,
        "out": out,
        "args": args,
        "total": len(cases),
    }
    if workers == 1:
        for record in _run_warm_worker(
            batches[0], worker_index=1, **warm_kwargs
        ):
            collect(record)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="combat-warm") as executor:
            futures = {
                executor.submit(
                    _run_warm_worker,
                    batch,
                    worker_index=worker_index,
                    **warm_kwargs,
                ): worker_index
                for worker_index, batch in enumerate(batches, start=1)
                if batch
            }
            for future in as_completed(futures):
                for record in future.result():
                    collect(record)
    records.sort(key=lambda item: item["index"])
    report = {
        "schemaVersion": 1,
        "tool": "combat_lab",
        "plan": str(plan_path),
        "harness": str(harness),
        "orchestration": {
            "warmWorkers": True,
            "caseWorkers": workers,
            "dop": search.get("dop", 1),
        },
        "importedState": plan.get("_importProvenance"),
        "cases": records,
        "summary": _aggregate(records),
        "byEncounter": {
            encounter_id: _aggregate(
                [record for record in records if record["encounter"] == encounter_id]
            )
            for encounter_id in sorted({record["encounter"] for record in records})
        },
    }
    report_path = out / "evaluation.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _emit_progress(
        "finished",
        phase="评估完成" if records and all(case["returnCode"] == 0 for case in records) else "评估结束",
        completed=len(records),
        total=len(cases),
        percent=len(records) / len(cases) * 100 if cases else 100,
    )
    print(f"report={report_path}", flush=True)
    return 0 if records and all(case["returnCode"] == 0 for case in records) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="STS2 游戏外 CombatSolver 战斗评估器")
    sub = parser.add_subparsers(dest="command", required=True)
    evaluate_parser = sub.add_parser("evaluate", help="运行直接构建或存档快照评估")
    evaluate_parser.add_argument("--input", required=True, help="评估计划 JSON")
    evaluate_parser.add_argument("--out", help="输出目录")
    evaluate_parser.add_argument("--harness", help="OfflineSearchHarness.dll 路径")
    evaluate_parser.add_argument("--dotnet", default="dotnet", help="dotnet 可执行文件")
    evaluate_parser.add_argument("--force", action="store_true", help="允许写入非空输出目录")
    evaluate_parser.add_argument("--verbose", action="store_true", help="显示每次调用的完整命令")
    evaluate_parser.add_argument("--stop-on-error", action="store_true", help="首个失败后停止")
    evaluate_parser.set_defaults(func=evaluate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except CombatLabError as exc:
        print(f"combat_lab: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
