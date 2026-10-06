"""
Scans the system for installed fonts (Windows / macOS / Linux) and returns
a {display_name: file_path} map. Uses fontTools to read the real family
name out of each font file, so the dropdown shows names the user recognizes
(e.g. "Montserrat Bold") instead of raw filenames.
"""
import os
import sys
import glob

from fontTools.ttLib import TTFont


def _font_dirs():
    dirs = []
    if sys.platform.startswith("win"):
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs.append(os.path.join(windir, "Fonts"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    elif sys.platform == "darwin":
        dirs += ["/Library/Fonts", "/System/Library/Fonts",
                 os.path.expanduser("~/Library/Fonts")]
    else:
        dirs += ["/usr/share/fonts", "/usr/local/share/fonts",
                  os.path.expanduser("~/.fonts"),
                  os.path.expanduser("~/.local/share/fonts")]
    return [d for d in dirs if os.path.isdir(d)]


def _family_name(path):
    try:
        font = TTFont(path, lazy=True, fontNumber=0)
        name_table = font["name"]
        # nameID 1 = Font Family name, 16 = Preferred Family (more reliable
        # when it exists, e.g. for style-linked families)
        for name_id in (16, 1):
            rec = name_table.getName(name_id, 3, 1, 0x409) or name_table.getName(name_id, 1, 0, 0)
            if rec:
                try:
                    return rec.toUnicode()
                except Exception:
                    pass
    except Exception:
        return None
    return None


def list_system_fonts():
    """Returns a sorted dict {family_name: filepath} for installed fonts."""
    found = {}
    for d in _font_dirs():
        for ext in ("*.ttf", "*.otf", "*.ttc"):
            for path in glob.glob(os.path.join(d, "**", ext), recursive=True):
                name = _family_name(path)
                if name and name not in found:
                    found[name] = path
    if not found:
        # Fallback so the app never shows an empty dropdown
        found["Arial"] = "Arial"
    return dict(sorted(found.items(), key=lambda kv: kv[0].lower()))


if __name__ == "__main__":
    fonts = list_system_fonts()
    print(f"Found {len(fonts)} fonts")
    for n, p in list(fonts.items())[:10]:
        print(f"  {n} -> {p}")
