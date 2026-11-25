"""Check the generated launcher with a real MCP subprocess and temporary storage."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import tempfile
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def validate_skill_tree(plugin_root: Path, expected: str, expected_set: str):
    from chemellm_codex import skill_fingerprint, skill_inventory
    skills = plugin_root/'skills'
    if not (skills/'chemellm-engineering/SKILL.md').is_file():
        raise ValueError('技能目录不存在：'+str(plugin_root))
    if not expected or skill_fingerprint(skills/'chemellm-engineering') != expected:
        raise ValueError('公共技能指纹未配置或不匹配：'+str(plugin_root))
    if not expected_set or skill_fingerprint(skills) != expected_set:
        source = {r['name']:r['fingerprint'] for r in skill_inventory(ROOT/'codex-plugin/skills')}
        actual = {r['name']:r['fingerprint'] for r in skill_inventory(skills)}
        changed = sorted(name for name in source.keys() | actual.keys() if source.get(name) != actual.get(name))
        raise ValueError('整套技能指纹未配置或不匹配：'+str(plugin_root)+'；差异技能：'+', '.join(changed))


def validate_configuration(config_path, plugin_root=None, source_only=False):
    configured = json.loads(config_path.read_text(encoding='utf-8'))['mcpServers']
    config = configured['chemellm']
    def flag(name):
        args = config['args']
        return next((args[i+1] for i in range(len(args)-1) if args[i] == name), '')
    expected, expected_set = flag('--skill-fingerprint'), flag('--skill-set-fingerprint')
    validate_skill_tree(ROOT/'codex-plugin', expected, expected_set)
    if not source_only:
        if plugin_root is None:
            version = json.loads((ROOT/'codex-plugin/.codex-plugin/plugin.json').read_text(encoding='utf-8'))['version']
            codex_root = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))
            candidates = list((codex_root/'plugins/cache').glob('*/chemellm/'+version))
            if len(candidates) != 1:
                raise ValueError('无法唯一定位实际插件缓存；请用 --plugin-root 指定，或安装前用 --source-only')
            plugin_root = candidates[0]
        validate_skill_tree(plugin_root, expected, expected_set)
        cached = json.loads((plugin_root/'.mcp.json').read_text(encoding='utf-8'))['mcpServers']
        if cached != configured:
            raise ValueError('已安装缓存的启动配置与待检查配置不同：'+str(plugin_root))
    return config


async def check(config_path: Path, plugin_root: Path | None = None, source_only=False) -> None:
    config = validate_configuration(config_path, plugin_root, source_only)
    with tempfile.TemporaryDirectory(prefix="chemellm-install-check-") as temporary:
        parameters = StdioServerParameters(
            command=config["command"], args=[*config["args"], "--data-root", temporary],
            cwd=temporary,
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                response = await session.call_tool("chem_health", {})
                if response.isError:
                    raise RuntimeError(str(response.content))
                health = response.structuredContent or json.loads(response.content[0].text)
                assert Path(health["dataDir"]).resolve() == Path(temporary).resolve(), health
                assert health["phaseGate"] == "enforce", health
                assert health['workflowVersion'] == 2, health
                assert health['skillPairing'] == 'matched', health
                assert health['skillSetPairing'] == 'matched', health
                assert len(health['skills']) == 5, health['skills']
                plugin = json.loads((ROOT/'codex-plugin/.codex-plugin/plugin.json').read_text(encoding='utf-8'))
                assert health['version'] == plugin['version'], health
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {"chem_execute", "chem_project", "chem_review", "chem_bundle", "chem_dwsim_study", "chem_dwsim_publish",
                        'chem_project_init','chem_artifacts_register','chem_records'} <= names
                model_schema=next(tool for tool in tools.tools if tool.name=='chem_dwsim').inputSchema
                assert 'check' in str(model_schema), 'DWSIM preflight action missing'
                print(f"MCP startup OK; {len(names)} tools; isolated storage, workflow and skill/kernel pairing OK.")

    if not source_only:
        expected = {
            'chemellm-blender': {'get_objects_summary', 'execute_blender_code'},
            'chemellm-freecad': {'execute_python', 'get_output_page'},
            'chemellm-vega': {'show_vegalite_chart'},
            'chemellm-qet': {'qet_project_info', 'qet_check'},
            'chemellm-dwsim': {'dwsim_engine_status', 'dwsim_create'},
        }
        entries = json.loads(config_path.read_text(encoding='utf-8'))['mcpServers']
        for name in expected.keys() & entries.keys():
            entry = entries[name]
            params = StdioServerParameters(command=entry['command'], args=entry.get('args', []),
                cwd=entry.get('cwd'), env=entry.get('env', {}))

            async def discover():
                async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
                    await session.initialize()
                    return {tool.name for tool in (await session.list_tools()).tools}

            names = await asyncio.wait_for(discover(), timeout=60)
            # An explicitly supplied native DWSIM MCP has its own tool catalog.
            native_dwsim = name == 'chemellm-dwsim' and 'chemellm_codex.dwsim_mcp' not in entry.get('args', [])
            if not names or (not native_dwsim and not expected[name] <= names):
                raise RuntimeError(f'Optional MCP {name} returned an unexpected tool catalog.')
            print(f"Optional MCP {name}: {len(names)} tools discovered.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "codex-plugin" / ".mcp.json")
    parser.add_argument('--plugin-root', type=Path, help='Actual installed plugin cache directory')
    parser.add_argument('--source-only', action='store_true', help='Pre-install check; does not claim installed cache verification')
    options = parser.parse_args()
    asyncio.run(check(options.config, options.plugin_root, options.source_only))


if __name__ == "__main__":
    main()
