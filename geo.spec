# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller geo.spec --noconfirm
# Prerequisites: pnpm --filter @geo/web build   (must run first)
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# Collect playwright (driver binary + python bindings + data files)
playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")

# Collect all server sub-modules so none are missed due to string-based uvicorn import
server_hiddenimports = collect_submodules("server")

datas = [
    # Alembic migration scripts
    ("server/alembic", "server/alembic"),
]

# Bundle the frontend build if it exists
if Path("web/dist").exists():
    datas.append(("web/dist", "web/dist"))

datas += playwright_datas

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=playwright_binaries,
    datas=datas,
    hiddenimports=[
        *server_hiddenimports,
        *playwright_hiddenimports,
        # uvicorn internals not auto-detected
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # asyncio / event loop
        "anyio",
        "anyio._backends._asyncio",
        "anyio._backends._trio",
        "sniffio",
        # HTTP
        "h11",
        # SQLAlchemy dialect
        "sqlalchemy.dialects.sqlite",
        "sqlalchemy.sql.default_comparator",
        "sqlalchemy.ext.asyncio",
        # Alembic
        "alembic.runtime.migration",
        "alembic.operations",
        "alembic.operations.ops",
        "alembic.operations.base",
        "alembic.script.base",
        "alembic.script.revision",
        "alembic.autogenerate",
        "alembic.autogenerate.api",
        # Pydantic
        "pydantic_settings",
        # multipart (file upload)
        "multipart",
        "python_multipart",
        # email validation (pydantic)
        "email_validator",
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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="GeoCollab",
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
    icon=None,
)
