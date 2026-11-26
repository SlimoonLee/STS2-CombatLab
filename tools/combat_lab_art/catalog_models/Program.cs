using System.Collections;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.Loader;
using System.Text;
using System.Text.Json;

// Export the model metadata behind catalog_schema.json: card pools and rarities,
// relic/potion rarities and icon paths, act encounter pools and their monsters.
// No run, combat, search or game window is created. The game assemblies are read
// through reflection, the same way CardValues reads native card values.
if (args.Length != 2)
    throw new ArgumentException("Usage: CatalogModels <OfflineSearchHarness.dll> <output.json>");
Console.OutputEncoding = new UTF8Encoding(false);
string harnessPath = Path.GetFullPath(args[0]);
using (JsonDocument config = JsonDocument.Parse(File.ReadAllText(Path.ChangeExtension(harnessPath, ".runtimeconfig.json"))))
    foreach (JsonProperty item in config.RootElement.GetProperty("runtimeOptions").GetProperty("configProperties").EnumerateObject())
        if (item.Value.ValueKind == JsonValueKind.String)
            AppContext.SetData(item.Name, item.Value.GetString());
Assembly harness = AssemblyLoadContext.Default.LoadFromAssemblyPath(harnessPath);
RuntimeHelpers.RunModuleConstructor(harness.ManifestModule.ModuleHandle);
Type bootstrap = harness.GetType("OfflineSearchHarness.GameBootstrap", throwOnError: true)!;
InvokeStatic(bootstrap, "ApplyGodotBypasses");
InvokeStatic(bootstrap, "SkipGodotNodeStaticConstructors");
InvokeStatic(bootstrap, "InitializeStaticState");
Assembly game = AppDomain.CurrentDomain.GetAssemblies().Single(a => a.GetName().Name == "sts2");
Type modelDb = game.GetType("MegaCrit.Sts2.Core.Models.ModelDb", throwOnError: true)!;
const BindingFlags HiddenInstance = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

SortedDictionary<string, object> cards = new(StringComparer.Ordinal);
foreach (object canonical in Static(modelDb, "AllCards"))
{
    // EnergyCost modifiers are only defined on a mutable card, as in CardValues.
    object mutable = canonical.GetType().GetMethod("ToMutable", Type.EmptyTypes)!.Invoke(canonical, null)!;
    object energy = Get(mutable, "EnergyCost");
    MethodInfo getCost = energy.GetType().GetMethod("GetWithModifiers")!;
    Type modifierType = getCost.GetParameters()[0].ParameterType;
    int cost = (bool)Get(energy, "CostsX") ? -1 : (int)getCost.Invoke(energy, [Enum.Parse(modifierType, "Local")])!;
    cards[Id(canonical)] = new
    {
        pool = (string)Get(Get(canonical, "Pool"), "Title"),
        type = Get(canonical, "Type")!.ToString().ToUpperInvariant(),
        rarity = Get(canonical, "Rarity")!.ToString().ToUpperInvariant(),
        maxUpgradeLevel = (int)Get(canonical, "MaxUpgradeLevel"),
        portraitPath = (string)Get(canonical, "PortraitPath"),
        betaPortraitPath = (string)Get(canonical, "BetaPortraitPath"),
        cost,
    };
}

SortedDictionary<string, object> relics = new(StringComparer.Ordinal);
// IconBaseName is not exported: Yummy Cookie derives it from a randomly chosen
// character, so the icon path is probed by naming convention in build_schema.
foreach (object relic in Static(modelDb, "AllRelics"))
{
    relics[Id(relic)] = new
    {
        rarity = Get(relic, "Rarity")!.ToString().ToUpperInvariant(),
        packedIconPath = (string)Get(relic, "PackedIconPath"),
    };
}

SortedDictionary<string, object> potions = new(StringComparer.Ordinal);
foreach (object potion in Static(modelDb, "AllPotions"))
{
    potions[Id(potion)] = new
    {
        rarity = Get(potion, "Rarity")!.ToString().ToUpperInvariant(),
        largeImagePath = (string)Get(potion, "LargeImagePath"),
    };
}

List<string> characters = Static(modelDb, "AllCharacters").Cast<object>().Select(Id).OrderBy(id => id, StringComparer.Ordinal).ToList();

// DebugEnchantments lists every enchantment in the game code, including mock and
// deprecated placeholders that never appear on a real card.
List<string> enchantments = Static(modelDb, "DebugEnchantments").Cast<object>().Select(Id)
    .Where(id => !id.Contains("DEPRECATED", StringComparison.Ordinal) && !id.Contains("MOCK", StringComparison.Ordinal))
    .OrderBy(id => id, StringComparer.Ordinal).ToList();

