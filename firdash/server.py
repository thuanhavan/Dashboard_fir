import argparse
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

from .collectors import USER
from .render import CACHE_TTL, build

_cache = {}


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

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if urlsplit(self.path).path != "/":
                self.send_error(404)
                return
            query = urlsplit(self.path).query or f"days={a.days}"
            body = page(query).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    port = srv.server_address[1]
    host = socket.gethostname()
    print(f"Dashboard on {host}:127.0.0.1:{port}\n")
    print("VS Code Remote: open the PORTS tab, forward", port, "and click the link.")
    print("Plain SSH from your laptop (must reach this same login node):")
    print(f"  ssh -N -L {port}:localhost:{port} -J {USER}@fir.alliancecan.ca {USER}@{host.split('.')[0]}")
    print(f"  then open http://localhost:{port}\n")
    print("Ctrl-C to stop.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
