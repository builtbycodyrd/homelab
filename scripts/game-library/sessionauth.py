#!/usr/bin/env python3
"""
sessionauth — session login for a protected section of the site.

The web server asks this service one question per request — is this allowed? —
and enforces the answer itself (nginx auth_request). The application behind it
never sees an unauthenticated request and contains no auth code of its own.

Design decisions:

  * Sessions live in memory only. A restart logs everyone out. There is no
    session store to leak and no stale token that outlives a reboot.
  * Passwords are salted and stretched with PBKDF2-HMAC-SHA256, and compared
    with a constant-time function so a wrong guess leaks nothing by timing.
  * The idle timeout is enforced server-side on a sliding window, not by the
    client, and it is configurable.
  * A failed attempt costs a fixed delay, which makes online guessing slow.
  * Cookies are HttpOnly, Secure and SameSite=Lax, so script can't read them
    and they don't ride along on cross-site requests.
  * The post-login redirect is validated against an allowed prefix — an open
    redirect here would be a phishing primitive.
  * It binds to localhost. Only the web server in front of it can reach it.

Environment:

    AUTH_CRED_FILE   JSON file: {"salt": "<hex>", "hash": "<hex>"}
    AUTH_CONF_FILE   JSON file: {"idle_minutes": 15}
    AUTH_PORT        listen port (default 8099)
    AUTH_PREFIX      allowed redirect prefix (default "/private")

Create a credential file with:

    python3 -c 'import os,json,hashlib;s=os.urandom(16);\
pw=input("password: ").encode();\
print(json.dumps({"salt":s.hex(),"hash":hashlib.pbkdf2_hmac("sha256",pw,s,200_000).hex()}))'
"""
import os, json, time, hmac, hashlib, secrets, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CRED   = os.environ.get("AUTH_CRED_FILE", "./auth-credential.json")
CONF   = os.environ.get("AUTH_CONF_FILE", "./auth-config.json")
PORT   = int(os.environ.get("AUTH_PORT", "8099"))
PREFIX = os.environ.get("AUTH_PREFIX", "/private")
COOKIE = "session"

_sessions = {}          # token -> last seen (epoch)
_lock = threading.Lock()


def conf():
    try:
        with open(CONF) as f:
            return json.load(f)
    except Exception:
        return {"idle_minutes": 15}


def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000).hex()


def check_pw(pw):
    try:
        with open(CRED) as f:
            d = json.load(f)
        return hmac.compare_digest(
            hash_pw(pw, bytes.fromhex(d["salt"])), d["hash"])
    except Exception:
        return False


def valid(token):
    """True if the token is live. Each check slides the idle window forward."""
    if not token:
        return False
    idle = conf().get("idle_minutes", 15) * 60
    now = time.time()
    with _lock:
        seen = _sessions.get(token)
        if not seen or now - seen > idle:
            _sessions.pop(token, None)
            return False
        _sessions[token] = now
        return True


PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in</title>
<style>
:root{--bg:#17161a;--field:#211f25;--edge:#2e2b34;--fg:#e6e1d5;
      --dim:#9d978a;--accent:#d5623f}
*{box-sizing:border-box}
html,body{margin:0;height:100%%;background:var(--bg);color:var(--fg);
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  display:grid;place-items:center}
form{width:min(340px,90vw);text-align:left}
h1{font-size:44px;margin:0 0 4px;text-transform:uppercase;
  letter-spacing:-.02em;line-height:1}
h1 span{color:var(--accent)}
p{font-size:11px;color:var(--dim);letter-spacing:.12em;text-transform:uppercase;
  margin:0 0 22px}
input{width:100%%;background:var(--field);border:1px solid var(--edge);
  border-radius:2px;color:var(--fg);font:inherit;font-size:14px;
  padding:11px 13px;margin-bottom:10px}
input:focus{outline:2px solid var(--accent);outline-offset:1px}
button{width:100%%;background:var(--fg);border:0;border-radius:2px;
  color:#14131a;font:inherit;font-size:12px;letter-spacing:.12em;
  text-transform:uppercase;padding:12px;cursor:pointer}
button:hover{background:#fff}
.err{color:var(--accent);font-size:11px;margin:0 0 12px;min-height:14px}
.back{display:block;margin-top:18px;font-size:11px;color:var(--dim);
  text-decoration:none;letter-spacing:.1em;text-transform:uppercase}
.back:hover{color:var(--fg)}
</style></head><body>
<form method="POST" action="/auth/login">
  <h1>Sign in<span>.</span></h1>
  <p>%(sub)s</p>
  <div class="err">%(err)s</div>
  <input type="hidden" name="next" value="%(next)s">
  <input type="password" name="password" placeholder="Password" autofocus
         autocomplete="off" required>
  <button type="submit">Unlock</button>
  <a class="back" href="/">&larr; Back</a>
</form></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _cookie(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return None

    def _send(self, code, body=b"", ctype="text/html; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)

        # The endpoint the web server calls for every protected request.
        if path == "/check":
            if valid(self._cookie()):
                self._send(200, b"ok", "text/plain")
            else:
                self._send(401, b"no", "text/plain")
            return

        if path == "/logout":
            with _lock:
                _sessions.pop(self._cookie(), None)
            self._send(302, b"", extra=[
                ("Location", "/auth/"),
                ("Set-Cookie",
                 f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")])
            return

        mins = conf().get("idle_minutes", 15)
        body = (PAGE % {
            "err": "",
            "next": qs.get("next", [PREFIX + "/"])[0],
            "sub": f"locks after {mins} minutes idle",
        }).encode()
        self._send(200, body)

    def do_POST(self):
        if urlparse(self.path).path != "/login":
            self._send(404, b"")
            return

        n = int(self.headers.get("Content-Length", 0) or 0)
        data = parse_qs(self.rfile.read(n).decode())
        pw = data.get("password", [""])[0]

        # Never redirect outside the protected area — an open redirect here
        # would let someone send a login link that lands anywhere.
        nxt = data.get("next", [PREFIX + "/"])[0]
        if not nxt.startswith(PREFIX):
            nxt = PREFIX + "/"

        if check_pw(pw):
            tok = secrets.token_urlsafe(32)
            with _lock:
                _sessions[tok] = time.time()
            self._send(302, b"", extra=[
                ("Location", nxt),
                ("Set-Cookie",
                 f"{COOKIE}={tok}; Path=/; HttpOnly; Secure; SameSite=Lax")])
        else:
            time.sleep(1.5)     # fixed cost per failure slows guessing
            mins = conf().get("idle_minutes", 15)
            body = (PAGE % {"err": "Wrong password.", "next": nxt,
                            "sub": f"locks after {mins} minutes idle"}).encode()
            self._send(401, body)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