// GenerateMonsters chooses between variants with the encounter's own RNG, which a
// model straight from ModelDb lacks, so each encounter is seeded several times.
FieldInfo encounterRng = game.GetType("MegaCrit.Sts2.Core.Models.EncounterModel", throwOnError: true)!
    .GetField("_rng", HiddenInstance) ?? throw new MissingFieldException("EncounterModel", "_rng");
Type rngType = game.GetType("MegaCrit.Sts2.Core.Random.Rng", throwOnError: true)!;

SortedDictionary<string, object> encounters = new(StringComparer.Ordinal);
SortedDictionary<string, object> acts = new(StringComparer.Ordinal);
foreach (object act in Static(modelDb, "ActsByIndex").Cast<object>().SelectMany(index => ((IEnumerable)index).Cast<object>()))
{
    HashSet<string> bosses = ((IEnumerable)Get(act, "BossDiscoveryOrder")).Cast<object>().Select(Id)
        .ToHashSet(StringComparer.Ordinal);
    List<string> all = new(), boss = new(), elite = new(), weak = new(), strong = new();
    foreach (object encounter in Call(act, "GenerateAllEncounters"))
    {
        string id = Id(encounter);
        encounters[id] = Describe(encounter, encounterRng, rngType);
        all.Add(id);
        if (bosses.Contains(id))
            boss.Add(id);
        else if (Get(encounter, "RoomType").ToString() == "Elite")
            elite.Add(id);
        else if ((bool)Get(encounter, "IsWeak"))
            weak.Add(id);
        else
            strong.Add(id);
    }
    acts[Id(act)] = new
    {
        index = (int)Get(act, "Index"),
        isDefault = (bool)Get(act, "IsDefault"),
        all,
        boss,
        elite,
        weak,
        strong,
    };
}

File.WriteAllText(Path.GetFullPath(args[1]), JsonSerializer.Serialize(new
{
    schemaVersion = 1, gameVersion = game.GetName().Version?.ToString(), cards, relics, potions, characters, enchantments, acts, encounters,
}, new JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping }), new UTF8Encoding(false));
Console.WriteLine($"Exported {cards.Count} cards, {relics.Count} relics, {potions.Count} potions, {characters.Count} characters, "
    + $"{encounters.Count} encounters from {acts.Count} act variants");

static object Describe(object encounter, FieldInfo encounterRng, Type rngType)
{
    MethodInfo generate = encounter.GetType()
        .GetMethod("GenerateMonsters", BindingFlags.Instance | BindingFlags.NonPublic)
        ?? throw new MissingMethodException(encounter.GetType().FullName, "GenerateMonsters");
    List<string> monsters = [];
    for (uint seed = 1; seed <= 512; seed++)
    {
        encounterRng.SetValue(encounter, Activator.CreateInstance(rngType, (ulong)seed));
        foreach (object tuple in (IEnumerable)generate.Invoke(encounter, null)!)
        {
            string id = Id(tuple!.GetType().GetField("Item1")!.GetValue(tuple)!);
            if (!monsters.Contains(id))
                monsters.Add(id);
        }
    }
    return new
    {
        className = encounter.GetType().Name,
        roomType = Get(encounter, "RoomType").ToString(),
        isWeak = (bool)Get(encounter, "IsWeak"),
        // What the fight can open with; AllPossibleMonsters also lists summons.
        monsters,
        possibleMonsters = ((IEnumerable)Get(encounter, "AllPossibleMonsters")).Cast<object>().Select(Id).Distinct().ToList(),
    };
}
static IEnumerable Static(Type type, string name) => (IEnumerable)type.GetProperty(name)!.GetValue(null)!;
static object? InvokeStatic(Type type, string name) => type.GetMethod(name, BindingFlags.Public | BindingFlags.Static)!.Invoke(null, null);
static object Get(object instance, string name)
    => (instance.GetType().GetProperty(name) ?? throw new MissingMemberException(instance.GetType().FullName, name)).GetValue(instance)!;
static IEnumerable Call(object instance, string name)
    => (IEnumerable)(instance.GetType().GetMethod(name, BindingFlags.Public | BindingFlags.Instance)
        ?? throw new MissingMethodException(instance.GetType().FullName, name)).Invoke(instance, null)!;
static string Id(object model) => (string)Get(Get(model, "Id"), "Entry");
