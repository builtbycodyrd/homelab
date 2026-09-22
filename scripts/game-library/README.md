# Game Library — Core Modules

Three modules from the library pipeline. The full write-up, including the drop-folder watchers and the artwork fetcher, is in [../../docs/game-library.md](../../docs/game-library.md).

| File | What it does |
|---|---|
| `detect.py` | Classification and import. Decides what a folder or archive actually is, and installs browser-playable titles into the library. |
| `index.py` | Walks the library and writes the JSON index the web UI reads. |
| `sessionauth.py` | Session authentication service the web server defers to for protected paths. |

Every path comes from an environment variable, so these run anywhere:

```bash
export LIBRARY_ROOT=/srv/library
python3 detect.py --check /path/to/something     # report only
python3 detect.py /path/to/something             # import it
python3 index.py                                 # rebuild the index
```

## detect.py

One function answers one question — what can run this? — and returns a verdict every other stage keys off:

| Verdict | Meaning |
|---|---|
| `web` | Runs in a browser as-is (HTML5, Unity WebGL, Godot web export, RPG Maker MV/MZ, Twine, Flash via Ruffle) |
| `convert` | Convertible, but needs a build step the original tooling has to do |
| `cart` | A cartridge or disc image for an emulated platform |
| `dos` | DOS-era or early Windows executable |
| `no` | Nothing here can run in a browser, and here's why |

Notes on the implementation:

- **The walk is depth-limited to two levels.** Deep enough to see past a wrapper folder, shallow enough that a large title doesn't cost a full tree walk.
- **Nothing is executed.** Archives are extracted and files are read. Running an unknown binary to find out what it is isn't something a background service should do.
- **Rejections explain themselves.** Every `no` carries a reason — a Ruby-based engine with no browser runtime is a different problem from a native build that needs a web export from its developer, and knowing which is which saves the next hour.
- **Multi-volume archives need the upstream tool.** The distribution's bundled extractors fail on them, one with an unsupported-method error and one outright. The binary is configurable via `UNRAR_BIN`.

## index.py

The front end reads one JSON file rather than touching the filesystem, so page loads never wait on a directory walk.

- **Platform metadata lives in one table** — emulator core, display label, accent color. Adding a platform is one line.
- **IDs are content-derived and stable,** so the UI can remember a selection across rebuilds.
- **Titles are cleaned up for display:** region and revision tags stripped, underscores normalized.
- **The write is atomic** — temp file plus rename — so the UI never reads a half-written index.

## sessionauth.py

Authentication as a separate service the web server calls before the request ever reaches the content, rather than auth code bolted into the application.

- Sessions in memory only; a restart logs everyone out and leaves nothing to leak.
- PBKDF2-HMAC-SHA256 with a per-credential salt, compared in constant time.
- Sliding idle timeout enforced server-side.
- Fixed delay on failure, which makes online guessing impractical.
- HttpOnly, Secure, SameSite cookies.
- Post-login redirect validated against an allowed prefix — an unvalidated one is an open redirect and a phishing primitive.
- Binds to localhost; only the web server in front of it can reach it.

Wire it up with nginx `auth_request`:

```nginx
location /private/ {
    auth_request     /auth-check;
    error_page 401 = @signin;
    # ... serve the protected content
}

location = /auth-check {
    internal;
    proxy_pass              http://127.0.0.1:8099/check;
    proxy_pass_request_body off;
    proxy_set_header        Content-Length "";
}

location @signin {
    return 302 /auth/?next=$request_uri;
}
```

## What's missing

No tests. `detect()` is pure input-to-verdict logic with no side effects, which makes it the easiest thing here to cover and the first thing I'd write tests for.
