#!/usr/bin/env bash
# Is everything ready? One screen, under ten seconds, run on serverpi.
#
#   bash tools/preflight.sh            # before filming, again before walking on stage
#
# Every line is PASS or FAIL with the reason. Exit status is the number of
# FAILs. Nothing here changes state except the one audio render it asks the
# engine for, which also proves the renderer to the heartbeat.
#
# Reads the service's real environment (/proc/<pid>/environ) so "the key is
# set" means set for the process that matters, not for this shell. Falls back
# to backend/.env when that is not readable.

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
SERVICE="${SENTINEL_SERVICE:-sentinel}"
PORT="${SENTINEL_PORT:-8765}"
PY="${SENTINEL_PYTHON:-$BACKEND/.venv/bin/python}"; [[ -x "$PY" ]] || PY="$(command -v python3 || command -v python)"

fails=0
pass() { printf 'PASS  %-16s %s\n' "$1" "$2"; }
fail() { printf 'FAIL  %-16s %s\n' "$1" "$2"; fails=$((fails + 1)); }

# --- 1. service ---------------------------------------------------------------
active="$(systemctl is-active "$SERVICE" 2>/dev/null || true)"
if [[ "$active" == "active" ]]; then
  pass service "$SERVICE.service active since $(systemctl show -p ActiveEnterTimestamp --value "$SERVICE" 2>/dev/null | cut -d' ' -f2-3)"
else
  fail service "$SERVICE.service is ${active:-not installed}"
fi

# --- service environment ------------------------------------------------------
# What the engine process actually has. The .env fallback is second best: the
# file can be edited after the service started.
pid="$(systemctl show -p MainPID --value "$SERVICE" 2>/dev/null || echo 0)"
env_src="/proc/$pid/environ"
if [[ "$pid" != "0" && -r "$env_src" ]]; then
  svc_env="$(tr '\0' '\n' < "$env_src")"; env_from="pid $pid"
elif [[ -r "$BACKEND/.env" ]]; then
  svc_env="$(cat "$BACKEND/.env")"; env_from="backend/.env (process env not readable)"
else
  svc_env=""; env_from="none"
fi
svc() { printf '%s\n' "$svc_env" | sed -n "s/^$1=//p" | tail -n1; }
export SENTINEL_TOKEN="$(svc SENTINEL_TOKEN)"
export ANTHROPIC_API_KEY="$(svc ANTHROPIC_API_KEY)"
export ELEVENLABS_API_KEY="$(svc ELEVENLABS_API_KEY)"
export OLLAMA_BASE_URL="$(svc OLLAMA_BASE_URL)"; export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
export SENTINEL_ASSETS_FILE="$(svc SENTINEL_ASSETS_FILE)"
export SENTINEL_DB="$BACKEND/sentinel.db"
export SENTINEL_PORT="$PORT"
export SENTINEL_CERT="$BACKEND/certs/cert.pem"
export SENTINEL_ROOT="$ROOT"

if [[ -n "$ANTHROPIC_API_KEY" ]]; then
  pass anthropic_key "ANTHROPIC_API_KEY set (${#ANTHROPIC_API_KEY} chars, from $env_from)"
else
  fail anthropic_key "ANTHROPIC_API_KEY unset in service env ($env_from): claude will report unreachable"
fi

# --- 2..9. everything that needs a socket, in one interpreter ------------------
cd "$BACKEND" || { fail python "cannot cd $BACKEND"; exit $fails; }
"$PY" - <<'PY'
import hashlib, json, os, socket, sqlite3, ssl, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

fails = 0
def pass_(k, m): print(f"PASS  {k:<16} {m}")
def fail(k, m):
    global fails; fails += 1; print(f"FAIL  {k:<16} {m}")

port, token = int(os.environ["SENTINEL_PORT"]), os.environ.get("SENTINEL_TOKEN", "")
cert = os.environ["SENTINEL_CERT"]
scheme = "https" if os.path.exists(cert) else "http"
base = f"{scheme}://127.0.0.1:{port}"
headers = {"Authorization": f"Bearer {token}"} if token else {}
ctx = None
if scheme == "https":
    ctx = ssl.create_default_context(cafile=cert)
    ctx.check_hostname = False  # the pin is the phone's job; here we want the socket

def get(url, timeout=3.0, raw=False):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        data = r.read()
    return data if raw else json.loads(data)

# 2. websocket hello
try:
    import asyncio, websockets
    async def hello():
        ws_url = f"{'wss' if ctx else 'ws'}://127.0.0.1:{port}/ws"
        kw = {"ssl": ctx} if ctx else {}
        try:
            conn = websockets.connect(ws_url, additional_headers=headers, open_timeout=3, **kw)
        except TypeError:  # websockets < 13 spelling
            conn = websockets.connect(ws_url, extra_headers=headers, open_timeout=3, **kw)
        async with conn as ws:
            return json.loads(await asyncio.wait_for(ws.recv(), 3))
    fr = asyncio.run(hello())
    if fr.get("type") == "hello":
        d = fr["data"]
        pass_("websocket", f"{'wss' if ctx else 'ws'}://:{port}/ws hello server_id={d.get('server_id')} "
                           f"contract={d.get('contract_version')} last_event_seq={d.get('last_event_seq')}")
    else:
        fail("websocket", f"first frame was {fr.get('type')!r}, not hello")
except Exception as e:
    fail("websocket", f"{type(e).__name__}: {str(e)[:80]}")

# 3. elevenlabs key + renderer (renderer confirmed by the render below)
eleven = bool(os.environ.get("ELEVENLABS_API_KEY"))

