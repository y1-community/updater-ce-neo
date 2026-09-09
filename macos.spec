# -*- mode: python ; coding: utf-8 -*-
# macos.spec — PyInstaller spec for macOS .app bundle
# Usage: pyinstaller macos.spec

import os
import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets', 'assets'),
    ],
    hiddenimports=[
        'PySide6.QtSvg',
        'PySide6.QtNetwork',
        'pyqt_liquidglass',
        'objc',
        'AppKit',
        'Foundation',
        'Quartz',
        'src.ui.glass',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

target_arch = os.environ.get('TARGET_ARCH', None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Innioasis Updater CE',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=target_arch,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.icns',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Innioasis Updater',
)

app = BUNDLE(
    coll,
    name='Innioasis Updater.app',
    icon='assets/icon.icns',
    bundle_identifier='com.innioasis.updater',
    info_plist={
        'CFBundleDisplayName': 'Innioasis Updater CE',
        'CFBundleShortVersionString': '3.0.0',
        'CFBundleVersion': '3.0.0',
        'LSMinimumSystemVersion': '13.0',
        'NSHighResolutionCapable': True,
        'NSRequiresAquaSystemAppearance': False,
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'Firmware Archive',
                'CFBundleTypeRole': 'Viewer',
                'LSHandlerRank': 'Alternate',
                'LSItemContentTypes': [
                    'com.pkware.zip-archive',
                    'public.zip-archive',
                    'org.rarlab.rar-archive',
                ],
            }
        ],
    },
)
