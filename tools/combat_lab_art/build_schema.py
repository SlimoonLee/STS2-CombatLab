"""Rebuild catalog_schema.json from the installed game.

The schema lists every card, relic, potion, character, act variant, encounter
and enchantment as model ids plus localization references; ``catalog.py``
resolves it into the runtime catalog. Model data comes from the game's own
``ModelDb`` through ``catalog_models``, image paths are probed in the installed
PCK, and only the hand-written ``ui`` labels are carried over from the previous
schema. Re-run this after a game update and then run ``catalog.py`` again.

Usage::

    py build_schema.py --pck <SlayTheSpire2.pck> --harness <OfflineSearchHarness.dll>

``--previous`` selects the schema to carry labels and monster artwork details
over from (default: the checked-in file); ``--localization-root`` reuses already
extracted localization tables.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from build import GDRE_TOOLS, HERE, ROOT, run_gdre

LOCALIZATION_NAMES = ("acts", "card_keywords", "cards", "characters", "enchantments", "encounters", "events", "monsters", "potions", "relics")
# Schema field -> suffix of the game's localization key.
TEXT_KEYS = (("name", "title"), ("description", "description"))
NAME_ONLY = (("name", "title"),)
MONSTER_NAME = (("name", "name"),)  # monster tables key their names by "<ID>.name"
DEFAULT_HARNESS = ROOT / "CombatSolver/.local/tool-build/OfflineSearchHarness/bin/Release/net9.0/OfflineSearchHarness.dll"
Tables = dict[str, dict[str, Any]]


def reference(language: str, table: str, key: str) -> dict[str, list[str]]:
    return {"$loc": [language, table, key]}


def texts(tables: Tables, table: str, identity: str, previous: dict[str, Any] | None, keys: tuple[tuple[str, str], ...] = TEXT_KEYS) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Reference the model's own localization entries in both languages.

    Returns None for a model the game ships without a name, such as a test or
    deprecated placeholder. A few cards have no description entry; they keep the
    literal text of the previous schema instead of a dangling reference.
    """
    if any(identity + "." + keys[0][1] not in tables[language].get(table, {}) for language in ("zhs", "eng")):
        return None
    zh, en = {}, {}
    for language, target, suffix in (("zhs", zh, ""), ("eng", en, "_en")):
        for field, key in keys:
            if identity + "." + key in tables[language].get(table, {}):
                target[field + suffix] = reference(language, table, identity + "." + key)
            else:
                target[field + suffix] = (previous or {}).get(field + suffix, "")
    return zh, en


def existing(pck: Path, work: Path, candidates: dict[str, str]) -> set[str]:
    """Return the ids whose candidate resource really exists in the installed package."""
    run_gdre(pck, work, ["res://" + value for value in candidates.values()], recover=False, optional=True)
    return {identity for identity, value in candidates.items() if (work / value).is_file()}


def pick_sources(pck: Path, work: Path, models: dict[str, Any], candidates: tuple[Any, ...]) -> dict[str, str]:
    """Take the first resource that exists for each model: the single-image file the
    artwork tools read, its beta fallback, then the matching atlas sprites."""
    sources: dict[str, str] = {}
    remaining = dict(models)
    for index, path_of in enumerate(candidates):
        if not remaining:
            break
        paths = {identity: path_of(identity, model) for identity, model in remaining.items()}
        found = existing(pck, work / str(index), paths)
        sources |= {identity: paths[identity] for identity in found}
        remaining = {identity: model for identity, model in remaining.items() if identity not in found}
    return sources


def load_tables(root: Path | None, pck: Path, work: Path) -> Tables:
    # GDRE reproduces the in-package path, so extracting into `work` yields work/localization/<language>/.
    localization = root / "localization" if root else work / "localization"
    if not root:
        run_gdre(pck, work, [f"res://localization/{language}/{name}.json" for language in ("zhs", "eng") for name in LOCALIZATION_NAMES], recover=False)
    return {language: {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted((localization / language).glob("*.json"))}
            for language in ("zhs", "eng")}


