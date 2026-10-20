"""Editable native SavedProperty fields for relic instances (STS2 0.111.0)."""

from __future__ import annotations

import copy
from typing import Any


def _counter(name: str, label: str, label_en: str, *, default: int = 0,
             maximum: int | None = None, cycle: int | None = None) -> dict[str, Any]:
    field = {"group": "ints", "name": name, "label": label, "label_en": label_en,
             "default": default, "min": 0}
    if maximum is not None:
        field["max"] = maximum
    if cycle is not None:
        field["cycle"] = cycle
    return field


def _flag(name: str, label: str, label_en: str, true_label: str, false_label: str,
          true_en: str, false_en: str, *, stable: bool = False,
          monotonic: bool = False) -> dict[str, Any]:
    return {"group": "bools", "name": name, "label": label, "label_en": label_en,
            "default": False, "trueLabel": true_label, "falseLabel": false_label,
            "trueLabel_en": true_en, "falseLabel_en": false_en,
            "stable": stable, "monotonic": monotonic}


COMMON_FIELDS = [
    _flag("IsWax", "蜡制遗物", "Wax relic", "蜡制", "普通", "Wax", "Normal", stable=True),
    {**_flag("IsMelted", "已融化（失效）", "Melted (inactive)", "已融化 · 失效", "未融化 · 有效",
             "Melted · inactive", "Unmelted · active", monotonic=True), "requires": "IsWax"},
]

RELIC_FIELDS = {
    "TUNING_FORK": [_counter("SkillsPlayed", "已累计打出的技能牌", "Skills played", maximum=9, cycle=10)],
    "LIZARD_TAIL": [_flag("WasUsed", "复活机会已用尽", "Revival already used", "已使用", "可用",
                           "Used", "Available", monotonic=True)],
    "HAPPY_FLOWER": [_counter("TurnsSeen", "回合计数", "Turn counter", maximum=2, cycle=3)],
    "FAKE_HAPPY_FLOWER": [_counter("TurnsSeen", "回合计数", "Turn counter", maximum=4, cycle=5)],
    "PENDULUM": [_counter("TurnsSeen", "回合计数", "Turn counter", maximum=2, cycle=3)],
    "POLLINOUS_CORE": [_counter("TurnsSeen", "回合计数", "Turn counter", maximum=3, cycle=4)],
    "NUNCHAKU": [_counter("AttacksPlayed", "累计打出的攻击牌", "Total attacks played", cycle=10)],
    "PEN_NIB": [_counter("AttacksPlayed", "攻击牌计数", "Attack counter", maximum=9, cycle=10)],
    "IRON_CLUB": [_counter("CardsPlayed", "累计打出的卡牌", "Total cards played", cycle=4)],
    "GALACTIC_DUST": [_counter("StarsSpent", "已累计消耗的星能", "Stars spent", maximum=9, cycle=10)],
    "JOSS_PAPER": [{**_counter("CardsExhausted", "消耗卡牌计数", "Cards exhausted", maximum=4, cycle=5), "omitDefault": True}],
    "BOOK_OF_FIVE_RINGS": [_counter("CardsAdded", "累计加入牌组的卡牌", "Cards added to deck", cycle=5)],
    "LASTING_CANDY": [_counter("CombatRewardsSeen", "已获得的战斗奖励次数", "Combat rewards received", cycle=2)],
    "PAELS_WING": [_counter("RewardsSacrificed", "累计献祭的奖励次数", "Rewards sacrificed", cycle=2)],
    "FISHING_ROD": [_counter("CombatsSeen", "普通战斗计数", "Normal combats completed", cycle=3)],
    "TOY_BOX": [_counter("CombatsSeen", "已完成战斗次数", "Combats completed")],
    "WINGED_BOOTS": [_counter("TimesUsed", "已使用次数", "Uses spent", maximum=3)],
    "GIRYA": [_counter("TimesLifted", "举重次数", "Lifts completed", maximum=3)],
    "SILVER_CRUCIBLE": [_counter("TimesUsed", "已使用次数", "Uses spent", maximum=3),
                        _counter("TreasureRoomsEntered", "已进入宝箱房次数", "Treasure rooms entered")],
    "SWORD_OF_STONE": [_counter("ElitesDefeated", "已击败精英次数", "Elites defeated")],
    "PUMPKIN_CANDLE": [_counter("KindleCount", "剩余生效战斗次数", "Active combats remaining")],
    "WONGOS_MYSTERY_TICKET": [
        _counter("CombatsFinished", "已完成战斗次数", "Combats completed"),
        _flag("GaveRelic", "奖励遗物已领取", "Reward relics claimed", "已领取", "未领取", "Claimed", "Unclaimed", monotonic=True),
    ],
    "BONE_TEA": [_counter("CombatsLeft", "剩余生效战斗次数", "Combats remaining", default=1, maximum=1)],
    "EMBER_TEA": [_counter("CombatsLeft", "剩余生效战斗次数", "Combats remaining", default=5, maximum=5)],
    "TEA_OF_DISCOURTESY": [_counter("CombatsLeft", "剩余生效战斗次数", "Combats remaining", default=1, maximum=1)],
    "VENERABLE_TEA_SET": [_flag("GainEnergyInNextCombat", "下场战斗获得额外能量", "Extra energy next combat",
                                "下场生效", "未激活", "Active next combat", "Inactive")],
    "FAKE_VENERABLE_TEA_SET": [_flag("GainEnergyInNextCombat", "下场战斗获得额外能量", "Extra energy next combat",
                                     "下场生效", "未激活", "Active next combat", "Inactive")],
    "MAW_BANK": [_flag("HasItemBeenBought", "已在商店消费（失效）", "Spent gold at a shop (inactive)",
                       "已失效", "有效", "Inactive", "Active", monotonic=True)],
    "SILKEN_TRESS": [_flag("IsUsed", "首次奖励附魔已触发", "First reward enchantment used",
                           "已触发", "未触发", "Triggered", "Not triggered", monotonic=True)],
    "LAVA_ROCK": [_flag("HasTriggered", "额外遗物奖励已触发", "Extra relic reward used",
                        "已触发", "未触发", "Triggered", "Not triggered", monotonic=True)],
    "LAVA_LAMP": [_flag("TookDamageThisCombat", "本场战斗已受到伤害", "Damage taken this combat",
                        "已受伤", "未受伤", "Damaged", "Undamaged")],
}


