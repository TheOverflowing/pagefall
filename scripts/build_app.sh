#!/bin/bash
set -euo pipefail

novelist_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
novelist_build="$novelist_root/.build"
novelist_app="$novelist_root/拾页.app"
novelist_arch="$(uname -m)"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "拾页原生应用需要在 macOS 上构建。" >&2
  exit 1
fi
if [[ ! -f "$novelist_root/native/App.swift" ]]; then
  echo "尚未找到 native/App.swift；请等待原生界面源码准备完成后重新构建。" >&2
  exit 1
fi
if [[ ! -f "$novelist_root/native/vision_ocr.swift" ]]; then
  echo "缺少 native/vision_ocr.swift，无法构建文字识别程序。" >&2
  exit 1
fi
if ! /usr/bin/xcrun --find swiftc >/dev/null 2>&1; then
  echo "未找到 Swift 编译器。请先安装 Xcode Command Line Tools。" >&2
  exit 1
fi
if [[ -x "$novelist_root/.venv/bin/python" ]]; then
  novelist_python="$novelist_root/.venv/bin/python"
else
  novelist_python="$(command -v python3 || true)"
fi
if [[ -z "$novelist_python" ]]; then
  echo "未找到 Python 3，请先按 README 完成开发环境安装。" >&2
  exit 1
fi

mkdir -p "$novelist_build/swift-module-cache"
echo "正在构建晋江正文解析…"
cd "$novelist_root"
node scripts/build_jinjiang.mjs
echo "正在构建本地文字识别…"
/usr/bin/xcrun swiftc -O \
  -target "$novelist_arch-apple-macosx14.0" \
  -module-cache-path "$novelist_build/swift-module-cache" \
  "$novelist_root/native/vision_ocr.swift" \
  -o "$novelist_build/vision-ocr"
/usr/bin/xcrun swiftc -O -framework Security \
  "$novelist_root/native/session_store.swift" -o "$novelist_build/session-store"
/usr/bin/codesign --force --sign - --identifier local.novelist.session-store "$novelist_build/session-store"
/usr/bin/xcrun swiftc -O -framework Security \
  "$novelist_root/native/api_key_store.swift" -o "$novelist_build/api-key-store"
/usr/bin/codesign --force --sign - --identifier local.novelist.api-key-store "$novelist_build/api-key-store"

echo "正在构建拾页原生界面…"
/usr/bin/xcrun swiftc -O -parse-as-library \
  -target "$novelist_arch-apple-macosx14.0" \
  -module-cache-path "$novelist_build/swift-module-cache" \
  -framework SwiftUI -framework AppKit \
  "$novelist_root/native/App.swift" "$novelist_root/native/LibraryView.swift" "$novelist_root/native/Localization.swift" "$novelist_root/native/TranslationViews.swift" "$novelist_root/native/BookViews.swift" "$novelist_root/native/LibraryFiltering.swift" "$novelist_root/native/LibraryFiltersView.swift" \
  -o "$novelist_build/NovelistApp"

mkdir -p "$novelist_app/Contents/MacOS" "$novelist_app/Contents/Resources"
/usr/bin/install -m 755 "$novelist_build/NovelistApp" "$novelist_app/Contents/MacOS/Novelist"
/usr/bin/swift "$novelist_root/scripts/make_icon.swift" "$novelist_build/AppIcon.iconset"
/usr/bin/iconutil -c icns "$novelist_build/AppIcon.iconset" -o "$novelist_app/Contents/Resources/AppIcon-v04.icns"
# Earlier builds shipped the abandoned artwork as AppIcon.icns; drop it so it
# cannot be picked up from the bundle again.
/bin/rm -f "$novelist_app/Contents/Resources/AppIcon.icns"
for novelist_language in en zh-Hans; do
  /bin/mkdir -p "$novelist_app/Contents/Resources/$novelist_language.lproj"
  /bin/cp "$novelist_root/native/Resources/$novelist_language.lproj/Localizable.strings" "$novelist_app/Contents/Resources/$novelist_language.lproj/Localizable.strings"
  /bin/cp "$novelist_root/native/Resources/$novelist_language.lproj/InfoPlist.strings" "$novelist_app/Contents/Resources/$novelist_language.lproj/InfoPlist.strings"
done
"$novelist_python" - "$novelist_app/Contents/Info.plist" "$novelist_root" <<'PY'
from pathlib import Path
import plistlib
import sys

destination = Path(sys.argv[1])
values = {
    "CFBundleDevelopmentRegion": "zh_CN",
    "CFBundleLocalizations": ["en", "zh-Hans"],
    "CFBundleDisplayName": "拾页",
    "CFBundleExecutable": "Novelist",
    "CFBundleIdentifier": "local.novelist.reader",
    "CFBundleInfoDictionaryVersion": "6.0",
    "CFBundleName": "拾页",
    "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": "0.7.0",
    "CFBundleVersion": "9",
    "LSApplicationCategoryType": "public.app-category.books",
    "LSMinimumSystemVersion": "14.0",
    "NSHighResolutionCapable": True,
    "NSPrincipalClass": "NSApplication",
    "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    "NovelistProjectRoot": sys.argv[2],
}
# Preserve an optional icon added by the project without requiring one to build.
if (destination.parent / "Resources" / "AppIcon-v04.icns").is_file():
    values["CFBundleIconFile"] = "AppIcon-v04"
with destination.open("wb") as stream:
    plistlib.dump(values, stream, sort_keys=False)
PY
/usr/bin/plutil -lint "$novelist_app/Contents/Info.plist"

if [[ -x /usr/bin/codesign && "${NOVELIST_SKIP_CODESIGN:-0}" != "1" ]]; then
  /usr/bin/codesign --force --deep --sign - "$novelist_app"
  /usr/bin/codesign --verify --deep --strict "$novelist_app"
fi
# The Dock shows Launch Services' cached icon once the app quits. Touch the
# bundle and re-register it so the cache picks up the current artwork.
/usr/bin/touch "$novelist_app" "$novelist_app/Contents/Info.plist"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$novelist_app" || true
echo "已生成：$novelist_app"
echo "这是依赖当前项目目录的本机开发版本，未进行 Apple 公证。"
