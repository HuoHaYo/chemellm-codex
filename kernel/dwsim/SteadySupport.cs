// Verified DWSIM 9.x structural adapters. These members are selected by code, never arbitrary request paths.
using System; using System.Collections; using System.Collections.Generic; using System.Reflection;

partial class DwsimRunner
{
    static void Set(object obj,string name,object value) {
        var p=Property(obj,name); if(p!=null && p.CanWrite){p.SetValue(obj,ConvertArg(value,p.PropertyType),null);return;}
        var f=obj.GetType().GetField(name,BindingFlags.Public|BindingFlags.Instance);
        if(f!=null){f.SetValue(obj,ConvertArg(value,f.FieldType));return;}
        throw new ArgumentException("Unsupported native member "+obj.GetType().Name+"."+name);
    }
    static Dictionary<string,object> Scalars(object obj) {
        var d=Dict(); if(obj==null)return d;
        foreach(var p in obj.GetType().GetProperties()) if(p.GetIndexParameters().Length==0 && p.CanRead) try {
            Type t=Nullable.GetUnderlyingType(p.PropertyType)??p.PropertyType;
            if(t.IsPrimitive || t.IsEnum || t==typeof(string))d[p.Name]=t.IsEnum?Text(p.GetValue(obj,null)):p.GetValue(obj,null);
        }catch{}
        foreach(var f in obj.GetType().GetFields())if(f.FieldType.IsPrimitive||f.FieldType==typeof(string))d[f.Name]=f.GetValue(obj);
        return d;
    }
    static object Package(object sim,string id) {
        foreach(DictionaryEntry p in (IDictionary)Get(sim,"PropertyPackages"))
            if(Text(p.Key)==id || Text(Get(p.Value,"Tag"))==id || Text(Get(p.Value,"UniqueID"))==id)return p.Value;
        throw new ArgumentException("Unknown property package key: "+id);
    }
    static IDictionary<string,object> Metadata(object sim){return (IDictionary<string,object>)Get(sim,"ExtraProperties");}
    static object ConfigurationMetadata(object sim){object value;return Metadata(sim).TryGetValue("ChemELLM_configuration",out value)?Json.DeserializeObject(Text(value)):Dict();}
    static object InteractionModel(object pp,string name){return Get(pp,name=="PR"?"m_pr":name=="SRK"?"m_srk":"m_uni");}
    static object PairData(object sim,object pp,string model,string a,string b,bool create) {
        object aux=InteractionModel(pp,model); var table=Get(aux,"InteractionParameters") as IDictionary;
        if(table==null)return null;
        var compounds=(IDictionary)Get(sim,"SelectedCompounds");
        string[] aa={a,Text(Get(compounds[a],"CAS_Number"))},bb={b,Text(Get(compounds[b],"CAS_Number"))};
        foreach(string i in aa)foreach(string j in bb)if(table.Contains(i)) {var row=(IDictionary)table[i];if(row.Contains(j))return row[j];}
        if(!create)return null;
        string first=a,second=b;
        // NRTL/UNIQUAC tables use compound names; cubic EOS tables use CAS numbers.
        if(model=="PR" || model=="SRK"){first=aa[1];second=bb[1];}
        if(!table.Contains(first))table[first]=Activator.CreateInstance(table.GetType().GetGenericArguments()[1]);
        var target=(IDictionary)table[first];object data=Activator.CreateInstance(target.GetType().GetGenericArguments()[1]);target[second]=data;return data;
    }
    static void ConfigurePackages(object sim,IEnumerable specs) {
        var metadata=Map(ConfigurationMetadata(sim));
        foreach(object raw in specs) {
            var spec=Map(raw);string key=Text(spec["key"]);object pp=null;
            try{pp=Package(sim,key);}catch(ArgumentException){}
            if(pp==null) {pp=Call(sim,"CreateAndAddPropertyPackage",Text(spec["name"]));Set(pp,"Tag",key);}
            else if(Text(Get(pp,"ComponentName"))!=Text(spec["name"]))throw new ArgumentException("Existing package key has a different model: "+key);
            foreach(object iraw in (IEnumerable)Value(spec,"interactions",new object[0])) {
                var ip=Map(iraw);string a=Text(ip["compound1"]),b=Text(ip["compound2"]),model=Text(ip["model"]);
                if(ModelName(pp)!=model)throw new ArgumentException("Interaction model does not match property package: "+key);
                if(PairData(sim,pp,model,a,b,false)==null && PairData(sim,pp,model,b,a,false)!=null)
                    throw new ArgumentException("Use the compound orientation returned by inspect for "+b+" / "+a);
                object data=PairData(sim,pp,model,a,b,true);if(data==null)throw new ArgumentException("Unsupported interaction model: "+model);
                foreach(var field in Map(ip["parameters"])) {
                    if(!Scalars(data).ContainsKey(field.Key) || !(Scalars(data)[field.Key] is double)) throw new ArgumentException("Inspect numeric interaction member before setting: "+field.Key);
                    Set(data,field.Key,field.Value);
                }
            }
            metadata[key]=spec;
        }
        Metadata(sim)["ChemELLM_configuration"]=Json.Serialize(metadata);
    }
    static void ConfigureReactions(object sim,Dictionary<string,object> model) {
        foreach(object raw in (IEnumerable)Value(model,"reactions",new object[0])) {
            var spec=Map(raw);var stoich=new Dictionary<string,double>();var elements=new Dictionary<string,double>();double mass=0;bool known=true;
            foreach(var c in Map(spec["stoichiometry"])) {
                double coefficient=Convert.ToDouble(c.Value);stoich[c.Key]=coefficient;
                object cp=((IDictionary)Get(sim,"SelectedCompounds"))[c.Key];if(cp==null)throw new ArgumentException("Reaction compound not selected: "+c.Key);
                mass+=coefficient*Convert.ToDouble(Get(cp,"Molar_Weight"));
                var table=Get(cp,"Elements") as IDictionary;if(table==null||table.Count==0){known=false;continue;}
                foreach(DictionaryEntry e in table){string key=Text(e.Key);elements[key]=(elements.ContainsKey(key)?elements[key]:0)+coefficient*Convert.ToDouble(e.Value);}
            }
            if(!known)throw new ArgumentException("Element data missing: cannot validate reaction "+Text(spec["key"]));
            foreach(var e in elements)if(Math.Abs(e.Value)>1e-8)throw new ArgumentException("Unbalanced reaction element "+e.Key+": "+Text(spec["key"]));
            if(Math.Abs(mass)>.02)throw new ArgumentException("Reaction molecular mass inconsistent: "+Text(spec["key"]));
            string key2=Text(spec["key"]);object reaction;
            if(Text(spec["type"])=="Conversion")
                reaction=Call(sim,"CreateConversionReaction",key2,Text(spec["source"]),stoich,Text(spec["base_compound"]),Text(spec["phase"]),Text(Convert.ToDouble(spec["conversion"])*100));
            else {
                reaction=Call(sim,"CreateEquilibriumReaction",key2,Text(spec["source"]),stoich,Text(spec["base_compound"]),Text(spec["phase"]),"Fugacity","Pa",0.0,"");
                Set(reaction,"KExprType",spec["equilibrium"]);
                if(Text(spec["equilibrium"])=="Constant"){Set(reaction,"KExprType","Constant");Set(reaction,"ConstantKeqValue",spec["equilibrium_constant"]);}
            }
            Set(reaction,"Tmin",spec["temperature_min_k"]);Set(reaction,"Tmax",spec["temperature_max_k"]);Call(sim,"AddReaction",reaction);
        }
        foreach(object raw in (IEnumerable)Value(model,"reaction_sets",new object[0])) {
            var spec=Map(raw);object set=Call(sim,"CreateReactionSet",Text(spec["key"]),"ChemELLM explicit reaction set");Call(sim,"AddReactionSet",set);
            int rank=0;foreach(object key in (IEnumerable)spec["reactions"]) {
                object r=Call(sim,"GetReaction",Text(key));Call(sim,"AddReactionToSet",Text(Get(r,"ID")),Text(Get(set,"ID")),true,rank++);
            }
        }
    }
    static string ModelName(object pp){string type=pp.GetType().Name;return type.Contains("NRTL")?"NRTL":type.Contains("UNIQUAC")?"UNIQUAC":type.Contains("Soave")?"SRK":type.Contains("PengRobinson")?"PR":"";}
    static object Interactions(object sim,object pp) {
        var rows=new List<object>();var names=new List<string>();foreach(DictionaryEntry c in (IDictionary)Get(sim,"SelectedCompounds"))names.Add(Text(c.Key));
        string model=ModelName(pp);
        if(model=="")return rows;
        for(int i=0;i<names.Count;i++)for(int j=i+1;j<names.Count;j++) {
            string a=names[i],b=names[j]; object data=PairData(sim,pp,model,a,b,false);
            if(data==null){data=PairData(sim,pp,model,b,a,false);if(data!=null){a=names[j];b=names[i];}}
            var row=Dict("compound1",a,"compound2",b,"model",model,"parameters",data==null?null:Scalars(data),
                "origin",data==null?"missing_or_implicit_default":"native_database_or_default",
                "source",data==null?"":Text(Get(data,"comment")),"conditions","待核对",
                "temperature_form",model=="PR"||model=="SRK"?"constant kij":model=="NRTL"?"tau_ij = (Aij + Bij*T + Cij*T^2)/(1.98721*T)":"tau_ij = exp((-Aij + Bij*T + Cij*T^2)/(1.98721*T))",
                "coefficient_units",model=="PR"||model=="SRK"?"kij dimensionless":"T K; A cal/mol; B cal/(mol*K); C cal/(mol*K^2); alpha dimensionless (NRTL)",
                "note","原生数据库/默认参数，来源和适用性需核对；零值不证明经过实验验证。");
            var meta=Map(ConfigurationMetadata(sim));object configured;
            if(meta.TryGetValue(Text(Get(pp,"Tag")),out configured))foreach(object iraw in (IEnumerable)Value(Map(configured),"interactions",new object[0])){
                var ip=Map(iraw);if(Text(ip["compound1"])==a && Text(ip["compound2"])==b){row["origin"]="user_specified";row["source"]=ip["source"];row["conditions"]=ip["conditions"];row["notes"]=ip["notes"];}}
            rows.Add(row);
        }
        return rows;
    }
    static object Settings(object obj) {
        var values=Dict();foreach(object p in Parameters(obj)){var d=Map(p);values[Text(d["name"])]=d["value"];}
        var specs=Get(obj,"Specs") as IDictionary;if(specs!=null){var d=Dict();foreach(DictionaryEntry s in specs)d[Text(s.Key)]=Scalars(s.Value);values["column_specs"]=d;}
        var stages=Get(obj,"Stages") as IList;if(stages!=null){var d=new List<object>();foreach(object s in stages)d.Add(Dict("id",Get(s,"ID"),"pressure_pa",Get(s,"P"),"efficiency",Get(s,"Efficiency")));values["stages"]=d;}
        var feeds=Get(obj,"MaterialStreams") as IDictionary;if(feeds!=null){var d=Dict();foreach(DictionaryEntry s in feeds)d[Text(s.Key)]=Scalars(s.Value);values["material_streams"]=d;}
        if(obj.GetType().Name=="Splitter")values["ratios"]=Get(obj,"Ratios");
        if(obj.GetType().Name=="ShortcutColumn")foreach(string n in new[]{"m_lightkey","m_heavykey","m_lightkeymolarfrac","m_heavykeymolarfrac","m_refluxratio","m_condenserpressure","m_boilerpressure","m_N","m_Nmin","m_Rmin","m_Qc","m_Qb"})values[n]=Get(obj,n);
        var spec=ReadAnnotation(obj);if(spec!=null)values["structured_spec"]=spec;
        return values;
    }
    const string Prefix="\nChemELLM.spec:";
    static object ReadAnnotation(object obj){string text=Text(Get(obj,"Annotation"));int i=text.IndexOf(Prefix);if(i<0)return null;try{return Json.DeserializeObject(text.Substring(i+Prefix.Length));}catch{return null;}}
    static void Remember(object obj,object spec){string text=Text(Get(obj,"Annotation"));int i=text.IndexOf(Prefix);if(i>=0)text=text.Substring(0,i);Set(obj,"Annotation",text+Prefix+Json.Serialize(spec));}
    static object Diagnostics(object obj) {
        var d=Dict("error",Text(Get(obj,"ErrorMessage")));
        foreach(string n in new[]{"Converged","IterationsTaken","IterationCount","Errors","MaximumIterations","MaxIterations","InternalLoopTolerance","ExternalLoopTolerance","ColumnSolverConvergenceReport","ReactionExtents","ComponentConversions","DeltaQ","DeltaT"})
            if(Get(obj,n)!=null)d[n]=Get(obj,n);
        if(Get(obj,"ConvergenceParameters")!=null)d["tolerances"]=Scalars(Get(obj,"ConvergenceParameters"));
        return d;
    }

