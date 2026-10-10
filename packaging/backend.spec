from PyInstaller.utils.hooks import collect_all, collect_submodules
from pathlib import Path

root = Path(SPECPATH).parent
playwright_data, playwright_bins, playwright_imports = collect_all('playwright')
crypto_data, crypto_bins, crypto_imports = collect_all('Cryptodome')
analysis = Analysis(
    [str(root / 'packaging/backend.py')],
    pathex=[str(root)],
    binaries=playwright_bins + crypto_bins,
    datas=playwright_data + crypto_data,
    hiddenimports=playwright_imports + crypto_imports + collect_submodules('uvicorn'),
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='pagefall-backend',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=True, target_arch=None, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='backend')
