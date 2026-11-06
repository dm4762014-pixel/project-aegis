"""Prepare an isolated, small Ollama instance without changing user-wide settings.

Only a process recorded by this helper (PID + creation time) is reused. An
occupied unknown port is an error, never a reason to terminate another service.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

import httpx
import psutil

ROOT = Path(__file__).resolve().parent
PORT = 11435
MODEL = 'smollm2:135m-instruct-q4_K_M'
INSTANCE = ROOT / 'runtime' / 'ollama-instance.json'


def owned_instance():
    try:
        record = json.loads(INSTANCE.read_text(encoding='utf-8'))
        process = psutil.Process(record['pid'])
        command = process.cmdline()
        return ('ollama' in process.name().lower() and 'serve' in command
                and abs(process.create_time() - record['create_time']) < .01
                and record['port'] == PORT
                and record['model_dir'] == str(ROOT / 'runtime' / 'aegis-models'))
    except (OSError, ValueError, KeyError, psutil.Error):
        return False


def port_in_use():
    with socket.socket() as probe:
        probe.settimeout(.5)
        return probe.connect_ex(('127.0.0.1', PORT)) == 0


def prepare():
    runtime = ROOT / 'runtime'
    runtime.mkdir(exist_ok=True)
    models = runtime / 'aegis-models'
    models.mkdir(exist_ok=True)
    executable = shutil.which('ollama')
    if not executable:
        raise RuntimeError('Install Ollama for Windows first.')
    if port_in_use() and not owned_instance():
        raise RuntimeError('Port 11435 belongs to an unknown service. It was left unchanged.')
    if not port_in_use():
        env = os.environ.copy()
        env.update(OLLAMA_HOST=f'127.0.0.1:{PORT}', OLLAMA_MODELS=str(models),
                   OLLAMA_NUM_PARALLEL='1', OLLAMA_MAX_LOADED_MODELS='1',
                   OLLAMA_CONTEXT_LENGTH='512', OLLAMA_KEEP_ALIVE='0',
                   OLLAMA_MAX_QUEUE='2', OLLAMA_DEBUG='false', OLLAMA_NO_CLOUD='1')
        with (runtime / 'ollama.stdout.log').open('a', encoding='utf-8') as out, (runtime / 'ollama.stderr.log').open('a', encoding='utf-8') as err:
            process = subprocess.Popen([executable, 'serve'], cwd=ROOT, env=env,
                stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        identity = psutil.Process(process.pid)
        INSTANCE.write_text(json.dumps(dict(pid=process.pid, create_time=identity.create_time(),
                                            port=PORT, model_dir=str(models))), encoding='utf-8')
    with httpx.Client(base_url=f'http://127.0.0.1:{PORT}', timeout=2, trust_env=False) as client:
        for _ in range(120):
            try:
                response = client.get('/api/tags')
                response.raise_for_status()
                installed = response.json()['models']
                break
            except (httpx.HTTPError, ValueError, KeyError):
                time.sleep(.5)
        else:
            raise RuntimeError('The isolated Ollama server did not become ready within 60 seconds.')
        if MODEL not in {m['name'] for m in installed}:
            print('Downloading the 105 MB small model into runtime/aegis-models...', flush=True)
            started = time.monotonic()
            last_print = 0
            with client.stream('POST', '/api/pull', json={'model': MODEL, 'stream': True}, timeout=120) as response:
                response.raise_for_status()
                succeeded = False
                for line in response.iter_lines():
                    data = json.loads(line)
                    if 'error' in data:
                        raise RuntimeError('The model download failed; inspect the local Ollama log.')
                    if time.monotonic() - started > 900:
                        raise RuntimeError('The download exceeded 15 minutes; no inference was started.')
                    if time.monotonic() - last_print > 10:
                        print(data.get('status', 'downloading'), data.get('completed', ''), flush=True)
                        last_print = time.monotonic()
                    succeeded = data.get('status') == 'success'
                if not succeeded:
                    raise RuntimeError('The model download did not report success.')
        response = client.get('/api/tags')
        response.raise_for_status()
        if MODEL not in {m['name'] for m in response.json()['models']}:
            raise RuntimeError('The configured small model was not found after setup.')
    print(f'Ready: isolated local model {MODEL} on port {PORT}. No other applications were stopped.', flush=True)


if __name__ == '__main__':
    try:
        prepare()
    except (RuntimeError, httpx.HTTPError, OSError) as error:
        raise SystemExit(str(error))