def build(previous: dict[str, Any], models: dict[str, Any], tables: Tables, pck: Path, work: Path) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    old = {kind: previous.get(kind, {}) for kind in ("cards", "relics", "potions", "encounters", "monsters")}
    catalog: dict[str, Any] = {key: previous[key] for key in ("source", "language", "gameText", "ui", "languages", "catalogVersion", "assetVersion", "ascensionMax")}
    sections = (
        # kind, model key, localization table, image candidates in priority order
        ("cards", "cards", "cards", (
            lambda identity, model: f"images/packed/card_portraits/{model['pool'].lower()}/{identity.lower()}.png.import",
            lambda identity, model: f"images/packed/card_portraits/{model['pool'].lower()}/beta/{identity.lower()}.png.import",
            lambda identity, model: model["portraitPath"].removeprefix("res://"),
            lambda identity, model: model["betaPortraitPath"].removeprefix("res://"))),
        ("relics", "relics", "relics", (
            lambda identity, model: f"images/relics/{identity.lower()}.png.import",
            # Some icons carry a state or character suffix (Looming Fruit, Yummy Cookie picks
            # a character at random), so those are probed in a fixed order instead of asked.
            *(lambda identity, model, suffix=suffix: f"images/relics/{identity.lower()}{suffix}.png.import"
              for suffix in ("_2", *("_" + character.lower() for character in models["characters"]))),
            lambda identity, model: f"images/relics/beta/{identity.lower()}.png.import",
            lambda identity, model: model["packedIconPath"].removeprefix("res://"))),
        ("potions", "potions", "potions", (lambda identity, model: f"images/potions/large/{identity.lower()}.png.import",)),
    )
    for kind, model_key, table, candidates in sections:
        localized_models = {identity: (model, texts(tables, table, identity, old[kind].get(identity))) for identity, model in models[model_key].items()}
        unnamed = sorted(identity for identity, (_, localized) in localized_models.items() if localized is None)
        named = {identity: model for identity, (model, localized) in localized_models.items() if localized is not None}
        sources = pick_sources(pck, work / f"probe-{kind}", named, candidates)
        section: dict[str, Any] = {}
        for identity, model in named.items():
            zh, en = localized_models[identity][1]
            if kind == "cards":
                pool = model["pool"].upper()
                entry = {"id": identity, **zh, "class": pool, "pools": [pool], "type": model["type"], "rarity": model["rarity"],
                         "cost": model["cost"]}
            else:
                entry = {"id": identity, **zh, "rarity": model["rarity"]}
            # Models the game ships without art (deprecated and mock placeholders) carry no image fields.
            if identity in sources:
                entry |= {"image": f"combat_lab/assets/{kind}/{identity}.png", "imageSource": sources[identity]}
            if identity == "MAD_SCIENCE" and "type" in old[kind].get(identity, {}):
                # Tinker Time takes its type from a saved enum member; the bare model reports NONE.
                entry["type"] = old[kind][identity]["type"]
            entry |= {**en, **({"maxUpgradeLevel": model["maxUpgradeLevel"]} if kind == "cards" else {})}
            section[identity] = entry
        catalog[kind] = section
        notes.append(f"{kind}: {len(section)} entries; images for {len(sources)} of {len(models[model_key])} models "
                     f"({sum(1 for source in sources.values() if '/beta/' in source)} beta, "
                     f"{sum(1 for source in sources.values() if source.endswith('.tres'))} atlas sprites)")
        if unnamed:
            notes.append(f"{kind}: skipped {len(unnamed)} models without a localized title: {unnamed[:8]}")

    catalog["characters"] = {}
    for identity in models["characters"]:
        if texts(tables, "characters", identity, None) is None:
            continue
        zh, en = texts(tables, "characters", identity, None)
        catalog["characters"][identity] = {"id": identity, **zh, **en}

    # Acts are grouped by stage index; each stage has one default branch and optional alternates.
    acts: list[dict[str, Any]] = []
    stages: dict[int, list[str]] = {}
    for act_id, act in models["acts"].items():
        if texts(tables, "acts", act_id, None, NAME_ONLY) is not None:
            stages.setdefault(act["index"], []).append(act_id)
    for index in sorted(stages):
        members = sorted(stages[index], key=lambda act_id: not models["acts"][act_id]["isDefault"])
        variants = []
        for act_id in members:
            zh, en = texts(tables, "acts", act_id, None, NAME_ONLY)
            variants.append({"index": index, "id": act_id, **zh, **en, "default": bool(models["acts"][act_id]["isDefault"])})
        zh, en = texts(tables, "acts", members[0], None, NAME_ONLY)
        acts.append({"index": index, "id": members[0], **zh, **en, "default": True, "variants": variants})
    catalog["acts"], catalog["actCount"], catalog["actVariantCount"] = acts, len(acts), len(models["acts"])

    # An encounter belongs to the first act variant that generates it.
    encounters: dict[str, Any] = {}
    for act_id in models["acts"]:
        act = models["acts"][act_id]
        for identity in act["all"]:
            if identity in encounters:
                continue
            model = models["encounters"][identity]
            if texts(tables, "encounters", identity, old["encounters"].get(identity)) is None:
                for pool in ("all", "boss", "elite", "weak", "strong"):
                    act[pool] = [member for member in act[pool] if member != identity]
                continue
            kind = "boss" if identity in act["boss"] else "elite" if identity in act["elite"] else "normal"
            zh, en = texts(tables, "encounters", identity, old["encounters"].get(identity), NAME_ONLY)
            carried = {key: old["encounters"][identity][key] for key in ("imageSource", "imageRendering") if key in old["encounters"].get(identity, {})}
            opening = model["monsters"]
            earlier = old["encounters"].get(identity, {}).get("monsterIds")
            if earlier and set(opening) <= set(earlier):
                # The earlier list may also name monsters summoned mid-fight. Keep it as long
                # as the game still opens with a subset of it; a real change replaces it.
                opening = earlier
            encounters[identity] = {"id": identity, "actIndex": act["index"], "actId": act_id, "className": model["className"], "kind": kind,
                                    "difficulty": kind if kind != "normal" else "weak" if model["isWeak"] else "normal", **zh, **en,
                                    "monsterIds": opening, "room": {"boss": "Boss", "elite": "Elite"}.get(kind, "Monster"),
                                    "image": f"combat_lab/assets/encounters/{identity}.png", **carried}
    catalog["encounters"] = encounters

    def normal(act: dict[str, Any]) -> list[str]:
        return [identity for identity in act["all"] if identity in act["weak"] or identity in act["strong"]]

    groups: list[dict[str, Any]] = []
    for index in sorted(stages):
        members = stages[index]
        groups.append({"id": f"ACT_{index}_ALL", "actIndex": index, "actIds": members, "kind": "all",
                       "encounters": list(dict.fromkeys(e for act_id in members for e in models["acts"][act_id]["all"]))})
        for act_id in members:
            act = models["acts"][act_id]
            for kind, members_of in (("all", act["all"]), ("normal", normal(act)), ("elite", act["elite"]), ("boss", act["boss"])):
                groups.append({"id": f"{act_id}_{kind.upper()}", "actIndex": index, "actIds": [act_id], "kind": kind, "encounters": members_of})
    for kind in ("normal", "elite", "boss"):
        groups.append({"id": f"ALL_{kind.upper()}", "kind": kind, "encounters": [identity for identity, e in encounters.items() if e["kind"] == kind]})
    for kind, matches in (("weak", lambda e: e["difficulty"] == "weak"), ("strong", lambda e: e["kind"] == "normal" and e["difficulty"] == "normal")):
        groups.append({"id": f"ALL_{kind.upper()}", "kind": kind, "difficulty": "weak" if kind == "weak" else "normal",
                       "encounters": [identity for identity, e in encounters.items() if matches(e)]})
    for index in sorted(stages):
        members = stages[index]
        for act_id in [None, *members]:
            act = models["acts"][act_id] if act_id else None
            for kind in ("weak", "strong"):
                pool = [e for other in members for e in models["acts"][other][kind]] if act is None else act[kind]
                groups.append({"id": f"{'ACT_' + str(index) if act is None else act_id}_{kind.upper()}", "kind": kind,
                               "difficulty": "weak" if kind == "weak" else "normal", "actIndex": index, "actIds": members if act is None else [act_id],
                               "encounters": pool})
    catalog["encounterGroups"] = groups

    monsters: dict[str, Any] = {}
    # The monster table keeps everything the previous schema listed and adds whatever an encounter can summon.
    for identity in [*old["monsters"], *(monster for encounter in models["encounters"].values() for monster in encounter["possibleMonsters"])]:
        if identity in monsters:
            continue
        localized = texts(tables, "monsters", identity, old["monsters"].get(identity), MONSTER_NAME)
        if localized is None and identity in old["monsters"]:
            # Segments and similar parts have no name of their own; keep the earlier entry as it was.
            monsters[identity] = old["monsters"][identity]
            continue
        if localized is None:
            continue
        zh, en = localized
        carried = {key: old["monsters"][identity][key] for key in ("image", "imageSource", "imageRendering", "skeletonSource", "imageSkins")
                   if key in old["monsters"].get(identity, {})}
        monsters[identity] = {"id": identity, **zh, **en, **carried}
    catalog["monsters"] = monsters
    catalog["enchantments"] = {}
    for identity in models["enchantments"]:
        if texts(tables, "enchantments", identity, None) is None:
            continue
        zh, en = texts(tables, "enchantments", identity, None)
        catalog["enchantments"][identity] = {"id": identity, **zh, **en}
    catalog["enchantmentIds"] = list(catalog["enchantments"])
    notes.append(f"encounters: {len(encounters)} in {len(groups)} groups; monsters: {len(monsters)}; "
                 f"enchantments: {len(catalog['enchantments'])}; characters: {len(catalog['characters'])}")
    return catalog, notes


