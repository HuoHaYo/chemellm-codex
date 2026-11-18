"""Cheap tool discovery. Schema generation is reserved for explicit requests."""
from fastapi import HTTPException

CATEGORIES = ('engineering', 'dwsim', '3d', 'references', 'costing')
REFERENCE_TOOLS = {'literature_search', 'pubchem', 'open_literature_search', 'project_search',
                   'fulltext_import', 'source_fetch', 'document_extract', 'document_read',
                   'claim_save', 'evidence_add', 'evidence_check', 'evidence_export'}
SCENE_TOOLS = {'export_blender', 'export_freecad', 'cad_part', 'drawing_read'}


def categories(name):
    result = []
    if name == 'dwsim':
        result.append('dwsim')
    if name in SCENE_TOOLS:
        result.append('3d')
    if name in REFERENCE_TOOLS or name == 'drawing_read':
        result.append('references')
    if name in {'economics', 'export_xlsx'}:
        result.append('costing')
    return result or ['engineering']


def discover(k, tool=None, view=None, category=None, offset=0, limit=20):
    if view not in {None, 'full', 'summary'} or category not in {None, *CATEGORIES}:
        raise HTTPException(422, '未知 view/category')
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(422, 'offset/limit 无效')
    schemas = {**k.SCHEMAS, 'dwsim': k.DwsimInput, 'export_blender': k.BlenderInput,
               'qelectrotech': k.QElectroTechInput, 'export_chart': k.ChartInput}
    executable = sorted((set(k.SCHEMAS) - {'ask_user', 'acceptance'}) | k.SPECIAL_TOOLS)
    descriptions = {item['id']: item for item in k.TOOLS}
    if tool is not None:
        if tool not in executable:
            raise HTTPException(422, '未知或不可执行的工具：' + tool)
        if category and category not in categories(tool):
            raise HTTPException(422, '工具不属于指定类别')
        if view != 'summary':
            schema = schemas.get(tool)
            return {'tool': tool, 'schema': schema.model_json_schema() if schema else None,
                    'description': descriptions.get(tool), 'specialTool': tool in k.SPECIAL_TOOLS,
                    'note': 'specialTool 无 schema 时请查阅对应 kernel 模块；不要猜测必需参数。'}
    if view == 'summary':
        names = [name for name in executable if (tool is None or name == tool)
                 and (category is None or category in categories(name))]
        selected = names[offset:offset + limit]
        items = [{'id': name, 'name': descriptions.get(name, {}).get('name', name),
                  'description': descriptions.get(name, {}).get('description', name),
                  'categories': categories(name), 'hasSchema': name in schemas} for name in selected]
        return {'items': items, 'total': len(names), 'offset': offset,
                'next_offset': offset + len(items) if offset + len(items) < len(names) else None,
                'categories': list(CATEGORIES)}
    if category:
        raise HTTPException(422, 'category 分页目录请使用 view=summary')
    # Preserve the original unqualified response, including non-executable schemas.
    return {'tools': k.TOOLS, 'schemas': {name: schema.model_json_schema() for name, schema in schemas.items()},
            'executable': executable, 'calcTools': sorted(k.CALC_TOOLS),
            'generateTools': sorted(k.GENERATE_TOOLS), 'evidenceTools': sorted(k.EVIDENCE_TOOLS)}
