"""Render the Caddy tarpit access log as an HTML leaderboard.

Served at tarpit.rowan.sh (see the vhost in ../Caddyfile). Reads the JSON access
log Caddy writes for the :80 / :443 catch-alls, aggregates per client IP, and
shows who got stuck and for how long.

Deliberately dependency-free (stdlib only) so it runs on a bare python:3-alpine
with no build step or pip install.

Env:
  TARPIT_LOG_GLOB  glob for the log files        (default /logs/tarpit.log*)
  TARPIT_EXCLUDE   comma-separated IPs to hide   (default empty)
  TARPIT_CACHE_TTL seconds between re-parses     (default 30)
  TARPIT_PORT      listen port                   (default 8082)
"""

import collections
import glob
import gzip
import html
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG_GLOB = os.environ.get("TARPIT_LOG_GLOB", "/logs/tarpit.log*")
EXCLUDE = {x.strip() for x in os.environ.get("TARPIT_EXCLUDE", "").split(",") if x.strip()}
CACHE_TTL = float(os.environ.get("TARPIT_CACHE_TTL", "30"))
PORT = int(os.environ.get("TARPIT_PORT", "8082"))

_cache = {"at": 0.0, "html": ""}


def _open(path):
    return gzip.open(path, "rt", errors="replace") if path.endswith(".gz") \
        else open(path, "r", errors="replace")


def load():
    """Parse every log file into a list of hold records.

    A 308 with ~no duration is Caddy's automatic HTTP->HTTPS redirect for a real
    vhost, which shares the :80 server and so lands in this log. Those are not
    tarpit holds and would badly skew the table, so they are dropped.
    """
    rows = []
    for path in sorted(glob.glob(LOG_GLOB)):
        try:
            with _open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    req = d.get("request") or {}
                    ip = req.get("remote_ip") or "?"
                    if ip in EXCLUDE:
                        continue
                    dur = float(d.get("duration") or 0.0)
                    if d.get("status") == 308 and dur < 0.5:
                        continue
                    ua = (req.get("headers", {}).get("User-Agent") or ["-"])[0]
                    rows.append({
                        "ip": ip,
                        "host": req.get("host") or "",
                        "uri": req.get("uri") or "",
                        "dur": dur,
                        "ua": ua,
                        "ts": float(d.get("ts") or 0),
                        "tls": bool(req.get("tls")),
                    })
        except OSError:
            continue
    return rows


