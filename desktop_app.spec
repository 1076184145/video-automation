# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path.cwd()
datas = [
    (str(project_root / "web"), "web"),
    (str(project_root / ".env.example"), "."),
]
for tool_script in ["check_desktop_tools.ps1", "install_desktop_tools.ps1"]:
    path = project_root / "tools" / tool_script
    if path.exists():
        datas.append((str(path), "tools"))
tools_bin = project_root / "tools" / "bin"
if tools_bin.exists():
    datas.append((str(tools_bin), "tools/bin"))


a = Analysis(
    ["desktop_app.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=["webview"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VideoAutomation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="VideoAutomation",
)
