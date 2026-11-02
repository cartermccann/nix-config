"""XKB layout catalog: identifiers such as "us(colemak_dh)" and their names."""
import os
from pathlib import Path
import xml.etree.ElementTree as ET

# The package substitutes the xkeyboard-config store path for this directory.
DEFAULT_ROOT = "/usr/share/X11/xkb"


def rules_dir():
    for root in (os.environ.get("POND_XKB_ROOT"), os.environ.get("XKB_CONFIG_ROOT"), DEFAULT_ROOT, "/etc/X11/xkb"):
        if root and (Path(root) / "rules/evdev.xml").is_file():
            return Path(root) / "rules"
    raise RuntimeError("The XKB layout catalog (evdev.xml) is unavailable")


_CACHE = None


def layouts():
    """Every layout and variant, keyed by identifier, in catalog order."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    directory, result = rules_dir(), {}
    for rules in ("evdev.xml", "evdev.extras.xml"):
        path = directory / rules
        if not path.is_file():
            continue
        for layout in ET.parse(path).iterfind("layoutList/layout"):
            name = layout.findtext("configItem/name")
            if not name or name == "custom":
                continue
            result.setdefault(name, {"id": name, "label": layout.findtext("configItem/description") or name,
                                     "listed": rules == "evdev.xml"})
            for variant in layout.iterfind("variantList/variant"):
                identifier = f"{name}({variant.findtext('configItem/name')})"
                result.setdefault(identifier, {"id": identifier,
                                               "label": variant.findtext("configItem/description") or identifier})
    _CACHE = result
    return result


def split(identifier):
    layout, _, variant = identifier.partition("(")
    return layout, variant.rstrip(")")


def join(layout, variant):
    return f"{layout}({variant})" if variant else layout


def identifiers(layout_csv, variant_csv=""):
    """Hyprland's comma-separated kb_layout/kb_variant as identifiers."""
    names = [part.strip() for part in str(layout_csv or "").split(",")]
    variants = [part.strip() for part in str(variant_csv or "").split(",")]
    return [join(name, variants[i] if i < len(variants) else "") for i, name in enumerate(names) if name]


def option_values(ids):
    pairs = [split(identifier) for identifier in ids]
    layout = ",".join(name for name, _ in pairs)
    variant = ",".join(variant for _, variant in pairs)
    return layout, ("" if not variant.strip(",") else variant)


def label(identifier):
    try:
        return layouts().get(identifier, {}).get("label") or identifier
    except (RuntimeError, OSError, ET.ParseError):
        return identifier
