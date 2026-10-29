using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;

class DwsimRunner
{
    static string DwsimDir;

    static Assembly ResolveAssembly(object sender, ResolveEventArgs args)
    {
        string simple = new AssemblyName(args.Name).Name + ".dll";
        string[] dirs = new string[] {
            DwsimDir,
            Path.Combine(DwsimDir, "ThermoCS"),
            Path.Combine(DwsimDir, "Lib"),
            Path.Combine(DwsimDir, "runtimes"),
            Path.Combine(DwsimDir, "extenders")
        };
        foreach (string dir in dirs)
        {
            string path = Path.Combine(dir, simple);
            if (File.Exists(path)) return Assembly.LoadFrom(path);
        }
        return null;
    }

    static object GetProperty(object obj, string name)
    {
        PropertyInfo p = obj.GetType().GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
        return p == null ? null : p.GetValue(obj, null);
    }

    static string Str(object value)
    {
        return value == null ? "" : Convert.ToString(value, CultureInfo.InvariantCulture);
    }

    static double Num(object value)
    {
        if (value == null) return 0.0;
        return Convert.ToDouble(value, CultureInfo.InvariantCulture);
    }

    static object Call(object obj, string name)
    {
        MethodInfo m = obj.GetType().GetMethod(name, BindingFlags.Public | BindingFlags.Instance, null, Type.EmptyTypes, null);
        if (m == null) return null;
        return m.Invoke(obj, null);
    }

    static string Q(string value)
    {
        if (value == null) value = "";
        StringBuilder builder = new StringBuilder("\"");
        foreach (char ch in value)
        {
            switch (ch)
            {
                case '\\': builder.Append("\\\\"); break;
                case '"': builder.Append("\\\""); break;
                case '\n': builder.Append("\\n"); break;
                case '\r': builder.Append("\\r"); break;
                case '\t': builder.Append("\\t"); break;
                default: builder.Append(ch); break;
            }
        }
        builder.Append("\"");
        return builder.ToString();
    }

    static string N(double value)
    {
        if (double.IsNaN(value) || double.IsInfinity(value)) return "null";
        return value.ToString("0.##########", CultureInfo.InvariantCulture);
    }

    static string A(double[] values)
    {
        if (values == null) return "[]";
        StringBuilder builder = new StringBuilder("[");
        for (int i = 0; i < values.Length; i++)
        {
            if (i > 0) builder.Append(", ");
            builder.Append(N(values[i]));
        }
        builder.Append("]");
        return builder.ToString();
    }

    static string S(string[] values)
    {
        if (values == null) return "[]";
        StringBuilder builder = new StringBuilder("[");
        for (int i = 0; i < values.Length; i++)
        {
            if (i > 0) builder.Append(", ");
            builder.Append(Q(values[i]));
        }
        builder.Append("]");
        return builder.ToString();
    }