def humandur(s):
    s = int(round(s))
    if s >= 3600:
        return "%dh %02dm" % (s // 3600, (s % 3600) // 60)
    if s >= 60:
        return "%dm %02ds" % (s // 60, s % 60)
    return "%ds" % s


def aggregate(rows):
    agg = {}
    for r in rows:
        a = agg.get(r["ip"])
        if a is None:
            a = agg[r["ip"]] = {"ip": r["ip"], "n": 0, "t": 0.0, "tls": 0,
                                "uas": collections.Counter(),
                                "uris": collections.Counter(),
                                "first": r["ts"], "last": r["ts"]}
        a["n"] += 1
        a["t"] += r["dur"]
        a["tls"] += 1 if r["tls"] else 0
        a["uas"][r["ua"]] += 1
        a["uris"][r["uri"]] += 1
        a["first"] = min(a["first"], r["ts"])
        a["last"] = max(a["last"], r["ts"])
    return sorted(agg.values(), key=lambda a: -a["t"])


CSS = """
:root{--bg:#fbfaf8;--fg:#1b1a17;--mut:#6d6a63;--line:#e3e0d8;--card:#fff;--accent:#8a5a2b}
:root:not([data-theme=light]) @media (prefers-color-scheme:dark){}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#14130f;--fg:#ece9e2;--mut:#9a958a;--line:#2b2924;--card:#1c1a16;--accent:#d9a066}}
:root[data-theme=dark]{--bg:#14130f;--fg:#ece9e2;--mut:#9a958a;--line:#2b2924;--card:#1c1a16;--accent:#d9a066}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
 margin:0;padding-block:32px;padding-left:20px;padding-right:20px}
.wrap{max-width:1100px;margin:0 auto}
h1{font-size:1.6rem;margin:0 0 4px;letter-spacing:-.02em}
.sub{color:var(--mut);margin:0 0 24px;font-size:.92rem}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:26px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;flex:1 1 150px}
.card .k{color:var(--mut);font-size:.72rem;text-transform:uppercase;letter-spacing:.07em}
.card .v{font-size:1.5rem;font-weight:600;margin-top:2px;font-variant-numeric:tabular-nums}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}
table{border-collapse:collapse;width:100%;min-width:720px;font-size:.88rem}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);font-weight:600;
 position:sticky;top:0;background:var(--card)}
tr:last-child td{border-bottom:none}
.ip{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;white-space:nowrap}
.t{font-variant-numeric:tabular-nums;white-space:nowrap;font-weight:600;color:var(--accent)}
.n{font-variant-numeric:tabular-nums;color:var(--mut)}
.ua{color:var(--mut);font-size:.82rem;word-break:break-word;max-width:330px}
.paths{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.78rem;word-break:break-all;max-width:230px}
.rank{color:var(--mut);font-variant-numeric:tabular-nums}
footer{color:var(--mut);font-size:.8rem;margin-top:22px;line-height:1.7}
.empty{padding:40px;text-align:center;color:var(--mut)}
@media(max-width:620px){body{padding-block:20px}h1{font-size:1.3rem}}
"""


def render():
    rows = load()
    agg = aggregate(rows)
    total = sum(a["t"] for a in agg)
    holds = len(rows)
    tls = sum(a["tls"] for a in agg)
    longest = max((r["dur"] for r in rows), default=0)

    out = ["<!-- generated by tarpit/stats.py -->",
           "<title>Tarpit</title>", "<meta name=viewport content='width=device-width,initial-scale=1'>",
           "<style>%s</style>" % CSS, "<div class=wrap>",
           "<h1>Tarpit</h1>",
           "<p class=sub>Requests to this host that matched no site get handed to a tarpit "
           "that answers slowly and never finishes. This is who has been stuck in it.</p>"]

    out.append("<div class=cards>")
    for k, v in (("time wasted", humandur(total)), ("connections held", "{:,}".format(holds)),
                 ("distinct clients", "{:,}".format(len(agg))),
                 ("longest single hold", humandur(longest)),
                 ("over https", "{:,}".format(tls))):
        out.append("<div class=card><div class=k>%s</div><div class=v>%s</div></div>"
                   % (html.escape(k), html.escape(v)))
    out.append("</div>")

    if not agg:
        out.append("<div class='tw'><div class=empty>Nothing caught yet.</div></div>")
    else:
        out.append("<div class=tw><table><thead><tr>"
                   "<th>#</th><th>client</th><th>wasted</th><th>hits</th>"
                   "<th>user agent</th><th>paths</th><th>last seen</th>"
                   "</tr></thead><tbody>")
        for i, a in enumerate(agg, 1):
            ua, _ = a["uas"].most_common(1)[0]
            if len(a["uas"]) > 1:
                ua += "  (+%d more)" % (len(a["uas"]) - 1)
            paths = ", ".join(u if c == 1 else "%s x%d" % (u, c)
                              for u, c in a["uris"].most_common(4))
            out.append(
                "<tr><td class=rank>%d</td><td class=ip>%s</td><td class=t>%s</td>"
                "<td class=n>%d</td><td class=ua>%s</td><td class=paths>%s</td>"
                "<td class=n>%s</td></tr>" % (
                    i, html.escape(a["ip"]), html.escape(humandur(a["t"])), a["n"],
                    html.escape(ua or "-"), html.escape(paths or "-"),
                    time.strftime("%d %b %H:%M", time.gmtime(a["last"]))))
        out.append("</tbody></table></div>")

    out.append("<footer>Updated %s UTC &middot; refreshes every %ds &middot; "
               "times are how long each client stayed connected before giving up."
               "</footer></div>" % (time.strftime("%d %b %Y %H:%M:%S", time.gmtime()), int(CACHE_TTL)))
    return "\n".join(out)


def cached_html():
    now = time.time()
    if now - _cache["at"] > CACHE_TTL or not _cache["html"]:
        _cache["html"] = render()
        _cache["at"] = now
    return _cache["html"]


class Handler(BaseHTTPRequestHandler):
    server_version = "tarpit-stats"

    def _respond(self, body, ctype, send_body=True):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=30")
        self.send_header("Refresh", str(int(CACHE_TTL)))
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def _payload(self):
        if self.path.rstrip("/") in ("/healthz",):
            return b"ok", "text/plain; charset=utf-8"
        return cached_html().encode(), "text/html; charset=utf-8"

    def do_GET(self):
        body, ctype = self._payload()
        self._respond(body, ctype)

    # Without this BaseHTTPRequestHandler answers HEAD with 501, which breaks
    # uptime checks and anything that probes before fetching.
    def do_HEAD(self):
        body, ctype = self._payload()
        self._respond(body, ctype, send_body=False)

    def log_message(self, *a):
        pass  # the point of this box is not to log its own dashboard


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
