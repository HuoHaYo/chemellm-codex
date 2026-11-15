"""Select final deliverables without exporting the project's internal archive."""
from __future__ import annotations

from pathlib import PurePosixPath

from .efficiency import relative_path

NATIVE_KINDS = {'blend', 'fcstd', 'step', 'stp', 'dwxml', 'dwxmz', 'qet'}
RESOURCE_KINDS = {'js', 'mjs', 'css', 'bin', 'woff', 'woff2', 'ttf', 'mtl'}
PROCESS_NAMES = {
    'project.json', 'requirements.json', 'plan.json', 'calculations.json', 'references.json',
    'source-groups.json', 'manifest.json', 'report.json', 'failure.json', 'citation.json',
    'scene-input.json', 'cad-mesh.json', 'runner-request.json', 'runner-result.json',
    'recompute.md', 'reproduce.md', 'blender_worker.py', 'freecad_mesh_worker.py',
    'dwsim输入.json', 'dwsim结果.json', 'dwsim布局输入.json',
    'qet-input.json', 'qet-check.json',
}


def file_role(file, project=None):
    """Explicit roles win; old files use provenance and known process identities."""
    if file.get('delivery_role') in {'deliverable', 'resource', 'internal'}:
        return file['delivery_role']
    path = PurePosixPath(str(file.get('relative_path') or file.get('scene_path') or file.get('name', '')).replace('\\', '/'))
    name = path.name.lower()
    kind = str(file.get('kind') or path.suffix.lstrip('.')).lower()
    if file.get('origin') in {'reference', 'intake'}:
        return 'internal'
    if project and any(c.get('report_artifact_id') == file.get('artifactId') for c in project.get('external_checks', [])):
        return 'internal'
    if (name in PROCESS_NAMES or kind in {'log', 'bak', 'tmp', 'pyc', 'py', 'ps1', 'sh', 'cmd', 'bat'} or name.endswith('.blend1')
            or any(p.lower() in {'work', 'diagnostics', 'logs', 'sources', '__pycache__'} for p in path.parts)
            or name.startswith('原始流程') or (kind.startswith(('blend', 'fcstd')) and kind[-1:].isdigit())):
        return 'internal'
    source = str(file.get('source') or file.get('tool') or file.get('generator') or '').lower()
    if source in {'kernel export_blender', 'export_blender'} and kind not in {'blend', 'glb', 'png'}:
        return 'internal'
    if kind == 'json' and (source.startswith(('kernel ', 'dwsim')) or file.get('tool') in {
            'pid_data', 'equipment_datasheet', 'relief_sizing', 'hazop_lopa', 'compliance_matrix', 'pid_check', 'cad_part'}):
        return 'internal'
    if kind in RESOURCE_KINDS:
        return 'resource'
    return 'deliverable'


def generated_metadata(path, source, relative=None):
    info = {'name': path.name, 'kind': path.suffix.lstrip('.'), 'source': source,
            'relative_path': relative or path.name}
    return {'delivery_role': file_role(info), 'depends_on': [], **({'scene_path': relative} if relative else {})}


def _latest(files):
    """Keep the latest version of a logical output, while preserving its resources."""
    current = {}
    for f in files:
        key = (f.get('tool') or f.get('generator'), f.get('relative_path') or f.get('name'),
               tuple(f.get('deliverable_keys', [])))
        current[key] = f
    return list(current.values())


