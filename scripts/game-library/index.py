#!/usr/bin/env python3
"""
index — walk the library and write the JSON index the web UI reads.

The front end never touches the filesystem: it loads one JSON file describing
every title and platform. That keeps page loads off directory walks, and makes
the index cheap to regenerate whenever something is added.

    LIBRARY_ROOT   base directory of the library (default: ./library)
    INDEX_OUT      where to write the index  (default: <root>/web/index.json)
"""
import os, json, time, hashlib
from urllib.parse import quote

LIBRARY = os.environ.get("LIBRARY_ROOT", os.path.abspath("./library"))
TITLES  = os.path.join(LIBRARY, "titles")
OUT     = os.environ.get("INDEX_OUT", os.path.join(LIBRARY, "web", "index.json"))

# platform directory -> (emulator core, display label, accent colour)
PLATFORMS = {
    "nes":          ("nes",        "NES",              "#b4403a"),
    "snes":         ("snes",       "Super NES",        "#6f63ad"),
    "n64":          ("n64",        "Nintendo 64",      "#3f8f63"),
    "gb":           ("gb",         "Game Boy",         "#7e8a4e"),
    "gbc":          ("gb",         "Game Boy Color",   "#b5568f"),
    "gba":          ("gba",        "Game Boy Advance", "#4b63a8"),
    "genesis":      ("segaMD",     "Genesis",          "#2f6ea8"),
    "mastersystem": ("segaMS",     "Master System",    "#4a7fa0"),
    "gamegear":     ("segaGG",     "Game Gear",        "#5d6f8c"),
    "segacd":       ("segaCD",     "Sega CD",          "#2b5f92"),
    "saturn":       ("segaSaturn", "Saturn",           "#3d5a80"),
    "psx":          ("psx",        "PlayStation",      "#8d8577"),
    "psp":          ("psp",        "PSP",              "#6b7280"),
    "atari2600":    ("atari2600",  "Atari 2600",       "#a86a3c"),
    "atari7800":    ("atari7800",  "Atari 7800",       "#9c5f36"),
    "lynx":         ("lynx",       "Lynx",             "#8a7a3c"),
    "jaguar":       ("jaguar",     "Jaguar",           "#7b6030"),
    "vb":           ("vb",         "Virtual Boy",      "#a33b3b"),
    "ws":           ("ws",         "WonderSwan",       "#6b7a8a"),
    "ngp":          ("ngp",        "Neo Geo Pocket",   "#7a6a8a"),
    "pce":          ("pce",        "PC Engine",        "#a05a48"),
    "coleco":       ("coleco",     "ColecoVision",     "#8a6a4a"),
    "3do":          ("3do",        "3DO",              "#6a7a6a"),
    "arcade":       ("arcade",     "Arcade",           "#a8483c"),
    "amiga":        ("amiga",      "Amiga",            "#5a7a9a"),
    "dos":          ("dos",        "DOS",              "#7a8a7a"),
    "web":          ("web",        "Browser",          "#4a8a7a"),
}

# Anything that isn't a title: artwork, notes, save states, stray config.
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt", ".md", ".nfo",
            ".sav", ".srm", ".state", ".part", ".zst", ".zss", ".ips",
            ".bat", ".dll", ".ini", ".cfg", ".exe", ".DS_Store"}
ART_EXT = (".png", ".jpg", ".jpeg", ".webp")


def entry_id(s):
    """Stable short id, so the UI can remember a selection across rebuilds."""
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def pretty(name):
    """Strip the region and revision tags that litter filenames."""
    n = os.path.splitext(name)[0]
    for tag in ("(USA)", "(Europe)", "(Japan)", "(World)", "(En,Fr,De)",
                "[!]", "(Rev 1)", "(Rev A)"):
        n = n.replace(tag, "")
    return " ".join(n.replace("_", " ").split())


def art_for(dirpath, base, platform, prefix):
    for ext in ART_EXT:
        p = os.path.join(dirpath, base + ext)
        if os.path.exists(p):
            return f"{prefix}/{platform}/{quote(os.path.basename(p))}"
    return None


def scan(root=None, prefix="/titles"):
    base = root or TITLES
    titles, seen = [], set()

    for platform in sorted(os.listdir(base)):
        pdir = os.path.join(base, platform)
        if not os.path.isdir(pdir) or platform not in PLATFORMS:
            continue
        core, label, colour = PLATFORMS[platform]

        # Browser titles are directories with an index.html, not single files.
        if platform == "web":
            for name in sorted(os.listdir(pdir)):
                sub = os.path.join(pdir, name)
                if os.path.isdir(sub) and os.path.exists(os.path.join(sub, "index.html")):
                    titles.append({
                        "id": entry_id(f"web/{name}"), "title": pretty(name),
                        "platform": platform, "label": label, "colour": colour,
                        "core": "web",
                        "url": f"{prefix}/web/{quote(name)}/index.html",
                        "size": 0, "art": art_for(pdir, name, platform, prefix),
                    })
                    seen.add(platform)
            continue

        for name in sorted(os.listdir(pdir)):
            path = os.path.join(pdir, name)
            if not os.path.isfile(path):
                continue
            stem, ext = os.path.splitext(name)
            if ext.lower() in SKIP_EXT or name.startswith("."):
                continue
            titles.append({
                "id": entry_id(f"{platform}/{name}"), "title": pretty(name),
                "platform": platform, "label": label, "colour": colour,
                "core": core, "url": f"{prefix}/{platform}/{quote(name)}",
                "size": os.path.getsize(path),
                "art": art_for(pdir, stem, platform, prefix),
            })
            seen.add(platform)

    platforms = [{"id": p, "label": PLATFORMS[p][1], "colour": PLATFORMS[p][2],
                  "count": sum(1 for t in titles if t["platform"] == p)}
                 for p in sorted(seen, key=lambda x: PLATFORMS[x][1])]

    return {"generated": int(time.time()), "platforms": platforms,
            "titles": sorted(titles, key=lambda t: t["title"].lower())}


def write(data, path):
    """Write atomically, so the UI never reads a half-written index."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)
    return len(data["titles"]), len(data["platforms"])


if __name__ == "__main__":
    n, p = write(scan(), OUT)
    print(f"{n} titles across {p} platforms")
