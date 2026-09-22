#!/usr/bin/env python3
"""
detect — work out whether a game can run in a browser, and if it can, install
it into the library.

    detect --check PATH        report only, change nothing
    detect PATH [NAME]         import one title
    detect --all PATH          import everything importable under PATH

PATH can be a game folder or a .zip / .7z / .rar archive.

This is the shared classification module for the whole pipeline. Every other
stage asks it the same question — what can actually run this? — and keys its
behavior off the verdict, so an improvement here improves every stage at once.

Paths come from the environment so the module isn't tied to one install:

    LIBRARY_ROOT   base directory of the library (default: ./library)
"""
import os, sys, shutil, subprocess, tempfile, re, json

LIBRARY = os.environ.get("LIBRARY_ROOT", os.path.abspath("./library"))
WEB     = os.path.join(LIBRARY, "titles", "web")
DOS     = os.path.join(LIBRARY, "titles", "dos")

BOLD="\033[1m"; DIM="\033[90m"; GRN="\033[92m"; YEL="\033[93m"; RED="\033[91m"; CYN="\033[96m"; END="\033[0m"

# Cartridge and disc image formats, mapped to the platform that runs them.
CART_EXT = {
    ".nes":"nes", ".fds":"nes", ".sfc":"snes", ".smc":"snes", ".gb":"gb",
    ".gbc":"gbc", ".gba":"gba", ".n64":"n64", ".z64":"n64", ".v64":"n64",
    ".md":"genesis", ".gen":"genesis", ".smd":"genesis", ".32x":"genesis",
    ".sms":"mastersystem", ".gg":"gamegear", ".pce":"pce", ".a26":"atari2600",
    ".a78":"atari7800", ".lnx":"lynx", ".ws":"ws", ".wsc":"ws", ".ngp":"ngp",
    ".ngc":"ngp", ".vb":"vb", ".col":"coleco", ".adf":"amiga",
}


# ---------------------------------------------------------------- detect
def detect(root):
    """Return (engine, verdict, detail).

    verdict:
        'web'      runs in a browser as-is
        'convert'  convertible, but needs a build step
        'cart'     a cartridge/disc image for an emulated platform
        'dos'      DOS-era or early Windows executable
        'no'       nothing here can run in a browser

    Walks at most two directory levels: deep enough to see past a wrapper
    folder, shallow enough that a large title doesn't cost a full tree walk.
    """
    names, lower = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        if depth > 2:
            dirnames[:] = []
            continue
        for f in filenames:
            names.append(os.path.join(dirpath, f))
            lower.append(f.lower())
        for d in dirnames:
            lower.append(d.lower() + "/")

    def has(*subs):
        return any(any(s in x for x in lower) for s in subs)

    def top(name):
        return os.path.exists(os.path.join(root, name))

    # --- already a web build --------------------------------------------
    idx = None
    for cand in ("index.html", os.path.join("www", "index.html")):
        if top(cand):
            idx = cand
            break

    if idx:
        if has("renpy.js", "game.zip") and has("web-presplash", "renpy"):
            return ("Ren'Py (web build)", "web", "already converted")
        if has("rmmz_core.js"):
            return ("RPG Maker MZ", "web", "runs natively in a browser")
        if has("rpg_core.js"):
            return ("RPG Maker MV", "web", "runs natively in a browser")
        if has(".loader.js") or has("unitywebgl", "build/"):
            return ("Unity (WebGL build)", "web", "already a browser build")
        if has(".wasm") and has(".pck"):
            return ("Godot (web export)", "web", "already a browser build")
        if has("twine", "harlowe", "sugarcube"):
            return ("Twine", "web", "plain HTML")
        return ("HTML5 / web", "web", "has an index.html")

    # --- older Ruby-based RPG Maker engines ------------------------------
    if has(".rvdata2"):
        return ("RPG Maker VX Ace", "no",
                "Ruby engine (RGSS3) — no browser runtime exists")
    if has(".rvdata"):
        return ("RPG Maker VX", "no", "Ruby engine (RGSS2) — no browser runtime")
    if has(".rxdata"):
        return ("RPG Maker XP", "no", "Ruby engine (RGSS) — no browser runtime")

    # --- Ren'Py -----------------------------------------------------------
    if has("renpy/") and has("game/"):
        return ("Ren'Py", "convert",
                "convertible — needs a web build from the Ren'Py launcher")

    # --- native engine builds ---------------------------------------------
    if has("unityplayer.dll") or has("_data/") or has("unitycrashhandler"):
        return ("Unity (native)", "no",
                "compiled desktop build — a WebGL build must come from the developer")
    if has("engine/") and has(".pak"):
        return ("Unreal Engine", "no", "no browser target for shipped builds")
    if has(".pck") and any(x.endswith(".exe") for x in lower):
        return ("Godot (native)", "no",
                "needs a web export from the original project")

    # --- ScummVM -----------------------------------------------------------
    if has(".scummvm") or has("monkey.000", ".he0", ".la0"):
        return ("ScummVM title", "no",
                "no browser runtime — look for the DOS release instead")

    # --- Flash ---------------------------------------------------------------
    swfs = [n for n in names if n.lower().endswith(".swf")]
    if swfs:
        return ("Flash (.swf)", "web", "wrapped with Ruffle")

    # --- cartridge / disc images ---------------------------------------------
    for n in names:
        ext = os.path.splitext(n)[1].lower()
        if ext in CART_EXT:
            return (f"cartridge image ({ext})", "cart", CART_EXT[ext])

    # --- DOS / early Windows --------------------------------------------------
    exes = [n for n in names if n.lower().endswith(".exe")]
    if exes:
        # A small executable with no engine data directory is usually a DOS-era
        # program. A large one is a modern native build.
        small = [e for e in exes if os.path.getsize(e) < 3 * 1024 * 1024]
        if small and not has("_data/", "engine/"):
            return ("DOS / early Windows", "dos", "try the dos/ folder")
        return ("native executable", "no",
                "compiled binary — stream it from a machine with a GPU instead")

    return ("unknown", "no", "no recognisable engine files found")


