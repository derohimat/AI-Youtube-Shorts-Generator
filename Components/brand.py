"""Brand kit shared by all projects: logo/watermark, caption font and colours, background music.

Stored in work/brand.json; uploaded files are copied into work/brand/.
"""
import glob
import json
import os
import re
import shutil
import subprocess

from Components import config

DEFAULT = {
    "logo": {"path": None, "position": "top-right", "size": 0.18, "opacity": 0.85},
    "font": {"path": None, "family": None},
    "colors": {"enabled": False, "color": "#FFFFFF", "highlight": "#FFE81F", "keyword": "#4ADE80"},
    "music": {"path": None, "volume": 0.15},
}


def brand_dir():
    return os.path.join(config.WORK_DIR, "brand")


def _path():
    return os.path.join(config.WORK_DIR, "brand.json")


def load():
    data = json.loads(json.dumps(DEFAULT))
    try:
        with open(_path(), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return data
    for key, value in saved.items():
        if key in data and isinstance(value, dict):
            data[key].update(value)
    return data


def save(data):
    from Components.pipeline import _write_json
    os.makedirs(config.WORK_DIR, exist_ok=True)
    _write_json(_path(), data)
    return data


def update(section, **fields):
    data = load()
    data[section].update(fields)
    return save(data)


def _store(upload, name):
    """Copy an uploaded file into work/brand/<name><ext>, replacing earlier versions."""
    os.makedirs(brand_dir(), exist_ok=True)
    for old in glob.glob(os.path.join(brand_dir(), name + ".*")):
        os.remove(old)
    target = os.path.join(brand_dir(), name + os.path.splitext(upload)[1].lower())
    shutil.copy(upload, target)
    return target


def set_logo(upload):
    return update("logo", path=_store(upload, "logo") if upload else None)


def set_music(upload):
    return update("music", path=_store(upload, "music") if upload else None)


def font_family(path):
    """Family name of a .ttf/.otf file (fontTools, falling back to fc-scan)."""
    try:
        from fontTools.ttLib import TTFont
        return TTFont(path, lazy=True)["name"].getBestFamilyName()
    except Exception:  # noqa: BLE001 - try the next method
        pass
    try:
        out = subprocess.run(["fc-scan", "--format", "%{family[0]}", path], capture_output=True, text=True)
        return out.stdout.strip() or None
    except OSError:
        return None


def fonts_dir():
    """Folder with the bundled fonts plus the brand font (libass reads fonts from one folder)."""
    target = os.path.join(brand_dir(), "fonts")
    os.makedirs(target, exist_ok=True)
    for bundled in glob.glob(os.path.join(config.FONTS_DIR, "*.[ot]tf")):
        dest = os.path.join(target, os.path.basename(bundled))
        if not os.path.exists(dest):
            shutil.copy(bundled, dest)
    return target


def set_font(upload):
    if not upload:
        return update("font", path=None, family=None)
    family = font_family(upload)
    if not family:
        raise ValueError("Could not read the font name; please upload a .ttf or .otf font file.")
    target = os.path.join(fonts_dir(), "brand-" + re.sub(r"[^\w.-]", "_", os.path.basename(upload)))
    for old in glob.glob(os.path.join(fonts_dir(), "brand-*")):
        os.remove(old)
    shutil.copy(upload, target)
    return update("font", path=target, family=family)


def _hex(value):
    """'#rrggbb', 'rrggbb' or 'rgba(r, g, b, a)' -> 'RRGGBB'."""
    value = str(value or "").strip()
    match = re.match(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", value)
    if match:
        return "".join(f"{int(float(c)):02X}" for c in match.groups())
    value = value.lstrip("#")
    return value[:6].upper() if re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", value) else None


def render_kwargs(data=None, use=True):
    """Keyword arguments for render_short / render_still from the brand kit."""
    data = data or load()
    if not use:
        return {}
    kwargs = {}
    logo = data["logo"]
    if logo.get("path") and os.path.exists(logo["path"]):
        kwargs["logo"] = dict(logo)
    font = data["font"]
    if font.get("family") and font.get("path") and os.path.exists(font["path"]):
        kwargs["font"] = font["family"]
        kwargs["fonts_dir"] = fonts_dir()
    colors = data["colors"]
    if colors.get("enabled"):
        kwargs["colors"] = {k: _hex(colors.get(k)) for k in ("color", "highlight", "keyword") if _hex(colors.get(k))}
    music = data["music"]
    if music.get("path") and os.path.exists(music["path"]):
        kwargs["music"] = dict(music)
    return kwargs


def fingerprint(data=None):
    """Changes whenever the brand kit changes (used in preview cache keys)."""
    return json.dumps(data or load(), sort_keys=True)
