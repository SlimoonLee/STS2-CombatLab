"""Generate runtime catalog, card values and artwork from an installed game.

The checked-in schema contains model IDs and localization/resource references.
Game localization, textures, compiled assemblies and saves stay on this machine.
No decompiled source tree or STS2 MCP installation is required.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

from build import ASSETS, CATALOG, DEFAULT_PCK, HERE, ROOT, run_gdre

sys.path.insert(0, str(ROOT))
from combat_lab_cards import MAD_SCIENCE_RIDERS, MAD_SCIENCE_TYPES


def split_top(text: str, separator: str) -> list[str]:
    parts, start, depth = [], 0, 0
    for index, char in enumerate(text):
        if char in "{(":
            depth += 1
        elif char in "})":
            depth -= 1
        elif char == separator and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    return [*parts, text[start:]]


def render_description(text: str, values: dict, language: str) -> str:
    """Resolve the original Mad Science conditional/value formatters."""
    rendered, index = [], 0
    while index < len(text):
        if text[index] != "{":
            rendered.append(text[index])
            index += 1
            continue
        end, depth = index + 1, 1
        while end < len(text) and depth:
            depth += (text[end] == "{") - (text[end] == "}")
            end += 1
        if depth:
            raise ValueError("Unbalanced localization expression")
        args = split_top(text[index + 1:end - 1], ":")
        value = values[args[0]]
        if len(args) == 1 or args[1] == "diff()":
            result = str(value)
        elif args[1].startswith("choose("):
            choices = args[1][7:-1].split("|")
            options = split_top(":".join(args[2:]), "|")
            result = options[choices.index(str(value)) if str(value) in choices else -1]
        elif args[1].startswith("energyIcons("):
            unit = "能量" if language == "zhs" else "Energy"
            result = unit if args[1] == "energyIcons(1)" else f"{value} {unit}"
        else:
            options = split_top(":".join(args[1:]), "|")
            result = options[0] if value else (options[1] if len(options) > 1 else "")
        rendered.append(render_description(result, values, language) if "{" in result else result)
        index = end
    return re.sub(r"\[/?[^\]]+\]", "", "".join(rendered))


def resolve(value: object, tables: dict) -> object:
    if isinstance(value, dict):
        if "$loc" in value:
            language, table, key = value["$loc"]
            return tables[language][table][key]
        if "$keywordDescription" in value:
            language, keywords = value["$keywordDescription"]
            table = tables[language]["card_keywords"]
            separator = table["PERIOD"] + (" " if language == "eng" else "")
            return separator.join(table[key + ".title"] for key in keywords)
        return {key: resolve(item, tables) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, tables) for item in value]
    return value


def complete_catalog(catalog: dict, tables: dict, native: dict) -> None:
    for encounter in catalog["encounters"].values():
        for language, suffix in (("zhs", ""), ("eng", "_en")):
            encounter["monsters" + suffix] = [catalog["monsters"].get(identity, {}).get("name" + suffix, identity)
                                               for identity in encounter.get("monsterIds", [])]
    acts = {variant["id"]: variant for act in catalog["acts"] for variant in act["variants"]}
    labels = {
        "zhs": {"all": "全部", "normal": "小怪", "weak": "弱怪池", "strong": "强怪池", "elite": "精英池", "boss": "Boss 池"},
        "eng": {"all": "All", "normal": "Normal", "weak": "Weak pool", "strong": "Strong pool", "elite": "Elite pool", "boss": "Boss pool"},
    }
    for group in catalog["encounterGroups"]:
        for language, suffix in (("zhs", ""), ("eng", "_en")):
            act_ids = group.get("actIds", [])
            if len(act_ids) == 1:
                title = acts[act_ids[0]]["name" + suffix]
            elif group.get("actIndex") is not None:
                title = f"{'阶段' if language == 'zhs' else 'Act'} {group['actIndex'] + 1}"
            else:
                title = "全部阶段" if language == "zhs" else "All acts"
            group["name" + suffix] = title + " · " + labels[language][group["kind"]]
    science = catalog["cards"]["MAD_SCIENCE"]
    config = {"typeProperty": "TinkerTimeType", "riderProperty": "TinkerTimeRider", "types": [], "riders": []}
    values = {**native["cards"]["MAD_SCIENCE"]["levels"]["0"]["vars"], "energyPrefix": 1}
    for type_value, type_name in MAD_SCIENCE_TYPES.items():
        variant_id = "MAD_SCIENCE_" + type_name.upper()
        variant = {"value": type_value, "enum": type_name, "type": type_name.upper(),
                   "imageSource": f"images/packed/card_portraits/event/{variant_id.lower()}.png.import",
                   "image": f"combat_lab/assets/cards/{variant_id}.png", "effects": {}}
        for rider_value in [0, *range((type_value - 1) * 3 + 1, type_value * 3 + 1)]:
            rider_name = MAD_SCIENCE_RIDERS[rider_value]
            arguments = {**values, "CardType": type_name, "HasRider": rider_value != 0,
                         **{name: name == rider_name for name in MAD_SCIENCE_RIDERS.values()}}
            variant["effects"][str(rider_value)] = {
                "description" + suffix: render_description(tables[language]["cards"]["MAD_SCIENCE.description"], arguments, language).strip()
                for language, suffix in (("zhs", ""), ("eng", "_en"))}
        config["types"].append(variant)
    for value, enum_name in MAD_SCIENCE_RIDERS.items():
        if not value:
            continue
        key = f"TINKER_TIME.pages.CHOOSE_RIDER.options.{enum_name.upper()}.title"
        config["riders"].append({"value": value, "enum": enum_name, "typeValue": (value - 1) // 3 + 1,
                                **{"name" + suffix: tables[language]["events"][key]
                                   for language, suffix in (("zhs", ""), ("eng", "_en"))}})
    config["upgradeKeyword"] = {"name" + suffix: tables[language]["card_keywords"]["INNATE.title"]
                                for language, suffix in (("zhs", ""), ("eng", "_en"))}
    science["configuration"] = config
    science.update(image=config["types"][0]["image"], imageSource=config["types"][0]["imageSource"])


def export_portraits(catalog: dict, pck: Path, work: Path) -> None:
    exports = {identity: item["imageSource"] for identity, item in catalog["cards"].items() if item.get("imageSource")}
    exports.update({"MAD_SCIENCE_" + item["type"]: item["imageSource"]
                    for item in catalog["cards"]["MAD_SCIENCE"]["configuration"]["types"]})
    remaps, source = work / "remaps", work / "portraits"
    run_gdre(pck, remaps, ["res://" + value for value in exports.values()], recover=False)
    compressed = []
    for resource in exports.values():
        contents = (remaps / resource).read_text(encoding="utf-8")
        # Imports point at one target as path="…"; VRAM-compressed textures such as
        # the beta portraits use a format-suffixed key like path.bptc="…" instead.
        match = (re.search(r'^path="([^"]+)"', contents, re.MULTILINE)
                 or re.search(r'^path\.[\w.]+="([^"]+)"', contents, re.MULTILINE))
        if not match:
            raise ValueError(f"No texture remap in {resource}; is the installed game version supported?")
        compressed.append(match[1])
    run_gdre(pck, source, compressed)
    target = ASSETS / "cards"
    target.mkdir(parents=True, exist_ok=True)
    for identity, resource in exports.items():
        with Image.open(source / resource.removesuffix(".import")) as original:
            image = original.convert("RGBA")
        image.thumbnail((256, 256), Image.Resampling.LANCZOS)
        image.save(target / (identity + ".png"), optimize=True)
    print(f"Exported {len(exports)} native card portraits")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pck", type=Path, default=DEFAULT_PCK)
    parser.add_argument("--harness", type=Path, default=ROOT / "CombatSolver/.local/tool-build/OfflineSearchHarness/bin/Release/net9.0/OfflineSearchHarness.dll")
    parser.add_argument("--work", type=Path, default=ROOT / "combat_lab/.art_work/catalog")
    parser.add_argument("--localization-root", type=Path, help="Reuse a previously extracted localization directory containing zhs/ and eng/")
    parser.add_argument("--native-values", type=Path, help="Reuse card values previously exported from the same supported game version")
    parser.add_argument("--metadata-only", action="store_true", help="Generate catalog and card values without extracting artwork")
    parser.add_argument("--browser", help="Optional Chrome/Edge executable for artwork rendering")
    args = parser.parse_args()
    localization = args.localization_root or args.work / "localization"
    if args.localization_root is None:
        names = ("acts", "card_keywords", "cards", "characters", "enchantments", "encounters", "events", "monsters", "potions", "relics")
        run_gdre(args.pck, args.work, [f"res://localization/{language}/{name}.json" for language in ("zhs", "eng") for name in names], recover=False)
    tables = {language: {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in (localization / language).glob("*.json")}
              for language in ("zhs", "eng")}
    values_path = ROOT / "combat_lab/card_values.json"
    values_path.parent.mkdir(parents=True, exist_ok=True)
    if args.native_values:
        if args.native_values.resolve() != values_path.resolve():
            shutil.copyfile(args.native_values, values_path)
    else:
        subprocess.run(["dotnet", "run", "--project", str(HERE / "card_values/CardValues.csproj"), "-c", "Release", "--",
                        str(args.harness.resolve()), str(values_path), str(localization.resolve())], check=True)
    native = json.loads(values_path.read_text(encoding="utf-8"))
    schema = json.loads((HERE / "catalog_schema.json").read_text(encoding="utf-8"))
    catalog = resolve(schema, tables)
    complete_catalog(catalog, tables, native)
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Generated local catalog: {len(catalog['cards'])} cards, {len(catalog['relics'])} relics, {len(catalog['encounters'])} encounters")
    if not args.metadata_only:
        export_portraits(catalog, args.pck, args.work)
        CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        command = [sys.executable, str(HERE / "build.py"), "--pck", str(args.pck), "--work", str(args.work / "artwork")]
        if args.browser:
            command += ["--browser", args.browser]
        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
