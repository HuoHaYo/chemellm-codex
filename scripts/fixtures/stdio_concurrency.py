"""Protocol-only fixture: hold a real unit conversion until a real cancel flag.

No simulator or engineering validation is fabricated. Production exposes no
test hooks. Run by test_codex_mcp.py in a separate isolated subprocess.
"""
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from chemellm_codex.__main__ import configure

configure(ROOT, Path(sys.argv[1]))
with redirect_stdout(sys.stderr):
    from chemellm_codex.server import create_server, kernel

original = kernel.run_tool


def held_conversion(pid, tool, args):
    if tool == 'convert_units' and args.get('value') == 1234567:
        folder = kernel.DWSIM_STUDIES.folder(pid)/('f'*32)
        folder.mkdir(parents=True)
        state = dict(job_id='f'*32, status='running', cases=[])
        (folder/'study.json').write_text(json.dumps(state), encoding='utf-8')
        def background_log():
            for i in range(30):
                print('protocol-fixture background log', i, flush=True)
                time.sleep(.01)
        thread = threading.Thread(target=background_log)
        thread.start()
        print('protocol-fixture request log', flush=True)
        started = time.monotonic()
        while not (folder/'cancel.requested').exists():
            if time.monotonic()-started > 15:
                raise ValueError('protocol fixture cancel did not arrive')
            time.sleep(.02)
        thread.join()
        state['status'] = 'cancelled'
        (folder/'study.json').write_text(json.dumps(state), encoding='utf-8')
    return original(pid, tool, args)


kernel.run_tool = held_conversion
create_server().run(transport='stdio')