def report(previous: dict[str, Any], catalog: dict[str, Any], tables: Tables) -> list[str]:
    """List unresolved localization references and what changed for content present in both schemas."""
    lines: list[str] = []
    for kind in ("cards", "relics", "potions", "characters", "encounters", "monsters", "enchantments"):
        for identity, item in catalog[kind].items():
            for field, value in item.items():
                if isinstance(value, dict) and "$loc" in value and value["$loc"][2] not in tables[value["$loc"][0]].get(value["$loc"][1], {}):
                    lines.append(f"UNRESOLVED {kind}.{identity}.{field} -> {value['$loc']}")
        before, after = previous.get(kind, {}), catalog[kind]
        added, removed = sorted(set(after) - set(before)), sorted(set(before) - set(after))
        changed = [(identity, field, before[identity][field], after[identity][field]) for identity in sorted(set(before) & set(after))
                   for field in after[identity] if field in before[identity] and before[identity][field] != after[identity][field] and "image" not in field]
        lines.append(f"{kind}: {len(before)} -> {len(after)} (+{len(added)}, -{len(removed)}), changed {len(changed)}"
                     + (f"; removed {removed[:6]}" if removed else ""))
        lines += [f"    {identity}.{field}: {old!r} -> {new!r}" for identity, field, old, new in changed[:10]]
    for group in catalog["encounterGroups"]:
        if any(identity not in catalog["encounters"] for identity in group["encounters"]):
            lines.append(f"BROKEN group {group['id']}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pck", type=Path, required=True)
    parser.add_argument("--harness", type=Path, default=DEFAULT_HARNESS)
    parser.add_argument("--previous", type=Path, default=HERE / "catalog_schema.json")
    parser.add_argument("--localization-root", type=Path)
    parser.add_argument("--work", type=Path, default=ROOT / "combat_lab/.art_work/schema")
    parser.add_argument("--out", type=Path, default=HERE / "catalog_schema.json")
    args = parser.parse_args()
    if not GDRE_TOOLS.is_file():
        raise SystemExit(f"找不到 GDRE Tools：{GDRE_TOOLS}；设置 GDRE_TOOLS 或把 gdre_tools.exe 放到 tools/ 下。")
    previous = json.loads(args.previous.read_text(encoding="utf-8"))
    args.work.mkdir(parents=True, exist_ok=True)
    models_path = (args.work / "models.json").resolve()
    subprocess.run(["dotnet", "run", "--project", str(HERE / "catalog_models/CatalogModels.csproj"), "-c", "Release", "--",
                    str(args.harness.resolve()), str(models_path)], check=True)
    models = json.loads(models_path.read_text(encoding="utf-8"))
    tables = load_tables(args.localization_root, args.pck, args.work)
    # Without localization every model looks unnamed. Stop here rather than replace
    # the checked-in schema with an empty one.
    unread = [f"{language}/{name}.json" for language in ("zhs", "eng") for name in LOCALIZATION_NAMES if not tables[language].get(name)]
    if unread:
        raise SystemExit("Localization tables could not be read: " + ", ".join(unread))
    catalog, notes = build(previous, models, tables, args.pck, args.work)
    if not catalog["cards"] or not catalog["encounters"]:
        raise SystemExit("No localized models were found; the existing schema was left untouched.")
    lines = report(previous, catalog, tables)
    args.out.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    summary = "\n".join([*notes, *lines]) + "\n"
    (args.work / "schema-report.txt").write_text(summary, encoding="utf-8")
    print(f"Wrote {args.out}")
    print(summary, end="")


if __name__ == "__main__":
    main()
