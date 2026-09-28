"""Build SteelSeriesTemps.exe with PyInstaller (output in dist/).

    pip install -r requirements.txt pyinstaller
    python build.py

Downloads LibreHardwareMonitor's sensor library (MPL-2.0) and packs it into
the .exe, so CPU/GPU temperatures work without installing any other app.
"""
import io
import os
import sys
import urllib.request
import zipfile

LHM_VERSION = "v0.9.6"
LHM_ZIP = f"https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/{LHM_VERSION}/LibreHardwareMonitor.zip"
LHM_LICENSE = f"https://raw.githubusercontent.com/LibreHardwareMonitor/LibreHardwareMonitor/{LHM_VERSION}/LICENSE"
# The window/UI parts of LibreHardwareMonitor aren't needed, only the sensor library.
SKIP = ("Aga.Controls", "OxyPlot", "Microsoft.Win32.TaskScheduler")


def fetch_lhm(target="build/lhm"):
    """Extract LibreHardwareMonitorLib.dll and its dependencies into target."""
    os.makedirs(target, exist_ok=True)
    print(f"Downloading LibreHardwareMonitor {LHM_VERSION}...")
    archive = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(LHM_ZIP).read()))
    for name in archive.namelist():
        base = os.path.basename(name)
        if base.endswith(".dll") and "/" not in name.strip("/") and not base.startswith(SKIP):
            with open(os.path.join(target, base), "wb") as f:
                f.write(archive.read(name))
    with open(os.path.join(target, "LICENSE-LibreHardwareMonitor.txt"), "wb") as f:
        f.write(urllib.request.urlopen(LHM_LICENSE).read())
    if not os.path.isfile(os.path.join(target, "LibreHardwareMonitorLib.dll")):
        sys.exit("LibreHardwareMonitorLib.dll missing from the download")
    return target


if __name__ == "__main__":
    import PyInstaller.__main__

    lhm = fetch_lhm()
    PyInstaller.__main__.run([
        "--noconfirm",
        "--onefile",
        "--console",           # a small window showing the live readings; close it to stop
        "--uac-admin",         # CPU temperature needs administrator rights
        "--name", "SteelSeriesTemps",
        f"--add-data={lhm}{os.pathsep}lhm",
        "--collect-all", "pythonnet",
        "--collect-all", "clr_loader",
        "--hidden-import", "clr",
        "temps.py",
    ])