    static object ReactionData(object sim){var list=new List<object>();foreach(DictionaryEntry r in (IDictionary)Get(sim,"Reactions"))list.Add(Scalars(r.Value));return list;}
    static bool ColumnConnection(object sim,Dictionary<string,object> model,Dictionary<string,object> link) {
        foreach(object raw in (IEnumerable)model["objects"]){var o=Map(raw);var s=Value(o,"spec",null) as Dictionary<string,object>;
            if(s==null || Text(s["mode"])!="column")continue;var c=Map(s["column"]);string tag=Text(o["tag"]);object tower=Find(sim,tag);
            int n=Convert.ToInt32(c["stages"]);if(Convert.ToInt32(Get(tower,"NumberOfStages"))!=n)Call(tower,"SetNumberOfStages",n);
            if(Text(link["target"])==tag && Map(c["feed_stages"]).ContainsKey(Text(link["source"]))) {
                Call(tower,"ConnectFeed",Find(sim,Text(link["source"])),Convert.ToInt32(Map(c["feed_stages"])[Text(link["source"])])-1);return true;}
            if(Text(link["source"])==tag && Text(link["target"])==Text(c["distillate_stream"])) {Call(tower,"ConnectDistillate",Find(sim,Text(link["target"])));return true;}
            if(Text(link["source"])==tag && Text(link["target"])==Text(c["bottoms_stream"])) {Call(tower,"ConnectBottoms",Find(sim,Text(link["target"])));return true;}
        }return false;
    }
    static void Advanced(object sim,object obj,Dictionary<string,object> raw) {
        if(Value(raw,"spec",null)==null)return;var spec=Map(raw["spec"]);string mode=Text(spec["mode"]);
        if(mode=="column") {
            var c=Map(spec["column"]);int count=Convert.ToInt32(c["stages"]);if(Convert.ToInt32(Get(obj,"NumberOfStages"))!=count)Call(obj,"SetNumberOfStages",count);Set(obj,"CondenserType",c["condenser"]);
            Call(obj,"CheckConnPos");
            Set(obj,"MaxIterations",c["maximum_iterations"]);Set(obj,"InternalLoopTolerance",c["solver_tolerance"]);Set(obj,"ExternalLoopTolerance",c["external_tolerance"]);
            var stages=(IList)Get(obj,"Stages");double top=Convert.ToDouble(c["top_pressure_pa"]),bottom=Convert.ToDouble(c["bottom_pressure_pa"]);
            for(int i=0;i<stages.Count;i++){Set(stages[i],"P",top+(bottom-top)*i/(stages.Count-1));Set(stages[i],"Efficiency",c["stage_efficiency"]);}
            foreach(var feed in Map(c["feed_stages"]))Call(obj,"SetStreamFeedStage",Text(Get(Find(sim,feed.Key),"Name")),Convert.ToInt32(feed.Value)-1);
            var native=(IDictionary)Get(obj,"Specs");foreach(string end in new[]{"C","R"}) {
                var s=Map(c[end=="C"?"condenser_spec":"reboiler_spec"]);object dest=native[end];Set(dest,"SType",s["type"]);Set(dest,"SpecValue",s["value"]);Set(dest,"SpecUnit",s["unit"]);
                if(Text(s["component"])!="") {Set(dest,"ComponentID",s["component"]);int index=0;foreach(DictionaryEntry cp in (IDictionary)Get(sim,"SelectedCompounds")){if(Text(cp.Key)==Text(s["component"]))break;index++;}Set(dest,"ComponentIndex",index);}
            }
            Set(obj,"CreateSolverConvergengeReport",true);
        }
        if(mode=="shortcut") {
            Set(obj,"m_lightkey",spec["light_key"]);Set(obj,"m_heavykey",spec["heavy_key"]);
            Set(obj,"m_lightkeymolarfrac",spec["light_key_in_bottoms"]);Set(obj,"m_heavykeymolarfrac",spec["heavy_key_in_distillate"]);
            Set(obj,"m_refluxratio",spec["reflux_ratio"]);Set(obj,"m_condenserpressure",spec["pressure_pa"]);Set(obj,"m_boilerpressure",spec["pressure_pa"]);
        }
        if(mode=="split_ratios"){var ratios=(IList)Get(obj,"Ratios");ratios.Clear();foreach(object v in (IEnumerable)spec["ratios"])ratios.Add(Convert.ToDouble(v));while(ratios.Count<3)ratios.Add(0.0);}
        if(mode=="recycle") {
            object cp=Get(obj,"ConvergenceParameters");foreach(var pair in new[]{new[]{"temperature_tolerance_k","Temperatura"},new[]{"pressure_tolerance_pa","Pressao"},new[]{"mass_flow_tolerance_kg_s","VazaoMassica"},new[]{"composition_tolerance","Composicao"}})
                if(Value(spec,pair[0],null)!=null)Set(cp,pair[1],spec[pair[0]]);
        }
        if(mode=="reaction") {
            string name=Text(spec["reaction_set"]);object set=null;foreach(DictionaryEntry r in (IDictionary)Get(sim,"ReactionSets"))
                if(Text(r.Key)==name||Text(Get(r.Value,"Name"))==name||Text(Get(r.Value,"ID"))==name)set=r.Value;
            if(set==null)throw new ArgumentException("Reaction set not found: "+name);
            Set(obj,"ReactionSetID",Get(set,"ID"));
        }
        Remember(obj,spec);
    }
}
