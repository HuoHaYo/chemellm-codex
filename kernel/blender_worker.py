"""Fixed Blender worker; uses Blender's bundled Python and no host packages."""
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view


COLORS = {'column': (.56, .64, .70), 'vessel': (.62, .70, .74), 'tank': (.80, .84, .86),
          'horizontal_vessel': (.60, .69, .73), 'exchanger': (.45, .59, .64),
          'pump': (.12, .36, .48), 'box': (.38, .52, .59), 'pipe': (.74, .47, .19),
          'pipe_rack': (.30, .38, .43), 'building': (.57, .66, .70),
          'road': (.12, .16, .19), 'fence': (.42, .49, .53), 'ground': (.25, .34, .29),
          'cad': (.45, .66, .68)}


def material(name, color, metallic=0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = (*color, 1)
    bsdf.inputs['Metallic'].default_value = metallic
    bsdf.inputs['Roughness'].default_value = .38 if metallic else .65
    return mat


def box(name, size, center, mat, parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    obj.parent = parent
    return obj


def cylinder(name, radius, depth, center, mat, parent=None):
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=radius, depth=depth, location=center)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(mat)
    for face in obj.data.polygons:
        face.use_smooth = abs(face.normal.z) < .5
    obj.parent = parent
    return obj


def beam(name, a, b, radius, mat, parent):
    a, b = Vector(a), Vector(b)
    obj = cylinder(name, radius, (b-a).length, (a+b)/2, mat, parent)
    obj.rotation_euler = (b-a).to_track_quat('Z', 'Y').to_euler()
    return obj


def ellipsoid(name, center, size, mat, parent):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, location=center)
    obj = bpy.context.object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    for face in obj.data.polygons:
        face.use_smooth = True
    obj.parent = parent
    return obj


def build_item(item, scale, meshes, collection):
    name, kind = item['id'], item['kind']
    root = bpy.data.objects.new(name, None)
    collection.objects.link(root)
    root.location = [v*scale for v in item['position']]
    root.rotation_euler = [math.radians(v) for v in item['rotation_deg']]
    root['chemellm_id'] = name
    root['kind'] = kind
    root['source'] = item['source']
    root['reference'] = json.dumps(item['reference'], ensure_ascii=False)
    root['presentation_only'] = True
    mat = material(name+' material', item['color'] or COLORS[kind], .45 if kind in {
        'column', 'vessel', 'tank', 'horizontal_vessel', 'exchanger', 'pipe', 'cad'} else 0)
    L, W, H, D = [(item.get(k) or 0)*scale for k in ('length', 'width', 'height', 'diameter')]
    before = set(bpy.data.objects)
    if kind in {'column', 'vessel', 'tank'}:
        if kind == 'tank':
            cylinder(name+' shell', D/2, H, (0, 0, H/2), mat, root)
        else:
            cap = min(D/4, H/4)
            cylinder(name+' shell', D/2, H-2*cap, (0, 0, H/2), mat, root)
            ellipsoid(name+' top', (0, 0, H-cap), (D/2, D/2, cap), mat, root)
            ellipsoid(name+' bottom', (0, 0, cap), (D/2, D/2, cap), mat, root)
        if kind == 'column':
            for z in (.25, .55, .85):
                bpy.ops.mesh.primitive_torus_add(major_radius=D*.54, minor_radius=D*.025,
                                               location=(0, 0, H*z))
                platform = bpy.context.object
                platform.name = name+' platform'
                platform.data.materials.append(mat)
                platform.parent = root
    elif kind in {'horizontal_vessel', 'exchanger'}:
        cap = min(D/4, L/4)
        obj = cylinder(name+' shell', D/2, L-2*cap, (0, 0, D*.8), mat, root)
        obj.rotation_euler.y = math.pi/2
        for x in (-L/2+cap, L/2-cap):
            ellipsoid(name+' head', (x, 0, D*.8), (cap, D/2, D/2), mat, root)
        for x in (-L*.3, L*.3):
            box(name+' saddle', (D*.24, D*.8, D*.4), (x, 0, D*.2), mat, root)
    elif kind == 'pump':
        box(name+' base', (L, W, H*.2), (0, 0, H*.1), mat, root)
        body = cylinder(name+' motor', min(W/2, H*.4), L*.75, (0, 0, H*.6), mat, root)
        body.rotation_euler.y = math.pi/2
    elif kind in {'box', 'building', 'ground'}:
        height = H or .08
        box(name, (L, W, height), (0, 0, height/2 if kind != 'ground' else -height/2), mat, root)
        if kind == 'building':
            box(name+' roof', (L*1.03, W*1.03, max(H*.035, .05)), (0, 0, H), mat, root)
    elif kind == 'pipe_rack':
        post = max(min(W, H)*.035, .08)
        for i in range(item['bays']+1):
            x = -L/2+L*i/item['bays']
            for y in (-W/2, W/2):
                box(name+' post', (post, post, H), (x, y, H/2), mat, root)
            box(name+' crossbeam', (post, W, post), (x, 0, H), mat, root)
        for y in (-W/2, W/2):
            box(name+' beam', (L, post, post), (0, y, H), mat, root)
    elif kind in {'pipe', 'road', 'fence'}:
        points = [Vector([v*scale for v in p]) for p in item['points']]
        for i, (a, b) in enumerate(zip(points, points[1:])):
            if kind == 'pipe':
                beam(f'{name}-{i}', a, b, D/2, mat, root)
            else:
                height = H if kind == 'fence' else .06
                obj = box(f'{name}-{i}', ((b-a).length, W if kind == 'road' else .08, height),
                          (a+b)/2+Vector((0, 0, height/2)), mat, root)
                obj.rotation_euler = (b-a).to_track_quat('X', 'Z').to_euler()
    elif kind == 'cad':
        for part in meshes[name]:
            mesh = bpy.data.meshes.new(name+' '+part['name'])
            mesh.from_pydata([[v*.001 for v in p] for p in part['vertices']], [], part['faces'])
            mesh.update()
            obj = bpy.data.objects.new(name+' '+part['name'], mesh)
            collection.objects.link(obj)
            obj.data.materials.append(mat)
            obj.parent = root
    for obj in set(bpy.data.objects)-before:
        if obj.type == 'MESH':
            obj['chemellm_id'] = name
            obj['source'] = item['source']
            obj['reference'] = root['reference']
    return root


