# PyInstaller (από τη ρίζα): uv run pyinstaller --noconfirm --clean desktop/misevent.spec
# Μέγεθος: χωρίς tkinter (tray μέσω ctypes) και χωρίς Pillow (το fpdf2
# το φορτώνει προαιρετικά — εικόνες σε PDF δεν χρησιμοποιούμε).
import os

ROOT = os.path.dirname(SPECPATH)  # τα μονοπάτια του spec λύνονται από τον φάκελό του


def r(path):
    return os.path.join(ROOT, path)


datas = [
    (r("webapp/templates"), "webapp/templates"),
    (r("webapp/static"), "webapp/static"),
    (r("webapp/eapd"), "webapp/eapd"),
    (r("fonts"), "fonts"),
    (r("data/init_parameters.json"), "data"),
    (r("misevent/integrations/ergani_parameters.db"), "misevent/integrations"),
]

a = Analysis(
    [r("desktop/launcher.py")],
    pathex=[ROOT],
    datas=datas,
    excludes=[
        "tkinter", "PIL", "pydoc",  # unittest: το fpdf2 κάνει import unittest.mock
        "pytest", "_pytest", "ruff", "setuptools", "pkg_resources", "distutils",
        "bz2", "_bz2", "lzma", "_lzma", "compression.zstd", "_zstd",  # zipfile: μόνο deflate
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="misevent",
    console=False,
    icon=r("webapp/static/app.ico"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="misevent")