    static void Main(string[] args)
    {
        if (args.Length < 3)
        {
            Console.Error.WriteLine("usage: DwsimRunner <run|inspect> <flowsheet> <output.json> [--temp <dir>] [--dwsim <dir>]");
            Environment.Exit(2);
        }
        try
        {
            string command = args[0].ToLowerInvariant();
            string flowsheet = Path.GetFullPath(args[1]);
            string output = Path.GetFullPath(args[2]);
            string temp = null;
            string dwsim = null;
            for (int i = 3; i < args.Length; i++)
            {
                if (args[i] == "--temp" && i + 1 < args.Length) temp = args[++i];
                else if (args[i] == "--dwsim" && i + 1 < args.Length) dwsim = args[++i];
            }
            if (!File.Exists(flowsheet)) throw new FileNotFoundException("flowsheet not found", flowsheet);
            if (dwsim == null) dwsim = Environment.GetEnvironmentVariable("DWSIM_HOME");
            if (String.IsNullOrEmpty(dwsim)) dwsim = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DWSIM");
            DwsimDir = Path.GetFullPath(dwsim);
            string automationDll = Path.Combine(DwsimDir, "DWSIM.Automation.dll");
            if (!File.Exists(automationDll)) throw new FileNotFoundException("DWSIM.Automation.dll not found", automationDll);
            if (temp == null) temp = Path.Combine(Path.GetTempPath(), "chemellm-dwsim");
            temp = Path.GetFullPath(temp);
            Directory.CreateDirectory(temp);
            Environment.SetEnvironmentVariable("TEMP", temp);
            Environment.SetEnvironmentVariable("TMP", temp);
            Directory.SetCurrentDirectory(DwsimDir);
            AppDomain.CurrentDomain.AssemblyResolve += ResolveAssembly;

            Assembly asm = Assembly.LoadFrom(automationDll);
            Type automationType = asm.GetType("DWSIM.Automation.Automation3");
            if (automationType == null) throw new Exception("DWSIM.Automation.Automation3 not found; DWSIM version unsupported");
            object automation = Activator.CreateInstance(automationType);
            MethodInfo load = automationType.GetMethod("LoadFlowsheet", new Type[] { typeof(string) });
            if (load == null) throw new Exception("LoadFlowsheet(string) not found");
            object sim = load.Invoke(automation, new object[] { flowsheet });

            string version = "";
            try
            {
                FileVersionInfo info = FileVersionInfo.GetVersionInfo(Path.Combine(DwsimDir, "DWSIM.exe"));
                version = info.FileVersion;
            }
            catch { }
            if (String.IsNullOrEmpty(version))
            {
                try { version = Str(automationType.GetMethod("GetVersion").Invoke(automation, null)); }
                catch { }
            }

            StringBuilder json = new StringBuilder();
            json.Append("{\n");
            json.Append("  \"engine\": \"DWSIM\",\n");
            json.Append("  \"version\": ").Append(Q(version)).Append(",\n");
            json.Append("  \"flowsheet\": ").Append(Q(flowsheet)).Append(",\n");
            json.Append("  \"command\": ").Append(Q(command)).Append(",\n");

            if (command == "inspect")
            {
                object objects0 = GetProperty(sim, "SimulationObjects");
                IDictionary dict0 = objects0 as IDictionary;
                json.Append("  \"object_count\": ").Append(dict0 == null ? 0 : dict0.Count).Append(",\n");
                json.Append("  \"solved\": ").Append(Convert.ToBoolean(GetProperty(sim, "Solved")) ? "true" : "false").Append(",\n");
                json.Append("  \"error\": ").Append(Q(Str(GetProperty(sim, "ErrorMessage")))).Append(",\n");
                json.Append("  \"objects\": [\n");
                bool first0 = true;
                if (dict0 != null)
                {
                    foreach (DictionaryEntry entry in dict0)
                    {
                        object item = entry.Value;
                        if (!first0) json.Append(",\n");
                        first0 = false;
                        json.Append("    {\"name\": ").Append(Q(Str(GetProperty(item, "Name")))).Append(", \"type\": ").Append(Q(item.GetType().Name)).Append("}");
                    }
                }
                json.Append("\n  ]\n}\n");
            }
            else
            {
                MethodInfo calculate = null;
                foreach (MethodInfo m in automationType.GetMethods())
                    if (m.Name == "CalculateFlowsheet" && m.GetParameters().Length == 2) calculate = m;
                if (calculate == null) throw new Exception("CalculateFlowsheet(IFlowsheet, ISimulationObject) not found");
                calculate.Invoke(automation, new object[] { sim, null });
                bool solved = Convert.ToBoolean(GetProperty(sim, "Solved"));
                string error = Str(GetProperty(sim, "ErrorMessage"));

                object objectsValue = GetProperty(sim, "SimulationObjects");
                IDictionary objects = objectsValue as IDictionary;
                List<string> streams = new List<string>();
                List<string> units = new List<string>();
                if (objects != null)
                {
                    foreach (DictionaryEntry entry in objects)
                    {
                        object obj = entry.Value;
                        string typeName = obj.GetType().Name;
                        string name = Str(GetProperty(obj, "Name")); object graphic = GetProperty(obj, "GraphicObject"); string tag = Str(GetProperty(graphic, "Tag")); string description = Str(GetProperty(graphic, "Description"));
                        if (typeName.IndexOf("MaterialStream", StringComparison.OrdinalIgnoreCase) >= 0)
                        {
                            double temperature = 0, pressure = 0, massFlow = 0;
                            string[] compounds = null;
                            double[] composition = null;
                            string streamError = "";
                            try
                            {
                                temperature = Num(Call(obj, "GetTemperature"));
                                pressure = Num(Call(obj, "GetPressure"));
                                massFlow = Num(Call(obj, "GetMassFlow"));
                                compounds = Call(obj, "GetCompoundNames") as string[];
                                composition = Call(obj, "GetOverallComposition") as double[];
                            }
                            catch (Exception ex) { streamError = ex.Message; }
                            streams.Add("    {\"tag\": " + Q(tag) + ", \"description\": " + Q(description) + ", \"name\": " + Q(name) + ", \"type\": " + Q(typeName)
                                + ", \"temperature_k\": " + N(temperature)
                                + ", \"pressure_pa\": " + N(pressure)
                                + ", \"mass_flow_kg_s\": " + N(massFlow)
                                + ", \"compounds\": " + S(compounds)
                                + ", \"mole_fractions\": " + A(composition)
                                + (streamError.Length > 0 ? ", \"error\": " + Q(streamError) : "")
                                + "}");
                        }
                        else if (typeName.IndexOf("EnergyStream", StringComparison.OrdinalIgnoreCase) < 0)
                        {
                            units.Add("    {\"tag\": " + Q(tag) + ", \"description\": " + Q(description) + ", \"name\": " + Q(name) + ", \"type\": " + Q(typeName) + "}");
                        }
                    }
                }
                json.Append("  \"solved\": ").Append(solved ? "true" : "false").Append(",\n");
                json.Append("  \"error\": ").Append(Q(error)).Append(",\n");
                json.Append("  \"stream_count\": ").Append(streams.Count).Append(",\n");
                json.Append("  \"unit_count\": ").Append(units.Count).Append(",\n");
                json.Append("  \"streams\": [\n").Append(String.Join(",\n", streams.ToArray())).Append("\n  ],\n");
                json.Append("  \"unit_operations\": [\n").Append(String.Join(",\n", units.ToArray())).Append("\n  ]\n}\n");
            }
            File.WriteAllText(output, json.ToString(), new UTF8Encoding(false));
            Console.WriteLine("OK " + output);
            Environment.Exit(0);
        }
        catch (Exception ex)
        {
            string message = ex.ToString();
            if (ex.InnerException != null) message += "\nINNER: " + ex.InnerException.ToString();
            try { File.WriteAllText(args.Length > 2 ? Path.GetFullPath(args[2]) : "dwsim-error.txt", "{\"solved\": false, \"error\": " + Q(message) + "}", new UTF8Encoding(false)); } catch { }
            Console.Error.WriteLine(message);
            Environment.Exit(1);
        }
    }
}