def select_files(project, artifact_ids=None):
    from .efficiency import selected_files

    files = project.get('files', [])
    by_id = {f['artifactId']: f for f in files if f.get('artifactId')}
    roots = []
    if artifact_ids is not None:
        if not artifact_ids:
            raise ValueError('未选择可交付文件')
        for ident in artifact_ids:
            if ident not in by_id:
                raise ValueError('选择的成果不存在：'+ident)
            f = by_id[ident]
            if file_role(f, project) != 'deliverable':
                raise ValueError('选择的文件不是最终成果：'+f['name'])
            roots.append(f)
    else:
        deliverables = project.get('plan', {}).get('deliverables', [])
        for d in deliverables:
            candidates = selected_files(project, d) if project.get('workflow') == 'efficient' else files
            formats = {s.lower().lstrip('.') for s in d.get('formats', [])}
            for f in candidates if d.get('artifact_ids') else _latest(candidates):
                kind = str(f.get('kind', '')).lower()
                explicit = f.get('artifactId') in d.get('artifact_ids', [])
                if file_role(f, project) != 'deliverable':
                    continue
                if not explicit and ((formats and kind not in formats) or (kind in NATIVE_KINDS and kind not in formats)):
                    continue
                roots.append(f)
        if not deliverables:
            roots = [f for f in _latest(files) if f.get('origin') == 'generated'
                     and file_role(f, project) == 'deliverable' and f.get('kind', '').lower() not in NATIVE_KINDS]
    if not roots:
        raise ValueError('无可交付文件；请指定最终成果或补齐交付项映射')

    # Dependencies are root-relative within the same fixed registration or scene.
    def group(f):
        return f.get('registration_id') or f.get('scene_group') or f.get('artifactId')

    indexes = {}
    for f in files:
        path = f.get('relative_path') or f.get('scene_path') or f.get('name', '')
        indexes[(group(f), relative_path(path).casefold())] = f
    selected, visiting = {}, set()

    def visit(f):
        ident = f['artifactId']
        if ident in visiting:
            raise ValueError('成果依赖存在循环：'+f['name'])
        if ident in selected:
            return
        if len(visiting) >= 512:
            raise ValueError('成果依赖层级过深：'+f['name'])
        if file_role(f, project) == 'internal':
            raise ValueError('内部过程文件不能作为交付依赖：'+f['name'])
        visiting.add(ident)
        dependencies = f.get('depends_on')
        if dependencies is None and f.get('registration_id'):
            # Pre-role registrations used the whole batch for relative assets.
            related = [x for x in _latest([x for x in files if group(x) == group(f)]) if x['artifactId'] != ident
                       and file_role(x, project) != 'internal' and x.get('kind', '').lower() not in NATIVE_KINDS]
        else:
            related = []
            for raw in dependencies or []:
                path = relative_path(raw)
                dependency = indexes.get((group(f), path.casefold()))
                if not dependency:
                    raise ValueError('成果依赖缺失：'+path)
                related.append(dependency)
        # Legacy sibling expansion is not a dependency edge and must not recurse.
        if dependencies is None and f.get('registration_id'):
            for x in related:
                selected[x['artifactId']] = x
        else:
            for x in related:
                visit(x)
        visiting.remove(ident)
        selected[ident] = f

    for f in roots:
        visit(f)
    return list(selected.values())


def instructions(project, manifest):
    lines = ['# 交付说明', '', '项目：'+project['name'], '', '## 成果与打开方式', '']
    titles = {d['key']: d['title'] for d in project.get('plan', {}).get('deliverables', [])}
    openers = {'blend': '用 Blender 打开', 'dwxml': '用 DWSIM 打开', 'dwxmz': '用 DWSIM 打开',
               'fcstd': '用 FreeCAD 打开', 'step': '用支持 STEP 的 CAD 软件打开',
               'stp': '用支持 STEP 的 CAD 软件打开', 'glb': '用支持 GLB 的三维查看器打开',
               'html': '用浏览器打开；需要本地服务时在此目录启动静态文件服务',
               'xlsx': '用 Excel 或兼容软件打开', 'docx': '用 Word 或兼容软件打开',
               'pdf': '用 PDF 阅读器打开', 'md': '用 Markdown 阅读器或文本编辑器打开'}
    for f in manifest['files']:
        if f.get('delivery_role') == 'resource':
            continue
        kind = PurePosixPath(f['archivePath']).suffix.lstrip('.').lower()
        purpose = '、'.join(titles[key] for key in f.get('deliverable_keys', []) if key in titles)
        lines.append('- `'+f['archivePath']+'`：'+(purpose+'；' if purpose else '')+openers.get(kind, '用支持此格式的软件打开'))
    review = project.get('review') or {}
    lines += ['', '## 使用与校核边界', '', '请保留目录结构，资源文件用于打开或运行相应成果。',
              '自动完整性校核：'+('已通过。' if review.get('ready') else '未通过或尚未执行；当前成果不能据此宣称已完成校核。'),
              '专业审核：'+('已记录为 '+str(review['professionalReview'])+'。' if review.get('professionalReview') not in {None, 'not_reviewed'} else '尚未完成；自动校核不代表工程标准符合性。')]
    if project.get('external_checks'):
        lines.append('外部检查报告结论来自提供方，文件登记不代表插件独立验证其结论。')
    return '\n'.join(lines)+'\n'