# 4. ollama
ollama = os.environ["OLLAMA_BASE_URL"].rstrip("/")
try:
    sys.path.insert(0, os.getcwd())
    from llm import load_prompt_spec
    want = load_prompt_spec().model
    ps = get(f"{ollama}/api/ps")
    loaded = {m.get("name") or m.get("model"): m for m in ps.get("models", [])}
    m = loaded.get(want) or next((v for k, v in loaded.items() if k and k.split(":")[0] == want.split(":")[0]), None)
    if m is None:
        fail("ollama", f"{ollama} up but {want} not loaded (loaded: {', '.join(loaded) or 'nothing'}); "
                       f"first triage will pay the load time")
    else:
        size, vram = m.get("size") or 0, m.get("size_vram") or 0
        gpu = round(100 * vram / size) if size else 0
        split = "100% GPU" if gpu >= 100 else f"{gpu}% GPU / {100 - gpu}% CPU"
        exp = m.get("expires_at", "")[:19].replace("T", " ")
        pass_("ollama", f"{ollama} {want} loaded, {split}, {size / 1e9:.1f} GB, unloads {exp or '?'}")
except Exception as e:
    fail("ollama", f"{ollama}: {type(e).__name__}: {str(e)[:70]}")

# 5. audio round trip: request a render, fetch the URL, digest, non-silent mp3
engine = None
try:
    t0 = time.monotonic()
    body = get(f"{base}/debug/tts", timeout=20)
    engine, ref = body["engine"], body["audio"]
    data = get(ref["url"], timeout=5, raw=True)
    sha = hashlib.sha256(data).hexdigest()
    silent = os.path.join("static", "silence-1s.mp3")
    silent_sha = hashlib.sha256(open(silent, "rb").read()).hexdigest() if os.path.exists(silent) else ""
    is_mp3 = data[:3] == b"ID3" or (len(data) > 4 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
    nonzero = sum(1 for b in data if b) / max(len(data), 1)
    problems = []
    if sha != ref["sha256"]: problems.append("sha256 mismatch")
    if not is_mp3: problems.append("not an mp3")
    if sha == silent_sha or nonzero < 0.05: problems.append("silent clip")
    if problems:
        fail("audio", f"{engine}: {', '.join(problems)} ({len(data)} bytes from {ref['url']})")
    else:
        pass_("audio", f"{engine} rendered {ref['duration_ms']} ms, {len(data)} bytes, sha256 ok, "
                       f"{nonzero:.0%} non-zero, {time.monotonic() - t0:.1f}s round trip")
except HTTPError as e:
    fail("audio", f"/debug/tts HTTP {e.code}: {e.read().decode(errors='replace')[:80]}")
except Exception as e:
    fail("audio", f"{type(e).__name__}: {str(e)[:80]}")

if eleven and engine == "elevenlabs":
    pass_("elevenlabs_key", "ELEVENLABS_API_KEY set, renderer=elevenlabs")
elif eleven:
    fail("elevenlabs_key", f"ELEVENLABS_API_KEY set but renderer={engine or 'unknown'}")
else:
    fail("elevenlabs_key", f"ELEVENLABS_API_KEY unset, renderer={engine or 'unknown'} (espeak fallback = degraded)")

# 6. last heartbeat's pipeline block, verbatim
try:
    st = get(f"{base}/debug/state")
    p = st["pipeline"]
    bad = [k for k, v in p.items() if v != "ok"]
    (fail if "unreachable" in p.values() else pass_)("heartbeat", json.dumps(p, separators=(", ", ": ")) +
                                                     (f"  <- {', '.join(bad)}" if bad else ""))
except Exception as e:
    fail("heartbeat", f"/debug/state: {type(e).__name__}: {str(e)[:80]}")

# 7. sentinel.db writable + events in the last 5 minutes
db = os.environ["SENTINEL_DB"]
try:
    if not os.path.exists(db):
        raise FileNotFoundError(db)
    con = sqlite3.connect(db, timeout=2)
    con.execute("BEGIN IMMEDIATE"); con.execute("ROLLBACK")  # proves the write lock, changes nothing
    since = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
    recent = con.execute("SELECT COUNT(*) FROM events WHERE created_at >= ?", (since,)).fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    con.close()
    pass_("database", f"{os.path.relpath(db, os.environ['SENTINEL_ROOT'])} writable, {recent} events in last 5 min ({total} total)")
except Exception as e:
    fail("database", f"{db}: {type(e).__name__}: {str(e)[:70]}")

# 8. protected assets + whether --live would be refused
try:
    from assets import AssetRegistry, ProtectedListEmpty
    path = os.environ.get("SENTINEL_ASSETS_FILE") or "protected_assets.yaml"
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    reg = AssetRegistry.load(path, auto_detect=False, env=False)
    assumed = sum(1 for line in open(path, encoding="utf-8") if "ASSUMED" in line and not line.lstrip().startswith("#"))
    try:
        reg.require_configured_for_live(); live = "--live would start"
    except ProtectedListEmpty:
        live = "--live would be refused (list empty)"
    pass_("assets", f"{os.path.relpath(path, os.environ['SENTINEL_ROOT'])}: {len(reg.configured)} configured, "
                    f"{assumed} marked ASSUMED; {live}")
except Exception as e:
    fail("assets", f"{type(e).__name__}: {str(e)[:80]}")

sys.exit(fails)
PY
fails=$((fails + $?))

if [[ $fails -eq 0 ]]; then echo "READY: all checks passed"; else echo "NOT READY: $fails check(s) failed"; fi
exit $fails
