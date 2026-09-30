"""Start the app in the background when the user logs in.

- Windows: a Task Scheduler logon task with highest privileges. A Startup
  folder shortcut would show a UAC prompt at every login, because reading
  CPU temperatures needs administrator rights; a task doesn't.
- macOS: a LaunchAgent in ~/Library/LaunchAgents.
- Linux: an autostart entry in ~/.config/autostart.
"""
import os
import subprocess
import sys

TASK_NAME = "SteelSeriesTemps"
MAC_LABEL = "com.afaguayo.steelseriestemps"


def launch_command():
    """How to start this app in the background, as an argument list."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--background"]
    python = sys.executable
    if sys.platform == "win32":                     # no console window at login
        pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
        python = pythonw if os.path.isfile(pythonw) else python
    return [python, os.path.abspath(os.path.join(os.path.dirname(__file__), "temps.py")), "--background"]


def mac_plist_path(home=None):
    return os.path.join(home or os.path.expanduser("~"), "Library", "LaunchAgents", MAC_LABEL + ".plist")


def linux_desktop_path(home=None):
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home or os.path.expanduser("~"), ".config")
    return os.path.join(config, "autostart", "steelseries-temps.desktop")


def quote_windows(args):
    return " ".join(f'"{a}"' if " " in a or not a.startswith("-") else a for a in args)


def set_enabled(enable, command=None, platform=sys.platform, run=subprocess.run, home=None):
    """Turn starting at login on or off. Returns True on success."""
    command = command or launch_command()
    if platform == "win32":
        if enable:
            args = ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", quote_windows(command),
                    "/SC", "ONLOGON", "/RL", "HIGHEST", "/F"]
        else:
            args = ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]
        return run(args, capture_output=True, text=True).returncode == 0
    path = mac_plist_path(home) if platform == "darwin" else linux_desktop_path(home)
    if not enable:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if platform == "darwin":
        arguments = "".join(f"\n        <string>{escape_xml(a)}</string>" for a in command)
        text = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{MAC_LABEL}</string>
    <key>ProgramArguments</key>
    <array>{arguments}
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""
    else:
        exec_line = " ".join(f'"{a}"' if " " in a else a for a in command)
        text = f"[Desktop Entry]\nType=Application\nName=SteelSeries Temps\nExec={exec_line}\nX-GNOME-Autostart-enabled=true\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


def is_enabled(platform=sys.platform, run=subprocess.run, home=None):
    if platform == "win32":
        try:
            return run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0
        except OSError:
            return False
    return os.path.isfile(mac_plist_path(home) if platform == "darwin" else linux_desktop_path(home))


def escape_xml(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
