"""Immutable local save imports and floor-history reconstruction for Combat Lab."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from combat_lab_relics import relic_fields, validate_relic_props
from combat_lab_cards import (
    STABLE_CARD_PROPERTIES, growth_card_fields, normalise_card_props, validate_card_props,
    mad_science_members, MAD_SCIENCE_TYPES, MAD_SCIENCE_RIDERS,
)


ROOT = Path(__file__).resolve().parent
IMPORT_ROOT = ROOT / "combat_lab" / "runs" / "web" / "imports"
MAX_SAVE_BYTES = 8 * 1024 * 1024


class SaveImportError(ValueError):
    pass


def model_entry(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("id", "")
    return str(value or "").split(".", 1)[-1]


def _model(value: Any, category: str) -> str:
    return f"{category}.{model_entry(value)}"


def _parse(text: str) -> dict[str, Any]:
    if len(text.encode("utf-8")) > MAX_SAVE_BYTES:
        raise SaveImportError("存档文件超过 8 MiB")
    try:
        data = json.loads(text.lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        raise SaveImportError(f"存档不是有效的 JSON：{exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("players"), list) or not data["players"]:
        raise SaveImportError("文件没有玩家数据；请选择 current_run.save 或历史 .run 文件")
    if not all(isinstance(p, dict) and (p.get("character_id") or p.get("character")) for p in data["players"]):
        raise SaveImportError("存档中的玩家缺少角色标识")
    history = data.get("map_point_history", [])
    if not isinstance(history, list) or any(not isinstance(act, list) for act in history):
        raise SaveImportError("无法识别 map_point_history 楼层记录")
    if not isinstance(data.get("acts"), list) or not data["acts"]:
        raise SaveImportError("存档没有阶段和分支记录")
    for player in data["players"]:
        for field in ("deck", "relics", "potions"):
            if not isinstance(player.get(field, []), list) or any(not isinstance(item, dict) or not item.get("id") for item in player.get(field, [])):
                raise SaveImportError(f"存档中的 {field} 列表无效")
    return data


def _read(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_SAVE_BYTES:
            raise SaveImportError("存档文件超过 8 MiB")
        return _parse(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError) as exc:
        raise SaveImportError(f"无法读取存档：{path}：{exc}") from exc


def _source_path(source_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", source_id):
        raise SaveImportError("导入来源标识无效")
    return IMPORT_ROOT / f"{source_id}.json"


def freeze_source(*, path: str = "", content: str | None = None, name: str = "") -> str:
    if content is None:
        if not path.strip():
            raise SaveImportError("请选择或填写存档文件")
        original = Path(path).expanduser().resolve()
        data = _read(original)
        name = original.name
        path = str(original)
    else:
        data = _parse(content)
        name = Path(name.replace("\\", "/")).name or "uploaded.run"
        path = ""
    # Parse in Python: Steam player IDs and RNG seeds must never pass through JS numbers.
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    source_id = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    IMPORT_ROOT.mkdir(parents=True, exist_ok=True)
    target = _source_path(source_id)
    if not target.exists():
        target.write_text(encoded, encoding="utf-8")
        target.with_suffix(".meta.json").write_text(json.dumps({"name": name, "path": path}, ensure_ascii=False), encoding="utf-8")
    return source_id


def _flatten(data: dict[str, Any], player_index: int) -> list[dict[str, Any]]:
    player = data["players"][player_index]
    player_id = player.get("net_id", player.get("id"))
    acts = [model_entry(a) for a in data["acts"]]
    rows = []
    for act, history in enumerate(data.get("map_point_history", [])):
        for local_floor, entry in enumerate(history, 1):
            if not isinstance(entry, dict):
                raise SaveImportError("楼层记录不是对象")
            stats = next((s for s in entry.get("player_stats", []) if str(s.get("player_id")) == str(player_id)), None)
            if stats is None:
                raise SaveImportError(f"第 {len(rows) + 1} 层缺少所选玩家的行为记录")
            rooms = entry.get("rooms", [])
            rows.append({"floor": len(rows) + 1, "act": act, "actId": acts[act] if act < len(acts) else "",
                         "actFloor": local_floor, "room": entry.get("map_point_type", "unknown"),
                         "encounters": [model_entry(r.get("model_id")) for r in rooms if str(r.get("model_id", "")).startswith("ENCOUNTER.")],
                         "stats": stats, "entry": entry})
    return rows


def discover_saves() -> dict[str, Any]:
    roots = [Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "SlayTheSpire2",
             Path.home() / ".local/share/SlayTheSpire2", Path.home() / ".local/share/godot/app_userdata/Slay the Spire 2",
             Path.home() / "Library/Application Support/SlayTheSpire2"]
    found: set[Path] = set()
    skipped = 0
    items = []
    for root in roots:
        if not root.exists():
            continue
        found.update(root.rglob("current_run*.save"))
        found.update(p for p in root.rglob("*.run") if p.parent.name == "history")
    for path in found:
        try:
            data = _read(path)
            rows = _flatten(data, 0)
            player = data["players"][0]
            items.append({"path": str(path), "name": path.name, "kind": "current" if "character_id" in player else "history",
                          "profile": " / ".join(path.parts[-5:-2]), "character": model_entry(player.get("character_id", player.get("character"))),
                          "ascension": data.get("ascension", 0), "floors": len(rows), "modifiedAt": path.stat().st_mtime})
        except (SaveImportError, OSError):
            skipped += 1
    items.sort(key=lambda x: (x["kind"] != "current", -x["modifiedAt"]))
    return {"items": items, "skipped": skipped}


def _picked(stats: dict[str, Any], kind: str) -> list[str]:
    picked = [model_entry(c.get("choice")) for c in stats.get(f"{kind}_choices", []) if c.get("was_picked")]
    # Purchases normally appear in choices too. Preserve multiplicity without counting twice.
    counts = Counter(picked)
    bought = Counter(model_entry(v) for v in stats.get(f"bought_{kind}s", []))
    return list((counts | bought).elements())


def _actions(stats: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    for key in ("cards_gained", "cards_removed", "upgraded_cards", "downgraded_cards", "potion_used", "potion_discarded", "relics_removed"):
        values = [model_entry(v) for v in stats.get(key, [])]
        if values:
            actions.append({"kind": key, "ids": values})
    for kind in ("relic", "potion"):
        ids = _picked(stats, kind)
        if ids:
            actions.append({"kind": f"{kind}s_gained", "ids": ids})
    for transform in stats.get("cards_transformed", []):
        actions.append({"kind": "transform", "ids": [model_entry(transform["original_card"]), model_entry(transform["final_card"])]})
    for enchant in stats.get("cards_enchanted", []):
        actions.append({"kind": "enchant", "ids": [model_entry(enchant["card"]), model_entry(enchant.get("enchantment"))]})
    for key in ("rest_site_choices", "event_choices", "ancient_choice"):
        if stats.get(key):
            actions.append({"kind": key, "count": len(stats[key]), "choices": stats[key] if key == "rest_site_choices" else []})
    return actions


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {**{key: row[key] for key in ("floor", "act", "actId", "actFloor", "room", "encounters")},
            "hp": row["stats"].get("current_hp"), "maxHp": row["stats"].get("max_hp"), "gold": row["stats"].get("current_gold"),
            "damage": row["stats"].get("damage_taken", 0), "healed": row["stats"].get("hp_healed", 0), "actions": _actions(row["stats"])}


def _wrap(raw: dict[str, Any], ref: str, born: int | None = None) -> dict[str, Any]:
    value = copy.deepcopy(raw)
    if born is not None and "floor_added_to_deck" not in value:
        value["floor_added_to_deck"] = born
    return {"raw": value, "ref": ref}


def _has_payload(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_payload(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_payload(v) for v in value)
    return value is not None and value != ""


def _unsupported_fields(raw: dict[str, Any], kind: str) -> list[str]:
    allowed = {"id", "props", "floor_added_to_deck"} if kind == "relics" else {"id", "slot_index"} if kind == "potions" else {"id", "props", "floor_added_to_deck", "current_upgrade_level", "enchantment"}
    fields = [key for key, value in raw.items() if key not in allowed and _has_payload(value)]
    def extensions(value: Any, prefix: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key.startswith("save_dict_") and _has_payload(child):
                    fields.append(f"{prefix}{key}")
                else:
                    extensions(child, f"{prefix}{key}.")
        elif isinstance(value, list):
            for i, child in enumerate(value):
                extensions(child, f"{prefix}{i}.")
    extensions(raw.get("props", {}), "props.")
    extensions(raw.get("enchantment", {}), "enchantment.")
    return fields


def _card_match(deck: list[dict[str, Any]], raw: dict[str, Any], floor: int, issues: list[str], *, upgrade: bool = False, rows: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    candidates = [c for c in deck if model_entry(c["raw"]) == model_entry(raw)
                  and (c["raw"].get("floor_added_to_deck") or 1) <= floor
                  and (not upgrade or c["raw"].get("current_upgrade_level", 0) > 0)]
    if raw.get("floor_added_to_deck") is not None:
        exact = [c for c in candidates if c["raw"].get("floor_added_to_deck") == raw["floor_added_to_deck"]]
        candidates = exact or [c for c in candidates if c["raw"].get("floor_added_to_deck") is None]
    if not candidates:
        issues.append(f"第 {floor} 层：无法匹配卡牌 {model_entry(raw)} 的变化，请核对牌组。")
        return None
    protected: dict[str, int] = {}
    if upgrade and rows:
        for earlier in rows[:floor - 1]:
            value = model_entry(raw)
            changed = earlier["stats"].get("upgraded_cards", []).count(_model(value, "CARD")) - earlier["stats"].get("downgraded_cards", []).count(_model(value, "CARD"))
            if not changed:
                continue
            eligible = [c for c in deck if model_entry(c["raw"]) == value and (c["raw"].get("floor_added_to_deck") or 1) <= earlier["floor"]]
            # A removed/transformed older copy could have owned the prior upgrade.
            dead_alternatives = [card for r in rows[earlier["floor"] - 1:floor - 1]
                                 for card in r["stats"].get("cards_removed", []) + [t["original_card"] for t in r["stats"].get("cards_transformed", [])]
                                 if model_entry(card) == value and (card.get("floor_added_to_deck") or 1) <= earlier["floor"]]
            if len(eligible) == 1 and not dead_alternatives:
                ref = eligible[0]["ref"]
                protected[ref] = max(0, protected.get(ref, 0) + changed)
    def score(c: dict[str, Any]) -> tuple[int, int, int, int, int]:
        saved = c["raw"]
        return (int(raw.get("floor_added_to_deck") is not None and saved.get("floor_added_to_deck") == raw["floor_added_to_deck"]),
                int(not upgrade or saved.get("current_upgrade_level", 0) > protected.get(c["ref"], 0)),
                int(saved.get("enchantment") == raw.get("enchantment")),
                int(saved.get("current_upgrade_level", 0) == raw.get("current_upgrade_level", 0)),
                (saved.get("floor_added_to_deck") or 1) if upgrade else 0)
    candidates.sort(key=score, reverse=True)
    top = [c for c in candidates if score(c) == score(candidates[0])]
    signatures = {json.dumps(c["raw"], sort_keys=True) for c in top}
    if len(signatures) > 1 or (upgrade and len(candidates) > 1):
        issues.append(f"第 {floor} 层：{model_entry(raw)} 有多张候选，原版未记录实例编号；强化或附魔分配需要核对。")
    return candidates[0]


def _previous_enchantment(rows: list[dict[str, Any]], card: dict[str, Any], floor: int, enchant_index: int) -> Any:
    birth = card.get("floor_added_to_deck")
    known = []
    for row in rows:
        if row["floor"] > floor:
            break
        stats = row["stats"]
        for original in stats.get("cards_gained", []):
            saved = copy.deepcopy(original)
            saved.setdefault("floor_added_to_deck", row["floor"])
            known.append(saved)
        known.extend(t["final_card"] for t in stats.get("cards_transformed", []))
        changes = stats.get("cards_enchanted", [])
        if row["floor"] == floor:
            changes = changes[:enchant_index]
        known.extend(e["card"] for e in changes)
    matches = [c for c in known if model_entry(c) == model_entry(card) and birth is not None and c.get("floor_added_to_deck") == birth]
    return copy.deepcopy(matches[-1].get("enchantment")) if matches else None


def _reverse_inventory(player: dict[str, Any], rows: list[dict[str, Any]], cutoff: int, issues: list[str]) -> tuple[list, list, list]:
    deck = [_wrap(c, f"deck:{i}") for i, c in enumerate(player.get("deck", []))]
    relics = [_wrap(r, f"relic:{i}") for i, r in enumerate(player.get("relics", []))]
    potions = [_wrap(p, f"potion:{i}") for i, p in enumerate(player.get("potions", []))]
    for row in reversed(rows[cutoff:]):
        floor, stats = row["floor"], row["stats"]
        # Logs group actions by category rather than time. Restore removed cards first,
        # so a card upgraded and removed in the same room can still be rolled back.
        for i, raw in enumerate(stats.get("cards_removed", [])):
            deck.append(_wrap(raw, f"removed:{floor}:{i}"))
        for i, transform in reversed(list(enumerate(stats.get("cards_transformed", [])))):
            final = transform["final_card"]
            match = _card_match(deck, final, floor, issues)
            if match:
                deck.remove(match)
            deck.append(_wrap(transform["original_card"], f"transformed:{floor}:{i}"))
        for i, enchanted in reversed(list(enumerate(stats.get("cards_enchanted", [])))):
            raw = enchanted["card"]
            match = _card_match(deck, raw, floor, issues)
            if match:
                previous = _previous_enchantment(rows, raw, floor, i)
                match["raw"].pop("enchantment", None)
                if previous:
                    match["raw"]["enchantment"] = previous
                if raw.get("floor_added_to_deck") is None:
                    issues.append(f"第 {floor} 层：{model_entry(raw)} 的原附魔信息不完整，已按无附魔还原，请核对。")
        for key, delta in (("upgraded_cards", -1), ("downgraded_cards", 1)):
            for value in reversed(stats.get(key, [])):
                match = _card_match(deck, {"id": value}, floor, issues, upgrade=delta == -1, rows=rows)
                if match:
                    match["raw"]["current_upgrade_level"] = max(0, match["raw"].get("current_upgrade_level", 0) + delta)
        for raw in reversed(stats.get("cards_gained", [])):
            match = _card_match(deck, {**raw, "floor_added_to_deck": floor}, floor, issues)
            if match:
                deck.remove(match)
        for i, removed in enumerate(stats.get("relics_removed", [])):
            acquired = [r["floor"] for r in rows[:floor] if model_entry(removed) in _picked(r["stats"], "relic")]
            restored = _wrap({"id": _model(removed, "RELIC")}, f"relic-removed:{floor}:{i}", acquired[-1] if acquired else 1)
            restored["stateUnavailable"] = True
            relics.append(restored)
        for value in reversed(_picked(stats, "relic")):
            matches = [r for r in relics if model_entry(r["raw"]) == value]
            exact = [r for r in matches if r["raw"].get("floor_added_to_deck") == floor]
            unknown = [r for r in matches if r["raw"].get("floor_added_to_deck") is None]
            if exact or unknown:
                relics.remove((exact or unknown)[-1])
            else:
                issues.append(f"第 {floor} 层：遗物 {value} 的获得记录与最终清单不一致；保留已确认的较早实例，请核对。")
        gained = _picked(stats, "potion")
        used = [model_entry(p) for p in stats.get("potion_used", []) + stats.get("potion_discarded", [])]
        if set(gained) & set(used):
            issues.append(f"第 {floor} 层：同种药水既获得又消耗，记录没有操作顺序，请核对药水。")
        # Restore consumed potions before removing acquisitions; this handles a
        # potion obtained and consumed within the same floor without inventing one.
        for i, value in enumerate(used):
            potions.append(_wrap({"id": _model(value, "POTION")}, f"potion-used:{floor}:{i}"))
        for value in reversed(gained):
            match = next((p for p in reversed(potions) if model_entry(p["raw"]) == value), None)
            if match:
                potions.remove(match)
            else:
                issues.append(f"第 {floor} 层：无法匹配药水 {value} 的获得记录，请核对药水。")
    if cutoff < len(rows):
        # Acquisition floors remain authoritative even when a mod omits reward logs.
        relics = [r for r in relics if (r["raw"].get("floor_added_to_deck") or 1) <= max(1, cutoff)]
        relics.sort(key=lambda r: r["raw"].get("floor_added_to_deck") or 1)
    return deck, relics, potions


def _annotate_relic_states(relics: list[dict[str, Any]], historical: bool) -> None:
    for relic in relics:
        props = relic["raw"].setdefault("props", {})
        fields = relic_fields(model_entry(relic["raw"]))
        known = {(field["group"], field["name"]): field for field in fields}
        # Include saved mod counters too, even when no native UI schema exists.
        for group in ("ints", "bools", "strings"):
            for entry in props.get(group, []):
                known.setdefault((group, entry["name"]), {"group": group, "name": entry["name"]})
        sources = {}
        unknown = []
        for (group, name), field in known.items():
            saved = next((entry for entry in props.get(group, []) if entry["name"] == name), None)
            value = saved["value"] if saved else field.get("default")
            unrecorded = relic.get("stateUnavailable", False) or (saved is None and name not in {"IsWax", "IsMelted"} and not field.get("omitDefault"))
            if historical and not field.get("stable") and not (field.get("monotonic") and value is False):
                unrecorded = True
            if unrecorded:
                unknown.append({"group": group, "name": name})
                if saved:
                    props[group].remove(saved)
                    if not props[group]:
                        del props[group]
                sources[name] = "unknown"
            else:
                sources[name] = "history" if historical else "save"
        relic["defaultedProps"] = unknown
        relic["propertySources"] = sources


def reconstruct(source_id: str, selection: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _read(_source_path(source_id))
    selection = selection or {}
    if not isinstance(selection, dict):
        raise SaveImportError("楼层选择必须是对象")
    try:
        player_index = int(selection.get("player", 0))
        player = data["players"][player_index] if player_index >= 0 else None
    except (ValueError, TypeError, IndexError) as exc:
        raise SaveImportError("所选玩家无效") from exc
    if player is None:
        raise SaveImportError("所选玩家无效")
    current = "character_id" in player
    rows = _flatten(data, player_index)
    try:
        floor = int(selection.get("floor", len(rows) or 1))
    except (ValueError, TypeError) as exc:
        raise SaveImportError("楼层必须为整数") from exc
    point = selection.get("point", "current" if current else "after")
    if point not in {"before", "after", "current"} or (point == "current" and not current):
        raise SaveImportError("请选择进入本层前或完成本层后")
    if point != "current" and not 1 <= floor <= len(rows):
        raise SaveImportError(f"楼层必须在 1 到 {len(rows)} 之间")
    cutoff = len(rows) if point == "current" else floor - (point == "before")
    row = rows[floor - 1] if rows and 1 <= floor <= len(rows) else None
    issues: list[str] = []
    deck, relics, potions = _reverse_inventory(player, rows, cutoff, issues)
    character = model_entry(player.get("character_id", player.get("character")))
    acts = [model_entry(a) for a in data["acts"]]
    act = int(data.get("current_act_index", len(data.get("map_point_history", [])) - 1)) if point == "current" else row["act"]
    act = max(0, act)
    if act >= len(acts):
        raise SaveImportError("所选阶段超出存档的阶段列表")
    act_floor = len(data.get("map_point_history", [[]])[act]) if point == "current" and act < len(data.get("map_point_history", [])) else row["actFloor"] if row else 1
    historical = point != "current" and (cutoff < len(rows) or current)
    if historical:
        for values, kind in ((deck, "卡牌"),):
            for value in values:
                raw = value["raw"]
                # Card configuration stays with the instance; only unrecorded
                # counters reset when reversing floor history.
                props = raw.get("props") or {}
                if props:
                    stable = STABLE_CARD_PROPERTIES.get(model_entry(raw), set())
                    value["defaultedProps"] = []
                    for group in ("ints", "floats", "bools"):
                        kept = []
                        for prop in props.get(group, []):
                            if group == "ints" and prop["name"] in stable:
                                kept.append(prop)
                            else:
                                value["defaultedProps"].append({"group": group, "name": prop["name"]})
                        if kept:
                            props[group] = kept
                        else:
                            props.pop(group, None)
                    if value["defaultedProps"] and not growth_card_fields(model_entry(raw)):
                        issues.append(f"{kind} {model_entry(raw)}：没有逐层保存计数和开关；未记录值使用游戏默认值，固定配置沿用存档，请核对。")
                if growth := growth_card_fields(model_entry(raw)):
                    unknown = value.setdefault("defaultedProps", [])
                    for name in (growth["current"], growth["increased"]):
                        if not any(field["name"] == name for field in unknown):
                            unknown.append({"group": "ints", "name": name})
                    issues.append(f"卡牌 {model_entry(raw)}：历史楼层未记录成长值，请在卡面填写{growth['name']}后评估。")
        issues.append("历史层没有完整随机数状态；评估使用当前样本种子建立战斗随机状态。")
        issues.append("历史记录按类别保存行为，缺少同层操作顺序；复杂变形、同名牌及未记录的模组变化可能需要手动修正。")
        issues.append("历史层没有逐层角色额外状态，使用新跑局的基础状态；例如任务标记和模组扩展需另行核对。")
    _annotate_relic_states(relics, historical)
    for kind, inventory in (("deck", deck), ("relics", relics), ("potions", potions)):
        for value in inventory:
            if _unsupported_fields(value["raw"], kind):
                issues.append(f"{model_entry(value['raw'])} 含当前宿主无法恢复的模组保存属性；评估会明确报错，请先移除或适配。")
    stats = {"hp": player.get("current_hp"), "maxHp": player.get("max_hp"), "gold": player.get("gold"),
             "maxEnergy": player.get("max_energy", 3), "potionSlots": player.get("max_potion_slot_count", 3),
             "orbSlots": player.get("base_orb_slot_count", 3 if character == "DEFECT" else 0)}
    if point != "current":
        end = row["stats"]
        if point == "after":
            stats.update(hp=end.get("current_hp"), maxHp=end.get("max_hp"), gold=end.get("current_gold"))
        elif floor > 1 and rows[floor - 2]["act"] == act:
            previous = rows[floor - 2]["stats"]
            stats.update(hp=previous.get("current_hp"), maxHp=previous.get("max_hp"), gold=previous.get("current_gold"))
        else:
            max_hp = end.get("max_hp", 1) - end.get("max_hp_gained", 0) + end.get("max_hp_lost", 0)
            hp = end.get("current_hp", 0) + end.get("damage_taken", 0) - end.get("hp_healed", 0)
            if floor == 1:
                hp = max_hp
                issues.append("第 1 层前的生命未单独记录，暂按初始满血；自定义规则或模组可能改变初始状态。")
            else:
                issues.append("跨幕进入本层前的治疗未单独存档，生命由本层收支回推，请核对。")
            stats.update(hp=max(0, min(max_hp, hp)), maxHp=max_hp,
                         gold=end.get("current_gold", 0) - end.get("gold_gained", 0) + end.get("gold_spent", 0) + end.get("gold_lost", 0))
        # History has no per-floor energy / capacity fields. Use native starting
        # capacity, with the potion belt's explicit permanent capacity effect.
        if historical or not current:
            stats["maxEnergy"] = 3
            stats["potionSlots"] = 3 - int(int(data.get("ascension", 0)) >= 4)
            if any(model_entry(r["raw"]) == "POTION_BELT" for r in relics):
                stats["potionSlots"] += 2
            issues.append("历史层未记录能量上限、药水槽和充能球槽的逐层变化；暂按基础值和药水腰带修正，可直接调整。")
    if any(value is None for value in stats.values()):
        raise SaveImportError("所选楼层缺少生命或金币数据")
    if len(data["players"]) > 1:
        issues.append("当前离线评估只使用所选玩家；多人队伍、协作机制和多人怪物缩放不包含在此状态中。")
    if point != "current" and current and cutoff == len(rows):
        issues.append("当前存档的末层可能尚未结算；牌组取自当前快照，生命和金币取自楼层记录。可切换到存档当前状态。")
    # Reconstructed potion slots are not recorded in floor logs. Keep exact
    # native slots for current saves; allocate historical instances sequentially.
    if point != "current":
        for i, potion in enumerate(potions):
            potion["raw"]["slot_index"] = i
    if len(potions) > stats["potionSlots"]:
        issues.append("还原的药水数量超过药水槽，请移除多余药水或调整槽数后再评估。")
    public_deck = []
    for card in deck:
        raw = card["raw"]
        enchant = raw.get("enchantment") or {}
        public_deck.append({"id": model_entry(raw), "ref": card["ref"], "upgrade": raw.get("current_upgrade_level", 0),
                            "enchantmentId": model_entry(enchant.get("id")), "enchantmentAmount": enchant.get("amount", ""),
                            "savedProps": copy.deepcopy(raw.get("props", {})), "defaultedProps": card.get("defaultedProps", []), "floorAdded": raw.get("floor_added_to_deck")})
    path = _source_path(source_id)
    meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    source = {"id": source_id, **meta, "kind": "current" if current else "history", "floors": len(rows),
              "players": [{"index": i, "character": model_entry(p.get("character_id", p.get("character")))} for i, p in enumerate(data["players"])]}
    return {"source": source, "selection": {"floor": floor, "point": point, "player": player_index},
            "character": character, "seed": str(data.get("seed", data.get("rng", {}).get("seed", "COMBATLAB"))),
            "ascension": data.get("ascension", 0), "act": act, "actId": acts[act], "actFloor": max(1, act_floor),
            "stats": stats, "deck": public_deck,
            "relics": [{"id": model_entry(r["raw"]), "ref": r["ref"], "savedProps": copy.deepcopy(r["raw"].get("props", {})),
                        "defaultedProps": r.get("defaultedProps", []), "propertySources": r.get("propertySources", {}),
                        "floorAdded": r["raw"].get("floor_added_to_deck")} for r in relics],
            "potions": [{"id": model_entry(p["raw"]), "ref": p["ref"], "slot": p["raw"].get("slot_index", i)} for i, p in enumerate(potions)],
            "history": [_public_row(r) for r in rows], "selectedFloor": _public_row(row) if row else None,
            "issues": list(dict.fromkeys(issues)), "_inventory": {"deck": deck, "relics": relics, "potions": potions},
            "_history": [copy.deepcopy(act_rows[:max(0, cutoff - sum(len(a) for a in data.get("map_point_history", [])[:i]))])
                         for i, act_rows in enumerate(data.get("map_point_history", [])[:act + 1])]}


def public_state(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if not k.startswith("_")}


def _built_saved_deck(cards: list[Any]) -> list[dict[str, Any]]:
    """Restore saved card setters through CardModel.FromSerializable at room entry."""
    deck = []
    for value in cards:
        value = {"id": value} if isinstance(value, str) else value
        if not isinstance(value, dict) or not model_entry(value.get("id", value.get("cardId"))):
            raise SaveImportError("卡牌缺少 id")
        card_id = model_entry(value.get("id", value.get("cardId")))
        props = normalise_card_props(card_id, value.get("savedProps") or {})
        if value.get("dynamicVars"):
            # SerializableCard deliberately has no arbitrary DynamicVars field.
            # Never silently reset a caller's explicit dynamic overrides.
            raise SaveImportError(f"{card_id}：保存属性请使用 savedProps；无法同时恢复未序列化的 dynamicVars 覆盖")
        if value.get("afflictionId") or (value.get("enumMembers") and card_id != "MAD_SCIENCE"):
            raise SaveImportError(f"{card_id}：当前卡牌覆盖项无法随保存属性序列化，请移除覆盖项后评估")
        if card_id == "MAD_SCIENCE":
            members = mad_science_members(value)
            fields = {field["name"]: field["value"] for field in props.get("ints", [])}
            for name, choices in (("TinkerTimeType", MAD_SCIENCE_TYPES), ("TinkerTimeRider", MAD_SCIENCE_RIDERS)):
                fields[name] = next(key for key, enum in choices.items() if enum == members[name])
            props["ints"] = [{"name": key, "value": val} for key, val in fields.items()]
        validate_card_props(card_id, props)
        raw = {"id": _model(card_id, "CARD"), "current_upgrade_level": int(value.get("upgrade", value.get("upgradeLevel", value.get("upgradeLevels", 0))))}
        if props:
            raw["props"] = props
        if value.get("enchantmentId"):
            raw["enchantment"] = {"id": _model(value["enchantmentId"], "ENCHANTMENT"), "amount": int(value.get("enchantmentAmount") or 1)}
        if value.get("floorAdded") is not None:
            raw["floor_added_to_deck"] = int(value["floorAdded"])
        count = value.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise SaveImportError("卡牌数量必须是正整数")
        deck.extend(copy.deepcopy(raw) for _ in range(count))
    return deck


def materialize_plan(plan: dict[str, Any], output: Path) -> dict[str, Any]:
    """Freeze edited instances for the native harness without reapplying pickup effects."""
    imported = plan.get("saveImport")
    if not imported:
        restore_cards = any(isinstance(card, dict) and card.get("savedProps") for card in plan.get("deck", []))
        if (plan.get("playerStats") or restore_cards) and str(plan.get("mode", "build")) == "build":
            stats = plan.get("playerStats") or {}
            fields = {key: int(stats[field]) for key, field in (("current_hp", "hp"), ("max_hp", "maxHp"),
                      ("gold", "gold"), ("max_energy", "maxEnergy"), ("max_potion_slot_count", "potionSlots"), ("base_orb_slot_count", "orbSlots"))
                      if field in stats}
            if restore_cards:
                fields["deck"] = _built_saved_deck(plan.get("deck", []))
            output.mkdir(parents=True, exist_ok=True)
            target = output / "built-player-state.json"
            target.write_text(json.dumps({"schemaVersion": 1, "player": fields}), encoding="utf-8")
            return {**plan, "_playerStatePath": str(target)}
        return plan
    if not isinstance(imported, dict):
        raise SaveImportError("saveImport 必须是对象")
    source_id = str(imported.get("sourceId", ""))
    restored = reconstruct(source_id, imported.get("selection"))
    refs = {entry["ref"]: entry["raw"] for values in restored["_inventory"].values() for entry in values}
    unknown_growth_refs = {entry["ref"] for entry in restored["_inventory"]["deck"]
                           if growth_card_fields(model_entry(entry["raw"])) and entry.get("defaultedProps")}
    loadout = {}
    for key, category in (("deck", "CARD"), ("relics", "RELIC"), ("potions", "POTION")):
        values = plan.get(key, restored[key])
        if not isinstance(values, list):
            raise SaveImportError(f"{key} 必须是列表")
        loadout[key] = []
        for i, value in enumerate(values):
            value = {"id": value} if isinstance(value, str) else value
            if not isinstance(value, dict) or not model_entry(value):
                raise SaveImportError(f"{key} 中的物品缺少 id")
            ref = value.get("ref")
            if ref and ref not in refs:
                raise SaveImportError(f"保存的 {key} 引用已失效，请重新导入所选楼层")
            raw = copy.deepcopy(refs[ref]) if ref else {"id": _model(value, category)}
            if model_entry(raw) != model_entry(value):
                raise SaveImportError("导入物品的引用和 ID 不匹配")
            if "savedProps" in value:
                if not isinstance(value["savedProps"], dict):
                    raise SaveImportError("保存属性必须是 JSON 对象")
                raw["props"] = copy.deepcopy(value["savedProps"])
            if key == "deck":
                try:
                    raw["props"] = normalise_card_props(model_entry(raw), raw.get("props") or {})
                    validate_card_props(model_entry(raw), raw.get("props", {}))
                    growth = growth_card_fields(model_entry(raw))
                    if ref in unknown_growth_refs and growth and not any(p["name"] == growth["current"] for p in raw["props"].get("ints", [])):
                        raise ValueError(f"{model_entry(raw)}：历史楼层未记录成长值，请先填写{growth['name']}")
                except ValueError as exc:
                    raise SaveImportError(str(exc)) from exc
                raw["current_upgrade_level"] = int(value.get("upgrade", raw.get("current_upgrade_level", 0)))
                enchant_id = value.get("enchantmentId", model_entry((raw.get("enchantment") or {}).get("id")))
                if enchant_id:
                    enchant = copy.deepcopy(raw.get("enchantment") or {})
                    if model_entry(enchant.get("id")) != model_entry(enchant_id):
                        enchant = {}
                    enchant.update(id=_model(enchant_id, "ENCHANTMENT"), amount=int(value.get("enchantmentAmount") or 1))
                    raw["enchantment"] = enchant
                else:
                    raw.pop("enchantment", None)
            if key == "potions":
                raw["slot_index"] = int(value.get("slot", i))
            if key == "relics":
                validate_relic_props(model_entry(raw), raw.get("props", {}))
            loadout[key].append(raw)
    stats = {**restored["stats"], **plan.get("playerStats", {})}
    stats = {k: int(v) for k, v in stats.items()}
    if stats["maxHp"] < 1 or not 1 <= stats["hp"] <= stats["maxHp"]:
        raise SaveImportError("评估前生命必须在 1 到最大生命之间；死亡存档请先调整生命")
    if stats["gold"] < 0 or stats["maxEnergy"] < 0 or stats["potionSlots"] < 0 or stats["orbSlots"] < 0:
        raise SaveImportError("金币、能量和槽数不能为负数")
    slots = [p["slot_index"] for p in loadout["potions"]]
    if len(slots) != len(set(slots)) or any(s < 0 or s >= stats["potionSlots"] for s in slots):
        raise SaveImportError("药水槽无效或重复，请调整药水数量和槽数")
    if not loadout["deck"]:
        raise SaveImportError("评估牌组不能为空")
    player = {**loadout, **{key: stats[field] for key, field in (("current_hp", "hp"), ("max_hp", "maxHp"), ("gold", "gold"),
              ("max_energy", "maxEnergy"), ("max_potion_slot_count", "potionSlots"), ("base_orb_slot_count", "orbSlots"))}}
    for kind, values in loadout.items():
        for raw in values:
            unsupported = _unsupported_fields(raw, kind)
            if unsupported:
                raise SaveImportError(f"{model_entry(raw)} 含未支持的模组保存字段：{', '.join(unsupported)}")
    # Native history player identities are remapped to the newly generated solo player.
    history = restored["_history"]
    original = _read(_source_path(source_id))["players"][restored["selection"]["player"]]
    if restored["selection"]["point"] == "current" and "extra_fields" in original:
        player["extra_fields"] = copy.deepcopy(original["extra_fields"])
    source_player_id = str(original.get("net_id", original.get("id")))
    for act in history:
        for entry in act:
            entry["player_stats"] = [s for s in entry.get("player_stats", []) if str(s.get("player_id")) == source_player_id]
            for stat in entry["player_stats"]:
                stat["player_id"] = 1
    artifact = {"schemaVersion": 1, "player": player, "map_point_history": history,
                "actFloor": restored["actFloor"] + int(restored["selection"]["point"] == "after"),
                "provenance": {"sourceId": source_id, "selection": restored["selection"], "issues": restored["issues"]}}
    output.mkdir(parents=True, exist_ok=True)
    target = output / "imported-player-state.json"
    target.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**plan, "mode": "build", "character": plan.get("character", restored["character"]),
            "act": plan.get("act", restored["act"]), "actId": plan.get("actId", restored["actId"]),
            "_playerStatePath": str(target), "_importProvenance": artifact["provenance"]}
