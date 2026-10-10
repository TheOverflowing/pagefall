#!/usr/bin/env python3
"""Install the user-owned Chrome Native Messaging host only when explicitly run."""

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import uuid
import zipfile


ROOT = Path(os.environ.get("NOVELIST_RESOURCE_ROOT", Path(__file__).resolve().parent.parent))
HOST_NAME = "local.novelist.connect"


def extension_id(manifest):
    public_key = base64.b64decode(manifest["key"], validate=True)
    digest = hashlib.sha256(public_key).hexdigest()[:32]
    return "".join(chr(ord("a") + int(nibble, 16)) for nibble in digest)


def atomic_write(path, data, mode=0o600):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def host_archive():
    # The host needs only standard-library code. It never opens the project's
    # profile or Keychain: it hands the validated payload to the running app.
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("__main__.py", "from novelist.native_host import main\nraise SystemExit(main())\n")
        archive.writestr("novelist/__init__.py", "")
        for filename in ("native_host.py", "session.py", "runtime.py"):
            archive.writestr("novelist/" + filename, (ROOT / "novelist" / filename).read_bytes())
    return payload.getvalue()


def main():
    if sys.platform != "darwin":
        raise SystemExit("此安装脚本目前仅支持 macOS 上的 Google Chrome。")
    standalone = bool(getattr(sys, "frozen", False))
    if not standalone and not (ROOT / ".venv" / "bin" / "python").is_file():
        raise SystemExit("未找到拾页 Python 环境，请先完成拾页安装。")
    python = Path(sys.executable if standalone else getattr(sys, "_base_executable", sys.executable)).resolve()
    if not python.is_file() or (not standalone and python.is_relative_to(Path.home() / "Downloads")):
        raise SystemExit("连接器需要安装在下载文件夹之外的 Python 运行环境。")
    if not (ROOT / ".build" / "session-store").is_file():
        raise SystemExit("未找到拾页钥匙串组件，请先构建拾页应用。")
    manifest = json.loads((ROOT / "chrome-extension" / "manifest.json").read_text(encoding="utf-8"))
    identifier = extension_id(manifest)
    # Chrome's subprocess must not need permission to read Downloads, Desktop,
    # or Documents. Package the tiny host into its own Application Support path.
    runtime = Path.home() / "Library/Application Support/Novelist/ChromeBridge"
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime.chmod(0o700)
    wrapper = runtime / "chrome-native-host"
    if standalone:
        wrapper_text = "#!/bin/sh\nexec " + shlex.quote(str(python)) + " native-host \"$@\"\n"
        extension = runtime / "extension"
        extension.mkdir(exist_ok=True, mode=0o700)
        for file in (ROOT / "chrome-extension").iterdir():
            if file.is_file(): shutil.copyfile(file, extension / file.name)
    else:
        archive_path = runtime / "novelist-native-host.pyz"
        atomic_write(archive_path, host_archive())
        wrapper_text = "#!/bin/sh\nexec " + shlex.quote(str(python)) + " -I -S " + shlex.quote(str(archive_path)) + " \"$@\"\n"
    atomic_write(wrapper, wrapper_text.encode(), 0o700)
    destination = Path.home() / "Library/Application Support/Google/Chrome/NativeMessagingHosts" / (HOST_NAME + ".json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    native_manifest = {
        "name": HOST_NAME,
        "description": "拾页晋江登录连接器",
        "path": str(wrapper),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{identifier}/"],
    }
    atomic_write(destination, (json.dumps(native_manifest, ensure_ascii=False, indent=2) + "\n").encode())
    print("已安装拾页 Chrome 本机连接器。")
    print("扩展文件夹：" + str(extension if standalone else ROOT / "chrome-extension"))
    print("固定扩展 ID：" + identifier)


if __name__ == "__main__":
    main()
