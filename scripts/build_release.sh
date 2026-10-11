#!/bin/bash
set -euo pipefail
novelist_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$novelist_root"
novelist_version="${1:-0.8.0}"
novelist_arch="$(uname -m)"
novelist_stage="$novelist_root/.build/release-$novelist_version-$novelist_arch"
novelist_app="$novelist_stage/拾页.app"
novelist_output="$novelist_root/.build/releases"
novelist_cache="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/Library/Caches/ms-playwright}"
[[ "$novelist_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Use a numeric release version.' >&2; exit 1; }
[[ "$(uname -s)" == Darwin ]] || { echo 'Build on macOS.' >&2; exit 1; }
mkdir -p "$novelist_stage" "$novelist_output"
npm run build:engine
NOVELIST_APP_PATH="$novelist_app" NOVELIST_BUILD_PATH="$novelist_stage/helpers" \
NOVELIST_VERSION="$novelist_version" NOVELIST_BUILD_NUMBER=10 bash scripts/build_app.sh
.venv/bin/python -m PyInstaller --noconfirm --clean \
  --distpath "$novelist_stage/runtime" --workpath "$novelist_stage/pyinstaller" packaging/backend.spec
novelist_resources="$novelist_app/Contents/Resources"
# Copy only runtime assets; never include the development book library or sessions.
ditto "$novelist_stage/runtime/backend" "$novelist_resources/backend"
mkdir -p "$novelist_resources/.build" "$novelist_resources/engine/dist" "$novelist_resources/browsers"
for novelist_helper in vision-ocr session-store api-key-store; do
  cp "$novelist_stage/helpers/$novelist_helper" "$novelist_resources/.build/$novelist_helper"
done
cp engine/dist/novelist-{rules,jinjiang}.js "$novelist_resources/engine/dist/"
cp engine/dist/novelist-jinjiang-api.cjs "$novelist_resources/engine/dist/"
cp engine/rules-manifest.json "$novelist_resources/engine/"
ditto chrome-extension "$novelist_resources/chrome-extension"
# Playwright's executable layout is versioned; include headed Chromium for login
# and its headless shell for background reading, together with ffmpeg.
for novelist_browser in chromium chromium_headless_shell ffmpeg; do
  novelist_found=0
  for novelist_folder in "$novelist_cache/$novelist_browser-"*; do
    [[ -d "$novelist_folder" ]] || continue
    ditto "$novelist_folder" "$novelist_resources/browsers/$(basename "$novelist_folder")"
    novelist_found=1
  done
  [[ "$novelist_found" == 1 ]] || { echo "Missing Playwright $novelist_browser; install Chromium first." >&2; exit 1; }
done
mkdir -p "$novelist_resources/licenses"
cp LICENSE "$novelist_resources/licenses/AGPL-3.0.txt"
cp engine/NOTICES.md "$novelist_resources/licenses/NOTICES.md"
ditto engine/third-party "$novelist_resources/licenses/third-party"
.venv/bin/python - "$novelist_resources/licenses" <<'PY'
from importlib.metadata import distributions
from pathlib import Path
import shutil,sys
destination=Path(sys.argv[1])
for distribution in distributions():
 directory=destination/'python-packages'/distribution.metadata['Name']
 for file in distribution.files or []:
  if '.dist-info/' not in str(file): continue
  if file.name == 'METADATA' or any(part.lower().startswith(('license','copying','notice')) for part in file.parts):
   source=Path(distribution.locate_file(file))
   if source.is_file():
    target=directory/Path(*file.parts[1:]);target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,target)
python_license=Path(sys.base_prefix)/f'lib/python{sys.version_info.major}.{sys.version_info.minor}/LICENSE.txt'
shutil.copyfile(python_license,destination/'Python-LICENSE.txt')
PY
.venv/bin/python - "$novelist_app/Contents/Info.plist" <<'PY'
import plistlib,sys
from pathlib import Path
p=Path(sys.argv[1]);d=plistlib.loads(p.read_bytes())
d.pop('NovelistProjectRoot',None);d['NovelistStandalone']=True
p.write_bytes(plistlib.dumps(d))
PY
# Re-sign nested Mach-O files before sealing the outer bundle. Chromium and the
# embedded interpreter require their existing runtime entitlements to remain.
.venv/bin/python - "$novelist_app" <<'PY'
from pathlib import Path
import subprocess,sys,re,tempfile
app=Path(sys.argv[1])
magic={b'\xcf\xfa\xed\xfe',b'\xce\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xfe\xed\xfa\xce',b'\xca\xfe\xba\xbe',b'\xbe\xba\xfe\xca'}
for p in app.rglob('*'):
 if p.is_file() and not p.is_symlink():
  with p.open('rb') as f: head=f.read(4)
  if head in magic:
   # otool parses parentheses in Chromium helper names as archive-member syntax.
   with tempfile.TemporaryDirectory(prefix='pagefall-version-') as folder:
    alias=Path(folder)/'binary';alias.symlink_to(p)
    commands=subprocess.check_output(['/usr/bin/otool','-l',str(alias)],text=True)
   versions=re.findall(r'\bminos\s+(\d+)\.(\d+)',commands) + re.findall(r'cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\d+)\.(\d+)',commands)
   if any((int(major),int(minor)) > (14,0) for major,minor in versions):
    raise SystemExit('Runtime binary requires newer than macOS 14: '+str(p))
   subprocess.run(['/usr/bin/codesign','--force','--sign','-','--preserve-metadata=entitlements',str(p)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
for p in sorted((p for p in app.rglob('*') if p.suffix in {'.app','.framework'}),key=lambda p:len(p.parts),reverse=True):
 subprocess.run(['/usr/bin/codesign','--force','--sign','-','--preserve-metadata=entitlements',str(p)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(app)],check=True)
PY
codesign --verify --deep --strict "$novelist_app"
novelist_dmg_source="$novelist_stage/dmg"
mkdir -p "$novelist_dmg_source"
ditto "$novelist_app" "$novelist_dmg_source/拾页.app"
ln -sfn /Applications "$novelist_dmg_source/Applications"
cp docs/INSTALL.md "$novelist_dmg_source/安装说明.txt"
novelist_image="$novelist_output/Pagefall-$novelist_version-macOS-$novelist_arch.dmg"
hdiutil create -ov -format UDZO -volname "拾页 Pagefall $novelist_version" -srcfolder "$novelist_dmg_source" "$novelist_image"
(cd "$novelist_output" && shasum -a 256 "$(basename "$novelist_image")" > "$(basename "$novelist_image").sha256")
echo "$novelist_image"
