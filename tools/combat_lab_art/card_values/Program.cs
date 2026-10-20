using System.Collections;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.Loader;
using System.Text;
using System.Text.Json;

// Export native model metadata only. No run, combat, search or game window is created.
if (args.Length != 3)
    throw new ArgumentException("Usage: CardValues <OfflineSearchHarness.dll> <output.json> <localization-directory>");
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
Type models = game.GetType("MegaCrit.Sts2.Core.Models.ModelDb", throwOnError: true)!;
IEnumerable canonicalCards = (IEnumerable)models.GetProperty("AllCards")!.GetValue(null)!;
SortedDictionary<string, object> cards = new(StringComparer.Ordinal);
foreach (object canonical in canonicalCards)
{
    string id = (string)Get(Get(canonical, "Id"), "Entry");
    object card = canonical.GetType().GetMethod("ToMutable", Type.EmptyTypes)!.Invoke(canonical, null)!;
    int max = (int)Get(card, "MaxUpgradeLevel");
    Dictionary<string, object> levels = [];
    for (int level = 0; level <= Math.Min(max, 10); level++)
    {
        if (level > 0)
        {
            card.GetType().GetMethod("UpgradeInternal")!.Invoke(card, null);
            card.GetType().GetMethod("FinalizeUpgradeInternal")!.Invoke(card, null);
        }
        SortedDictionary<string, object> values = new(StringComparer.Ordinal);
        List<string> calculated = [];
        foreach (object entry in (IEnumerable)Get(card, "DynamicVars"))
        {
            string key = (string)Get(entry, "Key");
            object variable = Get(entry, "Value");
            values[key] = variable.GetType().GetProperty("StringValue")?.GetValue(variable)
                ?? Get(variable, "BaseValue");
            for (Type? type = variable.GetType(); type != null; type = type.BaseType)
                if (type.Name == "CalculatedVar") { calculated.Add(key); break; }
        }
        object energy = Get(card, "EnergyCost");
        MethodInfo getCost = energy.GetType().GetMethod("GetWithModifiers")!;
        Type modifierType = getCost.GetParameters()[0].ParameterType;
        int cost = (bool)Get(energy, "CostsX") ? -2 : (int)getCost.Invoke(energy, [Enum.Parse(modifierType, "Local")])!;
        Dictionary<string, object?> saved = [];
        foreach (PropertyInfo property in card.GetType().GetProperties(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance))
            if (property.GetCustomAttributesData().Any(a => a.AttributeType.Name == "SavedPropertyAttribute"))
            {
                object? value = property.GetValue(card);
                saved[property.Name] = value is Enum enumeration ? Convert.ToInt32(enumeration) : value;
            }
        string[] keywords = ((IEnumerable)Get(card, "Keywords")).Cast<object>().Select(k => k.ToString()!).ToArray();
        string targetType = Get(card, "TargetType").ToString()!;
        levels[level.ToString()] = new { vars = values, calculated, cost, targetType, keywords, savedDefaults = saved };
    }
    cards[id] = new { maxUpgradeLevel = max, levels };
}
string output = Path.GetFullPath(args[1]);
Dictionary<string, object> keywordNames = [];
Dictionary<string, string> chinese = ReadKeywordTable("zhs"), english = ReadKeywordTable("eng");
foreach (string name in new[] { "Eternal", "Ethereal", "Exhaust", "Innate", "Retain", "Sly", "Unplayable" })
{
    string id = name.ToUpperInvariant();
    keywordNames[name] = new { id, name = chinese[id + ".title"], name_en = english[id + ".title"] };
}
Directory.CreateDirectory(Path.GetDirectoryName(output)!);
File.WriteAllText(output, JsonSerializer.Serialize(new {
    schemaVersion = 1, gameVersion = game.GetName().Version?.ToString(),
    gameAssemblyMvid = game.ManifestModule.ModuleVersionId, keywords = keywordNames, cards
}, new JsonSerializerOptions { WriteIndented = true }), new UTF8Encoding(false));
Console.WriteLine($"Exported {cards.Count} native card definitions: {output}");

Dictionary<string, string> ReadKeywordTable(string language) =>
    JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(Path.Combine(args[2], language, "card_keywords.json")))!;
static object Get(object instance, string name) => instance.GetType().GetProperty(name)!.GetValue(instance)!;
static object? InvokeStatic(Type type, string name) => type.GetMethod(name, BindingFlags.Public | BindingFlags.Static)!.Invoke(null, null);