def corners(objects):
    bpy.context.view_layer.update()
    return [o.matrix_world @ Vector(c) for o in objects if o.type == 'MESH' for c in o.bound_box]


def aim(obj, target):
    obj.rotation_euler = (Vector(target)-obj.location).to_track_quat('-Z', 'Y').to_euler()


def setup_camera(scene, points, camera_input, scale):
    low = Vector([min(p[i] for p in points) for i in range(3)])
    high = Vector([max(p[i] for p in points) for i in range(3)])
    center = (low+high)/2
    span = max((high-low).length, 1)
    data = bpy.data.cameras.new('Presentation camera')
    camera = bpy.data.objects.new('Presentation camera', data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    data.clip_start = .01
    data.clip_end = max(span*100, 1000)
    if camera_input:
        camera.location = Vector([x*scale for x in camera_input['position']])
        aim(camera, [x*scale for x in camera_input['target']])
        data.lens = camera_input['lens_mm']
    else:
        data.type = 'ORTHO'
        camera.location = center+Vector((1.25, -1.6, 1.25)).normalized()*span*2
        aim(camera, center)
        inverse = camera.rotation_euler.to_matrix().transposed()
        projected = [inverse @ (p-center) for p in points]
        width = max(p.x for p in projected)-min(p.x for p in projected)
        height = max(p.y for p in projected)-min(p.y for p in projected)
        aspect = scene.render.resolution_x/scene.render.resolution_y
        # ortho_scale spans the image width on a landscape render.
        data.ortho_scale = max(width, height*aspect)*1.20
    return camera, center, span


def choose_font(items, config):
    labels = ''.join(i['label'] or i['id'] for i in items)
    if config.get('font_path'):
        return bpy.data.fonts.load(config['font_path'])
    if any(ord(c) > 127 for c in labels):
        candidates = [Path('C:/Windows/Fonts/msyh.ttc'), Path('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'),
                      Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')]
        for path in candidates:
            if path.is_file():
                return bpy.data.fonts.load(str(path))
        raise RuntimeError('中文标注需要可用字体，请提供 render.font_path')
    return None


def add_labels(items, roots, camera, span, font):
    mat = material('Labels', (.025, .05, .07))
    nodes = mat.node_tree.nodes
    emission = nodes.new('ShaderNodeEmission')
    emission.inputs['Color'].default_value = (.015, .028, .04, 1)
    mat.node_tree.links.new(emission.outputs[0], nodes.get('Material Output').inputs['Surface'])
    for item, root in zip(items, roots):
        if item['kind'] in {'ground', 'fence', 'road', 'pipe', 'pipe_rack'} and not item['label']:
            continue
        points = corners(root.children_recursive)
        if not points:
            continue
        text = bpy.data.curves.new(item['id']+' label', 'FONT')
        text.body = item['label'] or item['id']
        text.align_x = 'CENTER'
        text.size = span*.017
        if font:
            text.font = font
        obj = bpy.data.objects.new(item['id']+' label', text)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = Vector([sum(p.x for p in points)/len(points), sum(p.y for p in points)/len(points),
                               max(p.z for p in points)+span*.014])
        obj.rotation_euler = camera.rotation_euler
        text.materials.append(mat)
        obj['chemellm_id'] = item['id']
        obj['is_label'] = True
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.convert(target='MESH')


def main():
    input_path = Path(sys.argv[sys.argv.index('--')+1]).resolve()
    folder = input_path.parent
    request = json.loads(input_path.read_text(encoding='utf-8'))
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scale = .001 if request['units'] == 'mm' else 1
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 1
    scene['title'] = request['title']
    scene['project_id'] = request['project_id']
    scene['presentation_only'] = True
    collection = bpy.data.collections.new('Plant')
    scene.collection.children.link(collection)
    meshes = json.loads((folder/'cad-mesh.json').read_text())['assets'] if (folder/'cad-mesh.json').is_file() else {}
    roots = [build_item(item, scale, meshes, collection) for item in request['items']]
    for engine in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    else:
        raise RuntimeError('当前 Blender 没有可用 Eevee 渲染引擎')
    scene.render.resolution_x = request['render']['width']
    scene.render.resolution_y = request['render']['height']
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = 'Standard'
    if hasattr(scene, 'eevee'):
        for key, value in {'use_gtao': True, 'gtao_distance': 3, 'gtao_factor': 1.25}.items():
            if hasattr(scene.eevee, key):
                setattr(scene.eevee, key, value)
    geometry = [o for r in roots for o in r.children_recursive if o.type == 'MESH']
    cad_sources = {entry['id']: entry for entry in request.get('cad_sources', [])}
    for item, root in zip(request['items'], roots):
        if item['id'] in cad_sources:
            source = cad_sources[item['id']]
            for obj in [root, *root.children_recursive]:
                obj['source_sha256'] = source['sha256']
                obj['source_snapshot'] = source['snapshot']
    points = corners(geometry)
    if not points:
        raise RuntimeError('场景没有可显示的几何')
    camera, center, span = setup_camera(scene, points, request['render']['camera'], scale)
    if not any(i['kind'] == 'ground' for i in request['items']):
        box('Presentation ground', (span*1.5, span*1.5, .05),
            (center.x, center.y, min(p.z for p in points)-.03), material('Ground', (.28, .35, .31)))
    if request['render']['labels']:
        add_labels(request['items'], roots, camera, span, choose_font(request['items'], request['render']))
        # Include text in the automatic camera fit, keeping the same direction.
        if not request['render']['camera']:
            all_points = corners([o for o in scene.objects if o.type == 'MESH' and o.name != 'Presentation ground'])
            inverse = camera.rotation_euler.to_matrix().transposed()
            pp = [inverse @ (p-camera.location) for p in all_points]
            aspect = scene.render.resolution_x/scene.render.resolution_y
            camera.data.ortho_scale = max(camera.data.ortho_scale,
                2*max(max(abs(p.x), abs(p.y)*aspect) for p in pp)*1.12)
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs[0].default_value = (.45, .52, .59, 1)
    scene.world.node_tree.nodes['Background'].inputs[1].default_value = .45
    sun_data = bpy.data.lights.new('Sun', 'SUN')
    sun_data.energy = 2
    sun = bpy.data.objects.new('Sun', sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(28), math.radians(-20), math.radians(-30))
    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = str(folder/'preview.png')
    bpy.context.view_layer.update()
    # Set a useful viewport for opening the native file interactively.
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.region_3d.view_distance = span*1.3
                area.spaces.active.region_3d.view_location = center
                area.spaces.active.region_3d.view_rotation = camera.rotation_euler.to_quaternion()
    bpy.ops.wm.save_as_mainfile(filepath=str(folder/'scene.blend'), compress=False)
    bpy.ops.export_scene.gltf(filepath=str(folder/'scene.glb'), export_format='GLB', export_extras=True)
    bpy.ops.render.render(write_still=True)
    bpy.context.view_layer.update()
    report_items = []
    for item, root in zip(request['items'], roots):
        ps = corners([o for o in root.children_recursive if o.type == 'MESH'])
        report_items.append({'id': item['id'], 'kind': item['kind'], 'reference': item['reference'],
                             'bounds_m': [[min(p[i] for p in ps) for i in range(3)],
                                          [max(p[i] for p in ps) for i in range(3)]]})
    projected = [world_to_camera_view(scene, camera, p) for p in points]
    report = {'engine': 'Blender', 'version': bpy.app.version_string, 'render_engine': scene.render.engine,
              'item_ids': [i['id'] for i in request['items']], 'items': report_items,
              'units': 'm', 'input_units': request['units'], 'presentation_only': True,
              'camera_fits_geometry': all(0 <= p.x <= 1 and 0 <= p.y <= 1 and p.z > 0 for p in projected)}
    (folder/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
