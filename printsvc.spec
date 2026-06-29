# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('printsvc.json', '.')]
binaries = [('C:\\Users\\cheng\\AppData\\Local\\Programs\\Python\\Python38-32\\Lib\\site-packages\\pywin32_system32\\pythoncom38.dll', '.'), ('C:\\Users\\cheng\\AppData\\Local\\Programs\\Python\\Python38-32\\Lib\\site-packages\\pywin32_system32\\pywintypes38.dll', '.')]
hiddenimports = ['printsvc', 'printsvc.ipp', 'printsvc.server', 'printsvc.winprint', 'printsvc.discovery', 'printsvc.config', 'printsvc.logger', 'printsvc.main', 'printsvc.docrender', 'win32print', 'win32ui', 'win32api', 'win32con', 'win32gui', 'PIL', 'PIL.Image', 'PIL.ImageWin', 'zeroconf', 'fitz']
tmp_ret = collect_all('win32print')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('zeroconf')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PrintSVC',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon=None,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
