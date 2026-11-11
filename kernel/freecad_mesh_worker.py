"""Fixed FreeCADCmd worker. Reads snapshots only; never saves source documents."""
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import FreeCAD as App
import Part


def hidden_names(path):
    hidden = set()
    with zipfile.ZipFile(path) as archive:
        if 'GuiDocument.xml' not in archive.namelist():
            return hidden
        root = ET.fromstring(archive.read('GuiDocument.xml'))
        for provider in root.iter('ViewProvider'):
            for prop in provider.iter('Property'):
                if prop.get('name') == 'Visibility':
                    value = prop.find('Bool')
                    if value is not None and value.get('value') == 'false':
                        hidden.add(provider.get('name'))
    return hidden


def components(path, names):
    if path.suffix.lower() != '.fcstd':
        shape = Part.read(str(path))
        solids = shape.Solids
        return None, [(f'{path.stem}-{i+1}', s) for i, s in enumerate(solids)] if solids else [(path.stem, shape)]
    # Saved Shape snapshots are used as-is. No recompute or save operation.
    doc = App.openDocument(str(path))
    candidates = {o.Name: o for o in doc.Objects if hasattr(o, 'Shape') and not o.Shape.isNull()}
    if names:
        missing = set(names)-candidates.keys()
        if missing:
            raise ValueError('FCStd 对象不存在或无静态几何：'+', '.join(sorted(missing)))
        chosen = [candidates[n] for n in names]
    else:
        hidden = hidden_names(path)
        chosen = [o for o in candidates.values() if o.Name not in hidden
                  and not any(p.Name in candidates for p in o.InList)]
    result = []
    for obj in chosen:
        shape = obj.Shape.copy()
        # Shape includes the object's local Placement. Add only its ancestors.
        if hasattr(obj, 'getGlobalPlacement'):
            shape.Placement = obj.getGlobalPlacement().multiply(obj.Placement.inverse()).multiply(shape.Placement)
        result.append((obj.Label or obj.Name, shape))
    return doc, result


def main():
    request = json.loads(Path(os.environ['CHEMELLM_CAD_REQUEST']).read_text(encoding='utf-8'))
    meshes = {}
    for job in request['jobs']:
        doc, parts = components(Path(job['path']), job['object_names'])
        try:
            mesh_parts = []
            for name, shape in parts:
                vertices, faces = shape.tessellate(request['deflection_mm'])
                if vertices and faces:
                    mesh_parts.append({'name': name, 'vertices': [[v.x, v.y, v.z] for v in vertices],
                                       'faces': [list(f) for f in faces]})
            if not mesh_parts:
                raise ValueError('CAD 无可导入的静态面几何：'+job['id'])
            meshes[job['id']] = mesh_parts
        finally:
            if doc:
                App.closeDocument(doc.Name)
    Path(request['output']).write_text(json.dumps({'units': 'mm', 'assets': meshes}), encoding='utf-8')


main()
