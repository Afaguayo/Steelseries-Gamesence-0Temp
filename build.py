"""Build SteelSeriesTemps.exe with PyInstaller (output in dist/).

    pip install -r requirements.txt pyinstaller
    python build.py
"""
import PyInstaller.__main__

PyInstaller.__main__.run([
    "--noconfirm",
    "--onefile",
    "--console",          # a small window showing the live readings; close it to stop
    "--name", "SteelSeriesTemps",
    "temps.py",
])