# ---------------------------------------------------------------- helpers
def slug(s):
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[\s_]+", "-", s) or "title"


def unpack(path):
    """Extract an archive to a temp dir and return (dir, cleanup_dir).

    Nothing inside the archive is executed — files are read, never run.
    """
    tmp = tempfile.mkdtemp(prefix="detect-")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".zip":
        cmd = ["unzip", "-q", "-o", path, "-d", tmp]
    elif ext == ".7z":
        cmd = ["7z", "x", "-y", f"-o{tmp}", path]
    elif ext == ".rar":
        # Note: the distro's free unrar and p7zip both fail on multi-volume
        # sets (unsupported method / outright failure). Use the upstream tool.
        cmd = [os.environ.get("UNRAR_BIN", "unrar"), "x", "-y", path, tmp]
    else:
        shutil.rmtree(tmp)
        return None, None
    if subprocess.run(cmd, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return None, None
    # collapse a single wrapper folder
    entries = os.listdir(tmp)
    if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
        return os.path.join(tmp, entries[0]), tmp
    return tmp, tmp


def ruffle_wrapper(dest, swf_name, title):
    """Write a minimal player page around a Flash file."""
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>html,body{{margin:0;height:100%;background:#000;overflow:hidden}}
#p{{width:100%;height:100%}}</style>
<script src="https://unpkg.com/@ruffle-rs/ruffle"></script></head>
<body><div id="p"></div><script>
window.RufflePlayer=window.RufflePlayer||{{}};
window.addEventListener('load',()=>{{
  const r=window.RufflePlayer.newest();
  const p=r.createPlayer();
  document.getElementById('p').appendChild(p);
  p.style.width='100%';p.style.height='100%';
  p.load({json.dumps(swf_name)});
}});
</script></body></html>"""
    with open(os.path.join(dest, "index.html"), "w") as f:
        f.write(html)


# ---------------------------------------------------------------- import
def do_import(root, engine, detail, name=None):
    title = name or os.path.basename(root.rstrip("/"))
    dest = os.path.join(WEB, slug(title))
    if os.path.exists(dest):
        return False, f"already in the library at web/{slug(title)}"

    src = root
    # RPG Maker MV/MZ ship their runtime inside www/
    if os.path.exists(os.path.join(root, "www", "index.html")) and \
       not os.path.exists(os.path.join(root, "index.html")):
        src = os.path.join(root, "www")

    os.makedirs(dest, exist_ok=True)
    # Native binaries are never copied into a web-served directory.
    subprocess.run(["rsync", "-a", "--exclude", "*.exe", "--exclude", "*.dll",
                    src.rstrip("/") + "/", dest + "/"],
                   stdout=subprocess.DEVNULL, check=False)

    if engine.startswith("Flash"):
        swf = None
        for dirpath, _, files in os.walk(dest):
            for f in files:
                if f.lower().endswith(".swf"):
                    swf = os.path.relpath(os.path.join(dirpath, f), dest)
                    break
            if swf:
                break
        if not swf:
            shutil.rmtree(dest, ignore_errors=True)
            return False, "no .swf found after copy"
        ruffle_wrapper(dest, swf, title)

    if not os.path.exists(os.path.join(dest, "index.html")):
        shutil.rmtree(dest, ignore_errors=True)
        return False, "no index.html at the top level after copy"

    return True, f"web/{slug(title)}"


# ---------------------------------------------------------------- output
def report_line(title, engine, verdict, detail, action=""):
    colour = {"web": GRN, "convert": YEL, "cart": CYN, "dos": CYN, "no": RED}[verdict]
    tag    = {"web": "PLAYABLE", "convert": "CONVERT", "cart": "EMULATED",
              "dos": "DOS", "no": "NO"}[verdict]
    print(f"  {title[:34]:<34} {DIM}{engine[:22]:<22}{END} {colour}{tag:<9}{END} {detail}")
    if action:
        print(f"  {'':<34} {'':<22} {DIM}→ {action}{END}")


RENPY_STEPS = f"""
{BOLD}Converting a Ren'Py title{END}
  Ren'Py's web export needs the launcher GUI — it does not build correctly
  from a headless command line. Once per title, on a desktop:

    1. Install the Ren'Py SDK (renpy.org), version 8.x
    2. Put the folder in your Ren'Py projects directory
    3. Open the launcher and select it
    4. Build Distributions → tick {BOLD}Web{END} → Build
       (the first run downloads web support — let it)
    5. You get a folder ending in -dists containing the web build
    6. Copy that folder here and run detect on it again

  Expect a large first load and heavy browser memory use. Older titles may
  need an SDK version close to the one they were built with.
"""


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    check_only = "--check" in args
    do_all     = "--all" in args
    args = [a for a in args if not a.startswith("--")]
    if not args:
        print("give me a path")
        sys.exit(1)
    target = os.path.abspath(args[0])
    name   = args[1] if len(args) > 1 else None

    if not os.path.exists(target):
        print(f"{RED}nothing at {target}{END}")
        sys.exit(1)

    items = []
    if do_all and os.path.isdir(target):
        for entry in sorted(os.listdir(target)):
            p = os.path.join(target, entry)
            if os.path.isdir(p) or os.path.splitext(p)[1].lower() in (".zip", ".7z", ".rar"):
                items.append(p)
    else:
        items = [target]

    print(f"\n{BOLD}Checking {len(items)} item(s){END}\n")
    saw_renpy = False
    imported = 0

    for path in items:
        cleanup = None
        root = path
        if os.path.isfile(path):
            root, cleanup = unpack(path)
            if not root:
                report_line(os.path.basename(path), "archive", "no",
                            "couldn't extract — unsupported or corrupt")
                continue

        engine, verdict, detail = detect(root)
        title = name or os.path.basename(path.rstrip("/"))
        title = os.path.splitext(title)[0]

        action = ""
        if verdict == "web" and not check_only:
            ok, where = do_import(root, engine, detail, title)
            action = f"imported to {where}" if ok else f"not imported: {where}"
            if ok:
                imported += 1
        elif verdict == "web":
            action = "would import to web/" + slug(title)
        elif verdict == "cart":
            action = f"file it under titles/{detail}/"
        elif verdict == "dos":
            action = "put the archive in titles/dos/"
        elif verdict == "convert":
            saw_renpy = True
            action = "see the note below"
        else:
            action = "stream it from a machine with a GPU"

        report_line(title, engine, verdict, detail, action)
        if cleanup:
            shutil.rmtree(cleanup, ignore_errors=True)

    if saw_renpy:
        print(RENPY_STEPS)

    if imported:
        subprocess.run(["python3", os.path.join(os.path.dirname(__file__), "index.py")],
                       stdout=subprocess.DEVNULL, check=False)
        print(f"\n{GRN}{imported} title(s) added.{END} Refresh the site to see them.\n")
    elif check_only:
        print(f"\n{DIM}Check only — nothing was copied.{END}\n")
    else:
        print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
