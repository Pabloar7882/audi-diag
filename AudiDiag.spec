# -*- mode: python ; coding: utf-8 -*-

hiddenimports=[
    'aiomysql', 'pymysql', 'serial',
    'serial.tools', 'serial.tools.list_ports',
    'serial.tools.list_ports_common', 'serial.tools.list_ports_windows',
    'serial.win32',
    'PyQt6.QtCore', 'PyQt6.QtWidgets', 'PyQt6.QtGui', 'yaml',
],

a = Analysis(
    ['main.py'],
	pathex=['src'],
    binaries=[],
    datas=[('config/config.yaml', 'config')],
    hiddenimports=['aiomysql', 'pymysql', 'serial', 'PyQt6.QtCore', 'PyQt6.QtWidgets', 'PyQt6.QtGui', 'yaml'],
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
    a.binaries,
    a.datas,
    [],
    name='AudiDiag',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
