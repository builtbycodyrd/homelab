"""Vector Bridge — server-side API for voice commands from an Anki Vector robot.

Design constraints, in order of importance:

  1. Every request is authenticated with a shared token.
  2. Read-only operations are a fixed dispatch table. There is no path from
     spoken words to a shell.
  3. Write operations come from a named allowlist file with fixed parameters,
     and are never assembled from speech.
  4. Anything that changes state is submitted to an approval gate; this service
     only reports the result.
  5. Unrecognized input fails closed.

Configuration comes from the environment (see .env.example). Only the token is
required; every optional piece degrades to a spoken explanation rather than an
error.
"""
import os
import shutil
import threading

import requests
import yaml
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

TOKEN         = os.environ["VECTOR_TOKEN"]
WIREPOD_URL   = os.environ.get("WIREPOD_URL", "").rstrip("/")
VECTOR_SERIAL = os.environ.get("VECTOR_SERIAL", "")
KUMA_URL      = os.environ.get("KUMA_URL", "").rstrip("/")
KUMA_KEY      = os.environ.get("KUMA_KEY", "")
APPROVAL_URL  = os.environ.get("APPROVAL_URL", "").rstrip("/")
OPS_FILE      = os.environ.get("OPS_FILE", "/opt/vector-bridge/ops.yaml")

app = FastAPI(title="Vector Bridge")


class Command(BaseModel):
    command: str


# ---------- speaking back through the robot's local API ----------------------

def speak(text: str) -> None:
    """Make the robot say something.

    Speaking requires taking behavior control away from the robot's own
    firmware. The release call is in a finally block: if the speak request
    fails or times out, the robot must not be left frozen and unresponsive.
    """
    if not (WIREPOD_URL and VECTOR_SERIAL):
        return
    base = f"{WIREPOD_URL}/api-sdk"
    p = {"serial": VECTOR_SERIAL}
    try:
        requests.get(f"{base}/assume_behavior_control",
                     params={**p, "priority": "high"}, timeout=5)
        requests.get(f"{base}/say_text", params={**p, "text": text}, timeout=20)
    except requests.RequestException:
        pass
    finally:
        try:
            requests.get(f"{base}/release_behavior_control", params=p, timeout=5)
        except requests.RequestException:
            pass


# ---------- read-only handlers ----------------------------------------------

def op_server_status() -> str:
    """Summarize service health from the uptime monitor."""
    if not KUMA_URL:
        return "Status checks aren't set up."
    try:
        r = requests.get(f"{KUMA_URL}/api/status-page/heartbeat/server",
                         headers={"Authorization": f"Bearer {KUMA_KEY}"},
                         timeout=6)
        r.raise_for_status()
        beats = r.json().get("heartbeatList", {})
        total = len(beats)
        down = sum(1 for v in beats.values() if v and v[-1].get("status") == 0)
    except Exception:
        return "I couldn't reach the monitor."

    if total == 0:
        return "No services are being monitored."
    if down == 0:
        return f"All {total} services are up."
    return f"{down} of {total} services are down."


def op_disk_space() -> str:
    usage = shutil.disk_usage("/")
    free_gb = usage.free / 1_000_000_000
    pct = usage.free / usage.total * 100
    return f"{free_gb:.0f} gigabytes free, about {pct:.0f} percent."


def op_uptime() -> str:
    try:
        with open("/proc/uptime") as fh:
            seconds = float(fh.read().split()[0])
    except OSError:
        return "I couldn't read the uptime."
    days, rem = divmod(int(seconds), 86400)
    hours = rem // 3600
    if days:
        return f"Up {days} days and {hours} hours."
    return f"Up {hours} hours."


def op_load() -> str:
    one, _, _ = os.getloadavg()
    return f"Load average is {one:.1f}."


READ_ONLY = {
    "server status": op_server_status,
    "status":        op_server_status,
    "disk space":    op_disk_space,
    "uptime":        op_uptime,
    "load":          op_load,
}


# ---------- write operations -------------------------------------------------

def load_write_ops() -> dict:
    """Named operations only — never build these from speech."""
    try:
        with open(OPS_FILE) as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


def submit_for_approval(name: str, spec: dict) -> None:
    """Hand off to the approval gate; speak the result when it comes back.

    Runs on a background thread so a slow approval doesn't hold the request
    open — the robot speaks the outcome whenever it arrives.
    """
    def run():
        try:
            r = requests.post(
                APPROVAL_URL,
                json={
                    "source": "vector",
                    "description": spec.get("description", name),
                    "plan": spec.get("plan", []),
                },
                timeout=300,
            )
            r.raise_for_status()
            speak(r.json().get("speak", "That's done."))
        except requests.RequestException:
            speak("The server didn't get back to me.")

    threading.Thread(target=run, daemon=True).start()


# ---------- routes -----------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True}


@app.post("/vector/command")
def vector_command(body: Command, x_vector_token: str = Header(default="")):
    if x_vector_token != TOKEN:
        raise HTTPException(status_code=401, detail="bad token")

    cmd = " ".join(body.command.lower().split())

    handler = READ_ONLY.get(cmd)
    if handler:
        return {"speak": handler()}

    spec = load_write_ops().get(cmd)
    if spec:
        if not APPROVAL_URL:
            return {"speak": "Write operations aren't enabled."}
        submit_for_approval(cmd, spec)
        return {"speak": "Asking for approval."}

    # Fail closed: no fuzzy matching, no "did you mean".
    return {"speak": "I don't know that one."}
