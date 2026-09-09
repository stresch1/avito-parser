# -*- mode: python ; coding: utf-8 -*-
# Сборка десктоп-версии: pyinstaller desktop.spec
# Логика парсинга (app/scraper.py) не меняется — просто оборачиваем тот же
# FastAPI-бэкенд + тот же HTML/JS-интерфейс (app/static/) в нативное окно (pywebview).
# Playwright ходит через системный Chrome (channel="chrome"), поэтому сам браузер
# в сборку паковать не нужно — только собственный driver-бинарник playwright
# (подхватывается автоматически через его официальный PyInstaller-хук).

a = Analysis(
    ['desktop.py'],
    pathex=[],
    binaries=[],
    datas=[('app/static', 'static')],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'aiosqlite',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AvitoParser',
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
    icon='app/static/app_icon.ico',
)
