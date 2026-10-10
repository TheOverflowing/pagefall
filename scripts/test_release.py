#!/usr/bin/env python3
"""Exercise a relocated frozen app without development tools or user data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import time
from urllib.request import ProxyHandler, build_opener

parser = argparse.ArgumentParser()
parser.add_argument('app', type=Path)
parser.add_argument('fixture', type=Path)
args = parser.parse_args()
app = args.app.resolve()
fixture = args.fixture.resolve()

def digest_files(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}

with tempfile.TemporaryDirectory(prefix='pagefall-installed-') as folder:
    temporary = Path(folder)
    relocated = temporary / 'Applications with spaces' / '拾页.app'
    shutil.copytree(app, relocated, symlinks=True)
    resources = relocated / 'Contents/Resources'
    executable = resources / 'backend/pagefall-backend'
    before = digest_files(relocated)
    env = {k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','NOVELIST','PLAYWRIGHT'))}
    env.update(HOME=str(temporary/'home'), PATH='/usr/bin:/bin:/usr/sbin:/sbin', NOVELIST_DATA_ROOT=str(temporary/'reading-data'))
    Path(env['HOME']).mkdir()
    smoke = subprocess.run([str(executable), 'smoke', str(fixture)], cwd=temporary, env=env,
                           capture_output=True, text=True, timeout=120, check=True)
    checks = json.loads(smoke.stdout)
    origin = 'chrome-extension://ebiomijpkjmejjdbpokfcjmdjlopedkk/'
    payload = json.dumps({'action':'ping'}).encode()
    framed = struct.pack('<I',len(payload)) + payload
    ping = subprocess.run([str(executable),'native-host',origin], input=framed,
                          env=env,cwd=temporary,capture_output=True,timeout=20,check=True)
    length = struct.unpack('<I',ping.stdout[:4])[0]
    assert json.loads(ping.stdout[4:4+length])['ok'] is True
    checks['chrome_protocol'] = 'ok'
    subprocess.run([str(executable),'install-chrome-bridge'],env=env,cwd=temporary,
                   capture_output=True,timeout=30,check=True)
    manifest_path = Path(env['HOME'])/'Library/Application Support/Google/Chrome/NativeMessagingHosts/local.novelist.connect.json'
    manifest = json.loads(manifest_path.read_text())
    assert manifest['allowed_origins'] == [origin]
    wrapper = subprocess.run([manifest['path'],origin],input=framed,env=env,cwd=temporary,
                             capture_output=True,timeout=20,check=True)
    assert json.loads(wrapper.stdout[4:])['ok'] is True
    checks['chrome_install'] = 'ok'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port = listener.getsockname()[1]
    env['NOVELIST_PORT'] = str(port)
    log = temporary/'backend.log'
    with log.open('wb') as output:
        process = subprocess.Popen([str(executable),'serve'],env=env,cwd=temporary,stdout=output,stderr=output)
        try:
            opener = build_opener(ProxyHandler({}))
            deadline = time.monotonic()+30
            while True:
                try:
                    with opener.open(f'http://127.0.0.1:{port}/api/library',timeout=1) as response:
                        library = json.load(response)
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic()>deadline:
                        raise RuntimeError('Packaged backend did not start: '+log.read_text())
                    time.sleep(0.2)
            assert library['books'] == []
            with opener.open(f'http://127.0.0.1:{port}/api/platforms') as response:
                assert len(json.load(response)['platforms']) >= 3
            checks['http_service'] = 'ok'
            assert (Path(env['NOVELIST_DATA_ROOT'])/'.local/books').is_dir()
            assert (Path(env['NOVELIST_DATA_ROOT'])/'exports').is_dir()
        finally:
            process.terminate()
            process.wait(timeout=15)
    assert digest_files(relocated) == before, 'App bundle was modified at runtime'
    checks['read_only_bundle'] = 'ok'
    checks['developer_tools_required'] = False
    print(json.dumps(checks,ensure_ascii=False,indent=2))
