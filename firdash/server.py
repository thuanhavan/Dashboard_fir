import argparse
import hashlib
import hmac
import os
import secrets
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .collectors import USER
from .render import CACHE_TTL, build

PASSWORD_FILE = Path.home() / ".config" / "firdash" / "password"
_cache = {}

LOGIN = """<!doctype html><html><head><meta charset="utf-8"><title>Fir dashboard</title>
<style>body{{margin:0;height:100vh;display:grid;place-items:center;background:#0b1424;color:#f0efec;
font-family:system-ui,"Segoe UI",sans-serif}} form{{background:#13213a;padding:28px 32px;border-radius:10px;
border:1px solid rgba(255,255,255,.1);display:flex;flex-direction:column;gap:12px;width:300px}}
input,button{{padding:9px 10px;border-radius:6px;border:1px solid rgba(255,255,255,.15);font-size:14px}}
input{{background:#0b1424;color:#f0efec}} button{{background:#3987e5;color:#fff;border:0;cursor:pointer}}
.err{{color:#e66767;font-size:13px}}</style></head><body>
<form method="post" action="/login"><b>▲ Fir dashboard</b>{err}
<input type="password" name="password" placeholder="Password" autofocus required>
<button>Sign in</button></form></body></html>"""


def load_password():
    if os.environ.get("FIRDASH_PASSWORD"):
        return os.environ["FIRDASH_PASSWORD"]
    if not PASSWORD_FILE.exists():
        PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(PASSWORD_FILE.parent, 0o700)
        fd = os.open(PASSWORD_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(12) + "\n")
    os.chmod(PASSWORD_FILE, 0o600)
    return PASSWORD_FILE.read_text().strip()


def page(query):
    t, body = _cache.get(query, (0, ""))
    if time.time() - t > CACHE_TTL:
        body = build(dict(parse_qsl(query)))
        _cache[query] = (time.time(), body)
    return body


def main():
    ap = argparse.ArgumentParser(description="Fir quota/job dashboard")
    ap.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    ap.add_argument("--days", type=int, default=7, help="default sacct history window")
    ap.add_argument("--html", metavar="FILE", help="write a static HTML file and exit")
    a = ap.parse_args()

    if a.html:
        with open(a.html, "w") as f:
            f.write(build({"days": str(a.days)}))
        print(f"wrote {a.html}")
        return

    password = load_password()
    session = secrets.token_urlsafe(32)
    cookie_name = f"firdash_{hashlib.sha256(USER.encode()).hexdigest()[:8]}"

    class H(BaseHTTPRequestHandler):
        def send(self, code, body, headers=()):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def authed(self):
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == cookie_name and hmac.compare_digest(v, session):
                    return True
            return False

        def do_POST(self):
            if urlsplit(self.path).path != "/login":
                self.send_error(404)
                return
            n = min(int(self.headers.get("Content-Length") or 0), 4096)
            form = dict(parse_qsl(self.rfile.read(n).decode(errors="replace")))
            if hmac.compare_digest(form.get("password", "").encode(), password.encode()):
                self.send(303, "", [("Location", "/"),
                                    ("Set-Cookie", f"{cookie_name}={session}; HttpOnly; SameSite=Strict; Path=/")])
            else:
                time.sleep(1)
                self.send(401, LOGIN.format(err='<span class="err">Wrong password</span>'))

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/logout":
                self.send(303, "", [("Location", "/"), ("Set-Cookie", f"{cookie_name}=; Max-Age=0; Path=/")])
                return
            if path != "/":
                self.send_error(404)
                return
            if not self.authed():
                self.send(401, LOGIN.format(err=""))
                return
            self.send(200, page(urlsplit(self.path).query or f"days={a.days}"))

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    port = srv.server_address[1]
    host = socket.gethostname()
    print(f"Dashboard on {host}:127.0.0.1:{port}  (password in {PASSWORD_FILE})\n")
    print("VS Code Remote: open the PORTS tab, forward", port, "and click the link.")
    print("Plain SSH from your laptop (must reach this same login node):")
    print(f"  ssh -N -L {port}:localhost:{port} -J {USER}@fir.alliancecan.ca {USER}@{host.split('.')[0]}")
    print(f"  then open http://localhost:{port}\n")
    print("Ctrl-C to stop.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
