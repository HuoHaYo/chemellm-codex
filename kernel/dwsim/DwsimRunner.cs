// External-process DWSIM Automation3 adapter. No DWSIM assemblies are redistributed.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;

class DwsimRunner
{
    static string Home;
    static JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 32 * 1024 * 1024 };
    static string[] Creatable = { "MaterialStream", "EnergyStream", "Mixer", "Splitter", "Heater", "Cooler",
        "Pump", "Compressor", "Expander", "Valve", "Vessel", "Tank", "HeatExchanger", "ShortcutColumn",
        "DistillationColumn", "AbsorptionColumn", "ReboiledAbsorber", "RefluxedAbsorber", "ComponentSeparator", "OT_Recycle" };

    static Assembly Resolve(object sender, ResolveEventArgs args)
    {
        foreach (string dir in new string[] { Home, Path.Combine(Home, "ThermoCS"), Path.Combine(Home, "Lib"), Path.Combine(Home, "extenders") })
        {
            string path = Path.Combine(dir, new AssemblyName(args.Name).Name + ".dll");
            if (File.Exists(path)) return Assembly.LoadFrom(path);
        }
        return null;
    }

    static Dictionary<string, object> Dict(params object[] pairs)
    {
        var result = new Dictionary<string, object>();
        for (int i = 0; i < pairs.Length; i += 2) result[(string)pairs[i]] = pairs[i + 1];
        return result;
    }
    static Dictionary<string, object> Map(object value) { return (Dictionary<string, object>)value; }
    static object Value(Dictionary<string, object> value, string key, object fallback)
    { object result; return value.TryGetValue(key, out result) ? result : fallback; }
    static string Text(object value) { return Convert.ToString(value, CultureInfo.InvariantCulture) ?? ""; }
    static PropertyInfo Property(object obj, string name)
    {
        for (Type type = obj.GetType(); type != null; type = type.BaseType)
        {
            var p = type.GetProperty(name, BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly);
            if (p != null) return p;
        }
        return null;
    }
    static object Get(object obj, string name)
    {
        if (obj == null) return null;
        var p = Property(obj, name);
        return p == null ? null : p.GetValue(obj, null);
    }
    static object ConvertArg(object value, Type type)
    {
        if (value == null) return null;
        type = Nullable.GetUnderlyingType(type) ?? type;
        if (type.IsInstanceOfType(value)) return value;
        if (type.IsEnum) return Enum.Parse(type, Text(value), false);
        if (type == typeof(int) && Convert.ToDouble(value) != Math.Truncate(Convert.ToDouble(value)))
            throw new ArgumentException("Integer parameter cannot be fractional");
        return Convert.ChangeType(value, type, CultureInfo.InvariantCulture);
    }
    static object Call(object obj, string name, params object[] args)
    {
        MethodInfo selected = null; object[] selectedArgs = null; int best = Int32.MaxValue;
        foreach (var method in obj.GetType().GetMethods())
        {
            if (method.Name != name || method.GetParameters().Length != args.Length) continue;
            var converted = new object[args.Length];
            bool compatible = true; int score = 0;
            for (int i = 0; i < args.Length; i++)
                try {
                    Type type = method.GetParameters()[i].ParameterType;
                    converted[i] = ConvertArg(args[i], type);
                    if (args[i] != null && !type.IsInstanceOfType(args[i])) score++;
                }
                catch { compatible = false; break; }
            if (compatible && score < best) { selected = method; selectedArgs = converted; best = score; }
        }
        if (selected != null) return selected.Invoke(obj, selectedArgs);
        throw new MissingMethodException(obj.GetType().FullName, name);
    }
    static object Optional(object obj, string name)
    { try { return Call(obj, name); } catch { return null; } }
    static string Tag(object obj) { return Text(Get(Get(obj, "GraphicObject"), "Tag")); }
    static IDictionary Objects(object sim) { return (IDictionary)Get(sim, "SimulationObjects"); }
    static object Find(object sim, string tag)
    {
        object found = null;
        foreach (DictionaryEntry entry in Objects(sim))
            if (Tag(entry.Value) == tag || Text(Get(entry.Value, "Name")) == tag)
            {
                if (found != null && found != entry.Value) throw new ArgumentException("Ambiguous object tag: " + tag);
                found = entry.Value;
            }
        if (found == null) throw new ArgumentException("Object not found: " + tag);
        return found;
    }

    static List<object> Parameters(object obj)
    {
        var result = new List<object>();
        foreach (var p in obj.GetType().GetProperties())
        {
            Type t = Nullable.GetUnderlyingType(p.PropertyType) ?? p.PropertyType;
            string space = p.DeclaringType.Namespace ?? "";
            if (!p.CanRead || !p.CanWrite || p.GetIndexParameters().Length != 0 ||
                !(space.StartsWith("DWSIM.UnitOperations.UnitOperations") || space.StartsWith("DWSIM.UnitOperations.Streams")) ||
                !(t.IsEnum || t == typeof(double) || t == typeof(int) || t == typeof(bool))) continue;
            try { result.Add(Dict("name", p.Name, "value", t.IsEnum ? Text(p.GetValue(obj, null)) : p.GetValue(obj, null),
                "type", t.Name, "choices", t.IsEnum ? Enum.GetNames(t) : new string[0])); }
            catch { }
        }
        return result;
    }
    static HashSet<string> PropertyIds(object obj, string kind)
    {
        var names = new HashSet<string>();
        foreach (var id in (IEnumerable)Call(obj, "GetProperties", kind)) names.Add(Text(id));
        return names;
    }
    static List<object> Properties(object sim, object obj)
    {
        var result = new List<object>();
        var writable = PropertyIds(obj, "RW");
        writable.UnionWith(PropertyIds(obj, "WR"));
        foreach (string id in PropertyIds(obj, "ALL"))
        {
            try {
                string description = Text(Call(obj, "GetPropertyDescription", id));
                if (description == id) description = Text(Call(sim, "GetTranslatedString", id));
                result.Add(Dict("id", id, "value", Call(obj, "GetPropertyValue", id, null),
                "unit", Call(obj, "GetPropertyUnit", id, null),
                "description", description, "writable", writable.Contains(id))); }
            catch (Exception ex) { result.Add(Dict("id", id, "error", ex.GetBaseException().Message)); }
        }
        return result;
    }
    static List<object> Ports(object obj, string name)
    {
        var result = new List<object>();
        var ports = Get(Get(obj, "GraphicObject"), name) as IEnumerable;
        if (ports != null) foreach (var port in ports)
            result.Add(Dict("index", result.Count, "name", Text(Get(port, "ConnectorName")),
                "attached", Get(port, "IsAttached"), "type", Text(Get(port, "Type"))));
        return result;
    }
    static void Apply(object sim, object obj, Dictionary<string, object> spec)
    {
        foreach (string key in new string[] { "temperature_k", "pressure_pa", "mass_flow_kg_s" })
            if (Value(spec, key, null) != null)
                Call(obj, key == "temperature_k" ? "SetTemperature" : key == "pressure_pa" ? "SetPressure" : "SetMassFlow",
                    Convert.ToDouble(spec[key], CultureInfo.InvariantCulture));
        if (Value(spec, "mole_fractions", null) != null)
        {
            var fractions = Map(spec["mole_fractions"]);
            var names = (string[])Call(obj, "GetCompoundNames");
            if (fractions.Count != names.Length) throw new ArgumentException("List all selected compounds for " + Tag(obj));
            var composition = new double[names.Length];
            for (int i = 0; i < names.Length; i++) composition[i] = Convert.ToDouble(fractions[names[i]], CultureInfo.InvariantCulture);
            Call(obj, "SetOverallComposition", (object)composition);
        }
        var allowed = new HashSet<string>();
        foreach (object p in Parameters(obj)) allowed.Add(Text(Map(p)["name"]));
        foreach (var pair in Map(Value(spec, "parameters", Dict())))
        {
            if (!allowed.Contains(pair.Key)) throw new ArgumentException("Inspect before setting model parameter: " + pair.Key);
            var p = Property(obj, pair.Key);
            p.SetValue(obj, ConvertArg(pair.Value, p.PropertyType), null);
        }
        var writable = PropertyIds(obj, "RW");
        writable.UnionWith(PropertyIds(obj, "WR"));
        foreach (var pair in Map(Value(spec, "properties", Dict())))
        {
            if (!writable.Contains(pair.Key)) throw new ArgumentException("Unknown/read-only DWSIM property: " + pair.Key);
            if (!Convert.ToBoolean(Call(obj, "SetPropertyValue", pair.Key, pair.Value, null)))
                throw new ArgumentException("DWSIM rejected property " + pair.Key);
        }
    }
    static object AddObject(object sim, string type, int x, int y, string tag)
    {
        if (Array.IndexOf(Creatable, type) < 0) throw new ArgumentException("Unsupported creation ObjectType: " + type);
        return Call(sim, "AddObject", type, x, y, tag);
    }
    static void Build(object sim, Dictionary<string, object> model)
    {
        foreach (object compound in (IEnumerable)model["compounds"]) Call(sim, "AddCompound", Text(compound));
        object pp = Call(sim, "CreateAndAddPropertyPackage", Text(model["property_package"]));
        if (pp == null) throw new ArgumentException("Property package not available");
        foreach (object value in (IEnumerable)model["objects"])
        {
            var spec = Map(value);
            object obj = AddObject(sim, Text(spec["type"]), Convert.ToInt32(spec["x"]), Convert.ToInt32(spec["y"]), Text(spec["tag"]));
            var package = Property(obj, "PropertyPackage");
            if (package != null && package.CanWrite) package.SetValue(obj, pp, null);
            Apply(sim, obj, spec);
        }
        foreach (object value in (IEnumerable)model["connections"])
        {
            var connection = Map(value);
            Call(sim, "ConnectObjects", Get(Find(sim, Text(connection["source"])), "GraphicObject"),
                Get(Find(sim, Text(connection["target"])), "GraphicObject"), connection["source_port"], connection["target_port"]);
        }
    }
    static Dictionary<string, object> Read(object sim, bool inspect, HashSet<string> selected)
    {
        var streams = new List<object>(); var units = new List<object>(); var energy = new List<object>();
        var objects = new List<object>(); var errors = new List<string>(); var packages = new List<object>();
        foreach (DictionaryEntry entry in Objects(sim))
        {
            object obj = entry.Value; string type = obj.GetType().Name;
            if (inspect && selected.Count > 0 && !selected.Contains(Tag(obj)) && !selected.Contains(Text(Get(obj, "Name")))) continue;
            var item = Dict("tag", Tag(obj), "name", Text(Get(obj, "Name")), "type", type,
                "calculated", Get(Get(obj, "GraphicObject"), "Calculated"));
            if (type == "MaterialStream")
            {
                item["temperature_k"] = Optional(obj, "GetTemperature"); item["pressure_pa"] = Optional(obj, "GetPressure");
                item["mass_flow_kg_s"] = Optional(obj, "GetMassFlow"); item["compounds"] = Optional(obj, "GetCompoundNames");
                item["mole_fractions"] = Optional(obj, "GetOverallComposition");
                if (!inspect)
                {
                    bool valid = true;
                    foreach (string key in new string[] { "temperature_k", "pressure_pa", "mass_flow_kg_s" })
                    {
                        double number = item[key] == null ? Double.NaN : Convert.ToDouble(item[key]);
                        if (Double.IsNaN(number) || Double.IsInfinity(number) || (key == "mass_flow_kg_s" ? number < 0 : number <= 0)) valid = false;
                    }
                    var fractions = item["mole_fractions"] as double[];
                    var names = item["compounds"] as string[];
                    double sum = 0;
                    if (fractions == null || names == null || fractions.Length != names.Length || fractions.Length == 0) valid = false;
                    else foreach (double fraction in fractions)
                    {
                        if (Double.IsNaN(fraction) || Double.IsInfinity(fraction) || fraction < -1e-10 || fraction > 1+1e-10) valid = false;
                        sum += fraction;
                    }
                    if (Math.Abs(sum-1) > 1e-6) valid = false;
                    if (!valid) errors.Add("Incomplete or invalid material stream result: " + Tag(obj));
                }
                streams.Add(item);
            }
            else if (type == "EnergyStream") { item["energy_flow_kw"] = Get(obj, "EnergyFlow"); energy.Add(item); }
            else { if (!inspect) item["properties"] = Properties(sim, obj); units.Add(item); }
            if (inspect)
            {
                if (selected.Count > 0) item["properties"] = Properties(sim, obj);
                item["parameters"] = Parameters(obj);
                item["input_ports"] = Ports(obj, "InputConnectors"); item["output_ports"] = Ports(obj, "OutputConnectors");
                var ec = Get(Get(obj, "GraphicObject"), "EnergyConnector");
                item["energy_connector"] = ec == null ? null : Dict("attached", Get(ec, "IsAttached"), "type", Text(Get(ec, "Type")));
            }
            objects.Add(item);
        }
        foreach (DictionaryEntry entry in (IDictionary)Get(sim, "PropertyPackages"))
            packages.Add(Dict("id", Text(entry.Key), "name", Text(Get(entry.Value, "ComponentName")), "type", entry.Value.GetType().Name));
        return Dict("solved", Convert.ToBoolean(Get(sim, "Solved")), "error", Text(Get(sim, "ErrorMessage")),
            "stream_count", streams.Count, "unit_count", units.Count, "streams", streams, "unit_operations", units,
            "energy_streams", inspect ? null : energy, "objects", inspect ? objects : null, "property_packages", packages, "result_errors", errors,
            "total_objects", Objects(sim).Count);
    }
    static object Sanitize(object value)
    {
        if (value == null || value is string || value is bool) return value;
        if (value is double && (Double.IsNaN((double)value) || Double.IsInfinity((double)value))) return null;
        if (value is IDictionary) { var d = Dict(); foreach (DictionaryEntry p in (IDictionary)value) d[Text(p.Key)] = Sanitize(p.Value); return d; }
        if (value is IEnumerable) { var list = new List<object>(); foreach (object v in (IEnumerable)value) list.Add(Sanitize(v)); return list; }
        if (value.GetType().IsEnum) return value.ToString();
        return value;
    }

    [STAThread]
    static int Main(string[] args)
    {
        // DWSIM and plugin diagnostics must never leak into the parent MCP protocol.
        Console.SetOut(Console.Error);
        if (args.Length != 3) { Console.Error.WriteLine("usage: DwsimRunner request.json result.json DWSIM_HOME"); return 2; }
        string output = Path.GetFullPath(args[1]);
        try
        {
            Home = Path.GetFullPath(args[2]); Directory.SetCurrentDirectory(Home);
            AppDomain.CurrentDomain.AssemblyResolve += Resolve;
            var request = Map(Json.DeserializeObject(File.ReadAllText(Path.GetFullPath(args[0]), Encoding.UTF8)));
            string action = Text(request["action"]);
            object automation = Activator.CreateInstance(Assembly.LoadFrom(Path.Combine(Home, "DWSIM.Automation.dll")).GetType("DWSIM.Automation.Automation3"));
            object sim = action == "create" || action == "catalog" ? Call(automation, "CreateFlowsheet") : Call(automation, "LoadFlowsheet", Text(request["flowsheet_path"]));
            Dictionary<string, object> result;
            if (action == "catalog")
            {
                var compounds = new List<object>(); var available = (IDictionary)Get(sim, "AvailableCompounds");
                foreach (DictionaryEntry c in available) compounds.Add(Dict("name", Text(c.Key), "cas", Text(Get(c.Value, "CAS_Number")), "formula", Text(Get(c.Value, "Formula"))));
                result = Dict("compounds", compounds, "property_packages", Call(sim, "GetAvailablePropertyPackages"), "object_types", Creatable);
            }
            else
            {
                if (action == "create") Build(sim, Map(request["model"]));
                foreach (object spec in (IEnumerable)Value(request, "updates", new object[0])) Apply(sim, Find(sim, Text(Map(spec)["tag"])), Map(spec));
                var solverErrors = new List<string>();
                if (action != "inspect")
                {
                    try { Call(automation, "CalculateFlowsheet2", sim); }
                    catch (TargetInvocationException ex) { solverErrors.Add(ex.GetBaseException().Message); }
                }
                var selected = new HashSet<string>();
                foreach (object tag in (IEnumerable)Value(request, "object_tags", new object[0])) { Find(sim, Text(tag)); selected.Add(Text(tag)); }
                result = Read(sim, action == "inspect", selected); result["solver_errors"] = solverErrors;
                if (action == "inspect") { result["streams"] = null; result["unit_operations"] = null; }
                if (solverErrors.Count > 0) result["solved"] = false;
                if (action != "inspect")
                {
                    string save = Text(request["save_path"]);
                    Call(automation, "SaveFlowsheet", sim, save, true); result["saved_flowsheet"] = save;
                }
            }
            result["engine"] = "DWSIM";
            result["version"] = FileVersionInfo.GetVersionInfo(Path.Combine(Home, "DWSIM.exe")).FileVersion;
            result["action"] = action;
            result["units"] = Dict("temperature", "K", "pressure", "Pa", "mass_flow", "kg/s", "energy_flow", "kW", "composition", "mole fraction");
            File.WriteAllText(output, Json.Serialize(Sanitize(result)), new UTF8Encoding(false));
            return 0;
        }
        catch (Exception ex)
        {
            string message = ex.GetBaseException().ToString(); Console.Error.WriteLine(message);
            File.WriteAllText(output, Json.Serialize(Dict("solved", false, "error", message)), new UTF8Encoding(false));
            return 1;
        }
    }
}
