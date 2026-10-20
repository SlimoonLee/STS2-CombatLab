"""Recover original game textures and render Spine poses for Combat Lab.

Requires Pillow, GDRE Tools, and `npm install` in this directory. The browser
renderer is only an export dependency; the local web app serves ordinary PNGs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CATALOG = ROOT / "combat_lab/game_catalog_zhs.json"
ASSETS = ROOT / "combat_lab/assets"
DEFAULT_PCK = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / (
    "Steam/steamapps/common/Slay the Spire 2/SlayTheSpire2.pck"
)

# Pick a deterministic representative for randomized cosmetic skins. Specific
# monster variants use the same skin names as their game's SetupSkins methods.
SKINS = {
    "BATTLE_FRIEND_V1": ["v1"],
    "BATTLE_FRIEND_V2": ["v2"],
    "BATTLE_FRIEND_V3": ["v3"],
    "CALCIFIED_CULTIST": ["coral"],
    "DAMP_CULTIST": ["slug"],
    "CUBEX_CONSTRUCT": ["circleeye", "moss2"],
    "KIN_FOLLOWER": ["hair_2"],
    "SCROLL_OF_BITING": ["skin1"],
    "TOADPOLE": ["eye1", "pattern1"],
    "TWO_TAILED_RAT": ["barnacles1", "head1"],
    "TOUGH_EGG": ["egg1"],
    "BOWLBUG_EGG": ["cocoon"],
    "BOWLBUG_NECTAR": ["goop"],
    "BOWLBUG_ROCK": ["rock"],
    "BOWLBUG_SILK": ["web"],
}
TRACKS = {
    "AEONGLASS": ["idle_loop", "_track1/rings_normal"],
    "ARCHITECT": ["idle_loop", "_tracks/head_normal"],
    "QUEEN": ["idle_loop", "tracks/writhe"],
    "SOUL_NEXUS": ["idle_loop", "tracks/writhe"],
    "LAGAVULIN_MATRIARCH": ["sleep_loop", "_tracks/eyes_closed_loop"],
    "VANTOM": ["idle_loop", "_tracks/charged_1"],
    "KAISER_CRAB": ["body/idle_loop", "left/idle_loop", "right/idle_loop"],
    "TOUGH_EGG": ["egg_idle_loop"],
}


def sections(file: Path) -> list[tuple[dict[str, str], dict[str, str]]]:
    """Read named resource/node sections and their single-line properties."""
    result: list[tuple[dict[str, str], dict[str, str]]] = []
    current: dict[str, str] | None = None
    for line in file.read_text(encoding="utf-8").splitlines():
        if line.startswith("[") and line.endswith("]"):
            attrs = dict(re.findall(r'(\w+)="([^"\n]*)"', line))
            attrs["section"] = line[1:].split(" ", 1)[0]
            current = {}
            result.append((attrs, current))
        elif current is not None and " = " in line:
            key, value = line.split(" = ", 1)
            current[key] = value
    return result


def resource_paths(file: Path) -> dict[str, str]:
    return {
        attrs.get("type", ""): attrs["path"].removeprefix("res://")
        for attrs, _ in sections(file)
        if attrs.get("section") == "ext_resource" and "path" in attrs
    }


def scene_profile(scene: Path, scenes: Path, monster_id: str) -> dict | None:
    entries = sections(scene)
    resources = {attrs["id"]: attrs for attrs, _ in entries if attrs.get("section") == "ext_resource"}
    visuals = next((props for attrs, props in entries if attrs.get("type") == "SpineSprite" and attrs.get("name") == "Visuals"), None)
    if visuals is None:
        return None
    ref = re.search(r'ExtResource\("([^"]+)"\)', visuals.get("skeleton_data_res", ""))
    if ref is None or ref[1] not in resources:
        return None
    resource = scenes / resources[ref[1]]["path"].removeprefix("res://")
    if not resource.is_file():
        return None
    paths = resource_paths(resource)
    if "SpineAtlasResource" not in paths or "SpineSkeletonFileResource" not in paths:
        return None
    result = {
        "id": monster_id,
        "atlas": paths["SpineAtlasResource"],
        "skeleton": paths["SpineSkeletonFileResource"],
        "previewSkin": json.loads(visuals.get("preview_skin", '"default"')),
    }
    if monster_id in SKINS:
        result["skins"] = SKINS[monster_id]
    if monster_id in TRACKS:
        result["tracks"] = TRACKS[monster_id]
    if monster_id == "BOWLBUG_EGG":
        result["children"] = [{
            "id": "CARRIED_EGG",
            "atlas": "animations/monsters/tough_egg/tough_egg.atlas",
            "skeleton": "animations/monsters/tough_egg/tough_egg.skel",
            "skins": ["egg1"],
            "animation": "egg_idle_loop",
            "attachSlot": "items",
            "position": [-12.7693, 48.3319],
            "rotation": -0.0797485,
            "scale": [0.943352, 0.90027],
        }]
    return result


def run_gdre(pck: Path, output: Path, includes: list[str], recover: bool = True) -> None:
    output.mkdir(parents=True, exist_ok=True)
    command = [str(ROOT / "tools/gdre_tools.exe"), "--headless", f"--{'recover' if recover else 'extract'}={pck}", f"--output={output}"]
    command += [f"--include={value}" for value in sorted(set(includes))]
    process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if process.returncode:
        raise RuntimeError(process.stdout[-3000:] + process.stderr[-3000:])
    print(f"Recovered assets into {output.name}")


def source_stems(catalog: dict, kind: str) -> list[str]:
    return sorted({Path(item["imageSource"].removesuffix(".import")).stem for item in catalog[kind].values() if item.get("imageSource")})


def icon_tiles(catalog: dict, kind: str, source: Path) -> int:
    written = 0
    for item_id, item in catalog[kind].items():
        if not item.get("imageSource"):
            continue
        file = source / item["imageSource"].removesuffix(".import")
        image = Image.open(file).convert("RGBA")
        image.thumbnail((128, 128), Image.Resampling.LANCZOS)
        tile = Image.new("RGBA", (128, 128))
        tile.alpha_composite(image, ((128 - image.width) // 2, (128 - image.height) // 2))
        target = ASSETS / kind / f"{item_id}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        tile.save(target, optimize=True)
        written += 1
    return written


def crop_sprite(file: Path) -> Image.Image:
    image = Image.open(file).convert("RGBA")
    # Discard fully transparent canvas padding, without changing color values.
    bounds = image.getchannel("A").point(lambda alpha: 255 if alpha > 3 else 0).getbbox()
    if bounds is None:
        raise RuntimeError(f"Empty rendered image: {file}")
    return image.crop(bounds)


def sprite_tiles(profiles: list[dict], rendered: Path) -> None:
    for profile in profiles:
        image = crop_sprite(rendered / f"{profile['id']}.png")
        image.thumbnail((240, 240), Image.Resampling.LANCZOS)
        tile = Image.new("RGBA", (256, 256))
        tile.alpha_composite(image, ((256 - image.width) // 2, (256 - image.height) // 2))
        target = ASSETS / "monsters" / f"{profile['id']}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        tile.save(target, optimize=True)


def encounter_tiles(catalog: dict, rendered_ids: set[str]) -> int:
    target = ASSETS / "encounters"
    target.mkdir(parents=True, exist_ok=True)
    for encounter_id, encounter in catalog["encounters"].items():
        members = list(dict.fromkeys(encounter.get("monsterIds", [])))
        if encounter_id == "KAISER_CRAB_BOSS":
            members = ["KAISER_CRAB"]
        missing = [member for member in members if member not in rendered_ids]
        if missing:
            raise RuntimeError(f"Missing sprites in {encounter_id}: {missing}")
        if not members:
            raise RuntimeError(f"Empty encounter: {encounter_id}")
        images = [crop_sprite(ASSETS / "monsters" / f"{member}.png") for member in members]
        # Each possible monster type remains whole and readable in the thumbnail.
        rows = 2 if len(images) > 3 else 1
        columns = (len(images) + rows - 1) // rows
        cell_width, cell_height = 336 // columns, 240 // rows
        tile = Image.new("RGBA", (336, 240))
        for index, image in enumerate(images):
            image.thumbnail((cell_width - 10, cell_height - 12), Image.Resampling.LANCZOS)
            x = (index % columns) * cell_width + (cell_width - image.width) // 2
            y = (index // columns) * cell_height + (cell_height - image.height) // 2
            tile.alpha_composite(image, (x, y))
        tile.save(target / f"{encounter_id}.png", optimize=True)
        encounter["image"] = f"combat_lab/assets/encounters/{encounter_id}.png"
        encounter["imageRendering"] = "spine-idle"
    return len(catalog["encounters"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pck", type=Path, default=DEFAULT_PCK)
    parser.add_argument("--work", type=Path, default=ROOT / "combat_lab/.art_work")
    parser.add_argument("--reuse", action="store_true", help="Reuse already recovered source files")
    parser.add_argument("--browser", help="Optional Chrome/Edge executable path")
    args = parser.parse_args()
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    scenes = args.work / "scenes"
    source = args.work / "source"
    if not args.reuse:
        run_gdre(args.pck, scenes, ["res://scenes/creature_visuals/*.tscn", "res://animations/monsters/**/*.tres"], recover=False)
    used_ids = {member for encounter in catalog["encounters"].values() for member in encounter.get("monsterIds", [])}
    profiles = []
    for monster_id in sorted(set(catalog["monsters"]) | used_ids):
        scene = scenes / "scenes/creature_visuals" / f"{monster_id.lower()}.tscn"
        if scene.exists():
            profile = scene_profile(scene, scenes, monster_id)
            if profile:
                profiles.append(profile)
    boss_scene = scenes / "scenes/creature_visuals/kaiser_crab_boss.tscn"
    boss = scene_profile(boss_scene, scenes, "KAISER_CRAB")
    if boss:
        profiles.append(boss)
    if not args.reuse:
        stems = set(source_stems(catalog, "monsters"))
        stems.update(Path(profile[key]).stem for profile in profiles for key in ("skeleton", "atlas"))
        run_gdre(args.pck, source, [f"res://.godot/imported/{stem}*" for stem in stems])
    for kind in ["relics", "potions"]:
        icon_source = args.work / kind
        if not args.reuse:
            run_gdre(args.pck, icon_source, [f"res://.godot/imported/{stem}*" for stem in source_stems(catalog, kind)])
        print(f"Wrote {icon_tiles(catalog, kind, icon_source)} {kind} icons")
    manifest = args.work / "manifest.json"
    manifest.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
    rendered = args.work / "rendered"
    command = ["node", str(HERE / "render.mjs"), str(source), str(manifest), str(rendered)]
    if args.browser:
        command.append(args.browser)
    subprocess.run(command, check=True)
    sprite_tiles(profiles, rendered)
    poses = json.loads((rendered / "poses.json").read_text(encoding="utf-8"))
    for profile in profiles:
        monster_id = profile["id"]
        if monster_id not in catalog["monsters"]:
            for encounter in catalog["encounters"].values():
                if monster_id in encounter.get("monsterIds", []):
                    index = encounter["monsterIds"].index(monster_id)
                    catalog["monsters"][monster_id] = {
                        "id": monster_id,
                        "name": encounter["monsters"][index],
                        "name_en": encounter["monsters_en"][index],
                    }
                    break
        if monster_id in catalog["monsters"]:
            item = catalog["monsters"][monster_id]
            item["image"] = f"combat_lab/assets/monsters/{monster_id}.png"
            item["imageRendering"] = "spine-idle"
            item["skeletonSource"] = profile["skeleton"]
            item["imageSkins"] = poses[monster_id]["skins"]
    count = encounter_tiles(catalog, set(poses))
    catalog["assetVersion"] = 2
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(profiles)} monster sprites and {count} encounter thumbnails")


if __name__ == "__main__":
    main()