def relic_fields(relic_id: str) -> list[dict[str, Any]]:
    return copy.deepcopy(RELIC_FIELDS.get(relic_id, []) + COMMON_FIELDS)


def property_value(props: dict[str, Any], group: str, name: str, default: Any = None) -> Any:
    return next((entry["value"] for entry in props.get(group, []) if entry.get("name") == name), default)


def validate_relic_props(relic_id: str, props: Any) -> None:
    if not isinstance(props, dict):
        raise ValueError("遗物状态必须是 JSON 对象")
    schemas = {(f["group"], f["name"]): f for f in relic_fields(relic_id)}
    for group, entries in props.items():
        if group.startswith("save_dict_"):
            continue
        if not isinstance(entries, list):
            raise ValueError(f"遗物 {relic_id} 的 {group} 必须是属性列表")
        names = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or "value" not in entry:
                raise ValueError(f"遗物 {relic_id} 的状态格式无效")
            name, value = entry["name"], entry["value"]
            if name in names:
                raise ValueError(f"遗物 {relic_id} 的状态 {name} 重复")
            names.add(name)
            field = schemas.get((group, name))
            if group == "bools" and not isinstance(value, bool):
                raise ValueError(f"遗物 {relic_id} 的 {name} 必须是开关值")
            if group == "ints":
                if isinstance(value, bool) or not isinstance(value, int) or not -2147483648 <= value <= 2147483647:
                    raise ValueError(f"遗物 {relic_id} 的 {name} 必须是有效整数")
                if field and not field.get("min", value) <= value <= field.get("max", value):
                    raise ValueError(f"{field['label']} 超出允许范围")
    if property_value(props, "bools", "IsMelted", False) and not property_value(props, "bools", "IsWax", False):
        raise ValueError("只有蜡制遗物可以设置为已融化")
