"""Native card values and persistent per-instance card configuration."""

from __future__ import annotations

import copy
from functools import lru_cache
import json
from pathlib import Path
from typing import Any


MAD_SCIENCE_TYPES = {1: "Attack", 2: "Skill", 3: "Power"}
MAD_SCIENCE_RIDERS = {
    0: "None", 1: "Sapping", 2: "Violence", 3: "Choking", 4: "Energized",
    5: "Wisdom", 6: "Chaos", 7: "Expertise", 8: "Curious", 9: "Improvement",
}
STABLE_CARD_PROPERTIES = {"MAD_SCIENCE": {"TinkerTimeType", "TinkerTimeRider"}}

GROWTH_CARD_FIELDS = {
    "THE_SCYTHE": {"name": "当前伤害", "name_en": "Current damage", "var": "Damage",
                   "current": "CurrentDamage", "increased": "IncreasedDamage", "base": 13},
    "GENETIC_ALGORITHM": {"name": "当前格挡", "name_en": "Current block", "var": "Block",
                          "current": "CurrentBlock", "increased": "IncreasedBlock", "base": 1},
}
MAX_CARD_VALUE = 999_999_999  # DynamicVar.BaseValue's native upper bound.


@lru_cache(maxsize=1)
def native_card_metadata() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "combat_lab" / "card_values.json"
    return json.loads(path.read_text(encoding="utf-8"))


def native_card_values() -> dict[str, Any]:
    return native_card_metadata()["cards"]


def growth_card_fields(card_id: str) -> dict[str, Any] | None:
    schema = GROWTH_CARD_FIELDS.get(card_id)
    if schema is None:
        return None
    values = native_card_values().get(card_id, {}).get("levels", {}).get("0", {}).get("vars", {})
    return {**schema, "base": values.get(schema["var"], schema["base"]), "max": MAX_CARD_VALUE}


def enrich_card_catalog(catalog: dict[str, Any]) -> None:
    """Consume metadata exported from mutable base/upgraded game models."""
    catalog.setdefault("ui", {})["cardKeywords"] = native_card_metadata()["keywords"]
    for card_id, values in native_card_values().items():
        card = catalog.get("cards", {}).get(card_id)
        if card is None:
            continue
        card["numericLevels"] = values["levels"]
        card["maxUpgradeLevel"] = values["maxUpgradeLevel"]
        if growth := growth_card_fields(card_id):
            card["growth"] = growth
    catalog["catalogVersion"] = max(6, catalog.get("catalogVersion", 1))


def normalise_card_props(card_id: str, props: dict[str, Any]) -> dict[str, Any]:
    """Keep a growth card's current value and accumulated growth synchronized."""
    if not isinstance(props, dict):
        raise ValueError("卡牌保存属性必须是 JSON 对象")
    result = copy.deepcopy(props)
    for group in ("ints", "bools", "strings", "floats"):
        fields = result.get(group, [])
        if not isinstance(fields, list) or any(not isinstance(field, dict) or not isinstance(field.get("name"), str) for field in fields):
            raise ValueError(f"卡牌保存属性 {group} 必须是带名称的属性列表")
        names = [field["name"] for field in fields]
        if len(names) != len(set(names)):
            raise ValueError(f"卡牌保存属性 {group} 中存在重复字段")
    growth = growth_card_fields(card_id)
    if not growth:
        return result
    fields = result.get("ints", [])
    values = {field["name"]: field.get("value") for field in fields}
    current, increased, base = growth["current"], growth["increased"], growth["base"]
    for name in (current, increased):
        if name in values and (isinstance(values[name], bool) or not isinstance(values[name], int)):
            raise ValueError(f"{growth['name']}必须是整数")
    if current not in values and increased not in values:
        return result
    amount = values.get(current, base + values.get(increased, 0))
    if amount < base or amount > MAX_CARD_VALUE:
        raise ValueError(f"{growth['name']}必须在 {base} 到 {MAX_CARD_VALUE} 之间")
    if increased in values and values[increased] != amount - base:
        raise ValueError(f"{growth['name']}与累计成长值不一致，请在卡面重新设置当前数值")
    values[current], values[increased] = amount, amount - base
    result["ints"] = [{"name": name, "value": value} for name, value in values.items()]
    return result


def mad_science_members(raw: dict[str, Any]) -> dict[str, str]:
    props = raw.get("savedProps") or {}
    if not isinstance(props, dict) or not isinstance(props.get("ints", []), list):
        raise ValueError("疯狂科学的保存属性必须包含有效的整数列表")
    members = dict(raw.get("enumMembers") or {})
    fields = {"TinkerTimeType": MAD_SCIENCE_TYPES, "TinkerTimeRider": MAD_SCIENCE_RIDERS}
    seen: set[str] = set()
    for prop in props.get("ints", []):
        if not isinstance(prop, dict) or prop.get("name") not in fields:
            continue
        name, value = prop["name"], prop.get("value")
        if name in seen or isinstance(value, bool) or not isinstance(value, int) or value not in fields[name]:
            raise ValueError(f"疯狂科学的 {name} 值无效")
        seen.add(name)
        members[name] = fields[name][value]
    for name, choices in fields.items():
        if name not in members:
            if name == "TinkerTimeRider":
                members[name] = "None"
                continue
            raise ValueError("疯狂科学缺少卡牌类型，请指定攻击、技能或能力")
        value = str(members[name])
        match = next((choice for number, choice in choices.items() if value.lower() in {str(number), choice.lower()}), None)
        if match is None:
            raise ValueError(f"疯狂科学的 {name} 值无效")
        members[name] = match
    type_value = next(value for value, name in MAD_SCIENCE_TYPES.items() if name == members["TinkerTimeType"])
    rider_value = next(value for value, name in MAD_SCIENCE_RIDERS.items() if name == members["TinkerTimeRider"])
    if rider_value and not (type_value - 1) * 3 < rider_value <= type_value * 3:
        raise ValueError("疯狂科学的附加效果与卡牌类型不匹配")
    return members


def validate_card_props(card_id: str, props: dict[str, Any]) -> None:
    props = normalise_card_props(card_id, props)
    if card_id == "MAD_SCIENCE":
        mad_science_members({"savedProps": props})
