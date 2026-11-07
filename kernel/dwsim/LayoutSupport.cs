// Native graphics inspection, manipulation and Skia rendering; no solver calls.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Xml.Linq;
using SkiaSharp;

partial class DwsimRunner
{
    const string LayoutKey = "ChemELLM_layout_v1";
    const string LayoutPrefix = "CHEMELLM-LAYOUT-";

    static List<object> Graphics(object sim) {
        var result=new List<object>();
        foreach(DictionaryEntry e in (IDictionary)Get(sim,"GraphicObjects")) result.Add(e.Value);
        return result;
    }
    static object LayoutMetadata(object sim) {
        object value;return Metadata(sim).TryGetValue(LayoutKey,out value)?Json.DeserializeObject(Text(value)):Dict();
    }
    static double Number(object obj,string key,double fallback) {
        var v=Get(obj,key);return v==null?fallback:Convert.ToDouble(v);
    }
    static void Font(object go) {
        // Windows' CJK font also contains Latin glyphs; the same face is used for measurement.
        foreach(string p in new[]{"RegularTypeFace","BoldTypeFace","ItalicTypeFace","BoldItalicTypeFace"})
            if(Property(go,p)!=null && Property(go,p).CanWrite) Set(go,p,SKTypeface.FromFamilyName("Microsoft YaHei"));
    }
    static List<object> NativeEdges(object sim) {
        var result=new List<object>();var seen=new HashSet<object>();
        foreach(var go in Graphics(sim)) {
            var ports=new List<object>();
            foreach(string key in new[]{"InputConnectors","OutputConnectors","SpecialConnectors"})
                if(Get(go,key) is IEnumerable)foreach(var p in (IEnumerable)Get(go,key))ports.Add(p);
            ports.Add(Get(go,"EnergyConnector"));
            foreach(var p in ports) {
                if(p==null || !Object.Equals(Get(p,"IsAttached"),true))continue;
                var c=Get(p,"AttachedConnector");if(c==null || !seen.Add(c))continue;
                var a=Get(c,"AttachedFrom");var b=Get(c,"AttachedTo");
                if(a==null||b==null)continue;
                Call(c,"SetupPositioning");var pts=new List<object>();
                foreach(var pt in (IEnumerable)Get(c,"PointList"))pts.Add(new[]{Number(pt,"X",0),Number(pt,"Y",0)});
                result.Add(Dict("source_id",Text(Get(a,"Name")),"target_id",Text(Get(b,"Name")),
                    "source",Text(Get(a,"Tag")),"target",Text(Get(b,"Tag")),
                    "source_port",Get(c,"AttachedFromConnectorIndex"),"target_port",Get(c,"AttachedToConnectorIndex"),
                    "source_energy",Get(c,"AttachedFromEnergy"),"target_energy",Get(c,"AttachedToEnergy"),"points",pts));
            }
        }
        return result;
    }
    static object[] NativePorts(object go,string key) {
        var result=new List<object>();var ports=Get(go,key) as IEnumerable;
        if(ports!=null)foreach(var p in ports)result.Add(Dict("index",result.Count,"name",Text(Get(p,"ConnectorName")),
            "x",Number(Get(p,"Position"),"X",0),"y",Number(Get(p,"Position"),"Y",0),
            "attached",Get(p,"IsAttached"),"type",Text(Get(p,"Type"))));
        return result.ToArray();
    }
    static Dictionary<string,object> ReadLayout(object sim,string directory,bool render) {
        Directory.CreateDirectory(directory);
        var graphics=Graphics(sim);var simulationIds=new HashSet<string>();
        foreach(DictionaryEntry e in Objects(sim))simulationIds.Add(Text(Get(e.Value,"Name")));
        var nodes=new List<object>();double left=Double.MaxValue,top=Double.MaxValue,right=Double.MinValue,bottom=Double.MinValue;
        foreach(var go in graphics) {
            Call(go,"PositionConnectors");
            double x=Number(go,"X",0),y=Number(go,"Y",0),w=Number(go,"Width",20),h=Number(go,"Height",20);
            string tag=Text(Get(go,"Tag"));bool isSim=simulationIds.Contains(Text(Get(go,"Name")));
            double fontSize=Number(go,"FontSize",10), labelWidth=0;
            using(var paint=new SKPaint()){paint.Typeface=(Get(go,Text(Get(go,"FontStyle"))=="Bold"?"BoldTypeFace":"RegularTypeFace") as SKTypeface)??SKTypeface.Default;paint.TextSize=(float)fontSize;labelWidth=paint.MeasureText(tag);}
            // DWSIM labels are centered below the symbol. Reserve the native font's full line height.
            var label=new[]{Math.Min(0,(w-labelWidth)/2),0,Math.Max(w,(w+labelWidth)/2),h+fontSize*2.8+8};
            left=Math.Min(left,x+label[0]);top=Math.Min(top,y);right=Math.Max(right,x+label[2]);bottom=Math.Max(bottom,y+label[3]);
            nodes.Add(Dict("name",Text(Get(go,"Name")),"tag",tag,"type",Text(Get(go,"ObjectType")),"simulation",isSim,
                "x",x,"y",y,"width",w,"height",h,"rotation",Get(go,"Rotation"),"flipped_h",Get(go,"FlippedH"),"flipped_v",Get(go,"FlippedV"),
                "label_width",labelWidth,"label_bounds",label,"input_ports",NativePorts(go,"InputConnectors"),"output_ports",NativePorts(go,"OutputConnectors"),
                "energy_port",Get(go,"EnergyConnector")==null?null:Dict("x",Number(Get(Get(go,"EnergyConnector"),"Position"),"X",0),"y",Number(Get(Get(go,"EnergyConnector"),"Position"),"Y",0))));
        }
        var edges=NativeEdges(sim);var metadata=Map(LayoutMetadata(sim));var previews=new List<object>();
        if(nodes.Count>0 && render) {
            var box=new[]{left-50,top-50,right+50,bottom+50};
            previews.Add(RenderNative(sim,directory,"overview",box,2400));
            foreach(var value in (IEnumerable)Value(metadata,"groups",new object[0])) {
                var group=Map(value);var bounds=(IEnumerable)Value(group,"bounds",new object[0]);var rect=new List<double>();
                foreach(var v in bounds)rect.Add(Convert.ToDouble(v));
                if(rect.Count==4) {
                    // Tile wide zones so detail previews retain a readable native label size.
                    for(double yy=rect[1];yy<rect[3];yy+=1300) {
                        for(double xx=rect[0];xx<rect[2];xx+=1800) {
                            previews.Add(RenderNative(sim,directory,"group-"+Text(group["id"])+"-"+previews.Count,
                                new[]{xx,yy,Math.Min(xx+1900,rect[2]),Math.Min(yy+1400,rect[3])},3000));
                            if(xx+1900>=rect[2])break;
                        }
                        if(yy+1400>=rect[3])break;
                    }
                }
            }
        }
        return Dict("nodes",nodes,"edges",edges,"metadata",metadata,"previews",previews);
    }
    static object RenderNative(object sim,string directory,string name,double[] rect,int maxDimension) {
        double scale=Math.Min(1.5,maxDimension/Math.Max(rect[2]-rect[0],rect[3]-rect[1]));
        int width=Math.Max(1,(int)Math.Ceiling((rect[2]-rect[0])*scale)),height=Math.Max(1,(int)Math.Ceiling((rect[3]-rect[1])*scale));
        using(var surface=SKSurface.Create(new SKImageInfo(width,height))) {
            surface.Canvas.Clear(SKColors.White);surface.Canvas.Scale((float)scale);surface.Canvas.Translate((float)-rect[0],(float)-rect[1]);
            var native=Call(sim,"GetSurface");
            Set(native,"Zoom",1.0f);Set(native,"Origin",new SKPoint(0,0));Set(native,"ShowGrid",false);
            Call(native,"UpdateSurface",surface);
            string path=Path.Combine(directory,name+".png");
            using(var snapshot=surface.Snapshot())using(var encoded=snapshot.Encode(SKEncodedImageFormat.Png,100))using(var file=File.Create(path))encoded.SaveTo(file);
            return Dict("path",path,"bounds",rect,"scale",scale,"width",width,"height",height,"renderer","DWSIM native SkiaSharp");
        }
    }
    static Dictionary<string,object> ApplyNativeLayout(object sim,Dictionary<string,object> plan) {
        var updates=new List<object>();var native=(IDictionary)Get(sim,"GraphicObjects");
        var members=new Dictionary<string,string>{{"x","X"},{"y","Y"},{"width","Width"},{"height","Height"},
            {"rotation","Rotation"},{"flipped_h","FlippedH"},{"flipped_v","FlippedV"}};
        foreach(var v in (IEnumerable)plan["nodes"]) {
            var n=Map(v);string id=Text(n["name"]);if(!native.Contains(id))throw new ArgumentException("Graphic not found: "+id);
            object go=native[id];foreach(var field in members)if(n.ContainsKey(field.Key))Set(go,field.Value,n[field.Key]);
            Call(go,"PositionConnectors");updates.Add(n);
        }
        var annotations=new List<object>();
        foreach(var v in (IEnumerable)plan["groups"]) {
            var group=Map(v);var b=new List<double>();foreach(var n in (IEnumerable)group["bounds"])b.Add(Convert.ToDouble(n));
            string id=Text(group["id"]);
            foreach(string kind in new[]{"frame","title"}) {
                string type=kind=="frame"?"DWSIM.Drawing.SkiaSharp.GraphicObjects.Shapes.RectangleGraphic":"DWSIM.Drawing.SkiaSharp.GraphicObjects.HTMLTextGraphic";
                object go=Activator.CreateInstance(Assembly.LoadFrom(Path.Combine(Home,"DWSIM.Drawing.SkiaSharp.dll")).GetType(type));
                Set(go,"Name",LayoutPrefix+id+"-"+kind);Set(go,"Tag","");Set(go,"X",b[0]);Set(go,"Y",b[1]);Set(go,"Width",(int)Math.Ceiling(b[2]-b[0]));Set(go,"Height",(int)Math.Ceiling(b[3]-b[1]));
                Set(go,"DrawLabel",false);Font(go);
                if(kind=="frame") {Set(go,"Text","");Set(go,"Fill",false);Set(go,"GradientMode",false);Set(go,"RoundEdges",false);Set(go,"LineColor",SKColor.Parse("#88a6b8"));Set(go,"LineWidth",1);}
                else {Set(go,"X",b[0]+20);Set(go,"Y",b[1]+12);
                    // HTML text is a native persistent DWSIM graphic. Its CSS font survives
                    // reopening; TextGraphic's transient Typeface is reset by GraphicsSurface.
                    Set(go,"Text","<div style=\"font-family: 'Microsoft YaHei', SimSun, sans-serif; font-size: 18px; color: #26465a;\">"+
                        System.Security.SecurityElement.Escape(Text(group["title"]))+"</div>");
                    Set(go,"Size",18.0);Set(go,"Color",SKColor.Parse("#26465a"));Set(go,"Height",40);
                    Set(go,"Width",Math.Max(100,Math.Min((int)Math.Ceiling(b[2]-b[0]-40),Text(group["title"]).Length*20+40)));}
                var element=new XElement("GraphicObject");foreach(var el in (IEnumerable)Call(go,"SaveData"))element.Add(el);
                annotations.Add(element.ToString(SaveOptions.DisableFormatting));
            }
        }
        return Dict("graphic_updates",updates,"annotations",annotations,"metadata",plan);
    }
}
