# -*- mode: python ; coding: utf-8 -*-
"""Echo 打包配置：单文件、无控制台

    python -m PyInstaller Echo.spec --noconfirm

把用不到的东西全排掉（setuptools / PIL / tkinter 等都是被间接拖进来的）；
ui/ 与 docs/ 作为数据目录一起塞进去 —— docs/AGENT.md 就是「复制给 AI」的正文。
"""

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('ui', 'ui'), ('docs', 'docs')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'pydoc', 'doctest', 'test', 'lib2to3',
        'PIL', 'numpy', 'pkg_resources',
        'sqlite3', 'xmlrpc', 'ftplib', 'imaplib', 'smtplib',
        'xml.dom', 'xml.sax', 'pdb', 'cProfile', 'pstats',
        'webview.platforms.gtk', 'webview.platforms.qt',
        'webview.platforms.cef', 'webview.platforms.android',
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Echo',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['docs/icon.ico'],
)
