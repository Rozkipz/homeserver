"""Render the tarpit hold log as an HTML leaderboard.

Served at tarpit.rowan.sh (see the vhost in ../Caddyfile). Aggregates per client
IP and shows who got stuck and for how long.

Reads the log the TARPIT writes (tarpit.py), not Caddy's access log. Caddy cannot
supply the duration: once a response is streaming it logs when the headers are
flushed, so every hold appears there as 0.0000s however long the client actually
stayed — measured a 20s hold logged as 0.0000s. The tarpit holds the socket, so
it is the only thing that knows.

Deliberately dependency-free (stdlib only) so it runs on a bare python:3-alpine
with no build step or pip install.

Env:
  TARPIT_LOG_GLOB  glob for the hold logs        (default /holds/holds.log*)
  TARPIT_EXCLUDE   comma-separated IPs to hide   (default empty)
  TARPIT_MIN_HOLD  ignore holds shorter than this (default 1.0 seconds)
  TARPIT_CACHE_TTL seconds between re-parses     (default 30)
  TARPIT_GEO       set to 0 to disable geolocation lookups
  TARPIT_GEO_CACHE where to persist ip->country   (default /cache/geo-cache.json)
  TARPIT_PORT      listen port                   (default 8082)
"""

import collections
import glob
import gzip
import html
import json
import os
import ipaddress
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Narrow on purpose: "holds.log*" also matches sidecar files like holds.log.preclean,
# and counting a backup alongside the live log silently doubles every figure on the
# page. Only the live log and its numbered rotations.
LOG_GLOB = os.environ.get("TARPIT_LOG_GLOB", "/holds/holds.log")
LOG_GLOB_ROTATED = os.environ.get("TARPIT_LOG_GLOB_ROTATED", "/holds/holds.log.[0-9]*")
EXCLUDE = {x.strip() for x in os.environ.get("TARPIT_EXCLUDE", "").split(",") if x.strip()}
# Banner-grabbers read the headers and hang up in milliseconds. Those are not holds
# in any meaningful sense, and a table full of 0s rows buries the real ones, so they
# are dropped rather than rounded. The count is still reported in the footer.
MIN_HOLD = float(os.environ.get("TARPIT_MIN_HOLD", "1.0"))
CACHE_TTL = float(os.environ.get("TARPIT_CACHE_TTL", "30"))
PORT = int(os.environ.get("TARPIT_PORT", "8082"))

_cache = {"at": 0.0, "html": ""}

GEO_ON = os.environ.get("TARPIT_GEO", "1") != "0"
# NOT under /holds: that is mounted read-only so the dashboard can never corrupt the
# hold log, which also means it cannot write a cache there. Without a writable path
# every render re-queries the geolocation API and burns the rate limit.
GEO_CACHE = os.environ.get("TARPIT_GEO_CACHE", "/cache/geo-cache.json")
_geo = {}          # ip -> {"cc","country","as"}
_geo_loaded = [False]


def flag(cc):
    """Country code -> flag emoji, via regional indicator symbols.

    Derived arithmetically rather than from a lookup table, so there is no data
    file to ship or keep current.
    """
    cc = (cc or "").upper()
    if len(cc) != 2 or not cc.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in cc)


def _geo_load():
    if _geo_loaded[0]:
        return
    _geo_loaded[0] = True
    try:
        with open(GEO_CACHE) as fh:
            _geo.update(json.load(fh))
    except Exception:
        pass


def _geo_save():
    try:
        tmp = GEO_CACHE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(_geo, fh)
        os.replace(tmp, GEO_CACHE)
        os.chmod(GEO_CACHE, 0o666)
    except Exception:
        pass


def geo_lookup(ips):
    """Resolve any IPs we have not seen before, and remember them.

    ip-api's free tier allows 15 batch calls a minute, so results are cached on
    disk and only genuinely new addresses are ever looked up — a steady-state
    render makes no network calls at all. Capped at two batches per render so a
    burst of new scanners cannot stall the page or trip the limit.
    """
    _geo_load()
    todo = [ip for ip in ips if ip not in _geo and _routable(ip)]
    if not GEO_ON or not todo:
        return
    changed = False
    for batch in [todo[i:i + 100] for i in range(0, len(todo), 100)][:2]:
        try:
            req = urllib.request.Request(
                "http://ip-api.com/batch?fields=status,countryCode,country,query,as",
                data=json.dumps(batch).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                for row in json.load(resp):
                    if row.get("status") == "success":
                        _geo[row["query"]] = {"cc": row.get("countryCode", ""),
                                              "country": row.get("country", ""),
                                              "as": row.get("as", "")}
                        changed = True
        except Exception:
            break   # offline or rate-limited: fall back to showing no country
    if changed:
        _geo_save()
_skipped = [0]   # holds below MIN_HOLD, reported in the footer
_private = [0]   # holds from private space (docker gateway), never listed


def _routable(ip):
    """Is this worth asking a geolocation service about?

    Prefix matching is not good enough here: "172.2" catches public 172.235.x as
    well as the private 172.16/12 block, which silently left real hosts unlabelled.
    """
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (a.is_private or a.is_loopback or a.is_link_local
                or a.is_multicast or a.is_reserved or a.is_unspecified)


def _open(path):
    return gzip.open(path, "rt", errors="replace") if path.endswith(".gz") \
        else open(path, "r", errors="replace")


def load():
    """Parse every hold log into a list of records, dropping instant disconnects.

    One line per connection, written by tarpit.py when the client finally lets
    go. No filtering needed: only proxied tarpit traffic reaches the tarpit, so
    unlike Caddy's access log there are no redirects or real-vhost hits mixed in.
    """
    rows = []
    _skipped[0] = 0
    _private[0] = 0
    for path in sorted(set(glob.glob(LOG_GLOB)) | set(glob.glob(LOG_GLOB_ROTATED))):
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
                    ip = d.get("ip") or "?"
                    if ip in EXCLUDE:
                        continue
                    if not _routable(ip):
                        # Private space never belongs on the board: the only source is
                        # 192.168.x, the docker bridge gateway standing in for IPv6
                        # clients whose real address is lost to NAT. Listing the
                        # gateway reads as though our own network were scanning us.
                        _private[0] += 1
                        continue
                    held = float(d.get("held") or 0.0)
                    if held < MIN_HOLD:
                        _skipped[0] += 1
                        continue
                    rows.append({
                        "ip": ip,
                        "host": d.get("host") or "",
                        "uri": d.get("uri") or "",
                        "dur": float(d.get("held") or 0.0),
                        "ua": d.get("ua") or "-",
                        "ts": float(d.get("ts") or 0),
                        "tls": (d.get("proto") or "").lower() == "https",
                    })
        except OSError:
            continue
    return rows


def humandur(s):
    # Banner-grabbers read the headers and hang up in milliseconds; rounding those
    # to a flat "0s" reads like a bug rather than a real (very short) hold.
    if 0 < s < 1:
        return "<1s"
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
                                "uri_t": collections.Counter(),
                                "first": r["ts"], "last": r["ts"]}
        a["n"] += 1
        a["t"] += r["dur"]
        a["tls"] += 1 if r["tls"] else 0
        a["uas"][r["ua"]] += 1
        a["uris"][r["uri"]] += 1
        a["uri_t"][r["uri"]] += r["dur"]
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
.paths{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.78rem;word-break:break-all;max-width:300px;line-height:1.75}
.pn{color:var(--mut);margin-left:4px}
.rank{color:var(--mut);font-variant-numeric:tabular-nums}
.cc{white-space:nowrap;font-size:.84rem}
.fl{font-size:1.05rem;margin-right:3px;vertical-align:-1px}
footer{color:var(--mut);font-size:.8rem;margin-top:22px;line-height:1.7}
.empty{padding:40px;text-align:center;color:var(--mut)}
@media(max-width:620px){body{padding-block:20px}h1{font-size:1.3rem}}
"""


def render():
    rows = load()
    agg = aggregate(rows)
    geo_lookup([a["ip"] for a in agg])
    total = sum(a["t"] for a in agg)
    holds = len(rows)
    tls = sum(a["tls"] for a in agg)
    longest = max((r["dur"] for r in rows), default=0)

    out = ["<!-- generated by tarpit/stats.py -->",
           "<title>Tarpit</title>", "<meta name=viewport content='width=device-width,initial-scale=1'>",
           "<style>%s</style>" % CSS, "<div class=wrap>",
           "<h1>Tarpit</h1>",
           "<p class=sub>Requests to this host that matched no site get handed to a tarpit "
           "that answers slowly and never finishes. This is who has been stuck in it. "
           "Time is the total each client spent held, across every request it made.</p>"]

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
                   "<th>#</th><th>client</th><th>country</th><th>wasted</th><th>hits</th>"
                   "<th>user agent</th><th>endpoints</th><th>last seen</th>"
                   "</tr></thead><tbody>")
        for i, a in enumerate(agg, 1):
            ua, _ = a["uas"].most_common(1)[0]
            if len(a["uas"]) > 1:
                ua += "  (+%d more)" % (len(a["uas"]) - 1)
            # Just the endpoints. Per-endpoint times and hit counts were shown here
            # and repeatedly read as per-hit rather than totals, which made the row
            # look like it did not add up. The only time on the row is now the
            # client's total, which is unambiguous. Ordered by time spent, so the
            # endpoint that cost them most is first even though the figure is not shown.
            paths = ", ".join(html.escape(u or "/") for u, _ in a["uri_t"].most_common(6))
            extra = len(a["uri_t"]) - 6
            if extra > 0:
                paths += " <span class=pn>+%d more</span>" % extra
            g = _geo.get(a["ip"], {})
            cc = g.get("cc", "")
            country = ("<span class=fl>%s</span> %s" % (flag(cc), html.escape(g.get("country") or cc))
                       if cc else "<span class=pn>&mdash;</span>")
            out.append(
                "<tr><td class=rank>%d</td><td class=ip>%s</td><td class=cc>%s</td>"
                "<td class=t>%s</td>"
                "<td class=n>%d</td><td class=ua>%s</td><td class=paths>%s</td>"
                "<td class=n>%s</td></tr>" % (
                    i, html.escape(a["ip"]), country, html.escape(humandur(a["t"])), a["n"],
                    html.escape(ua or "-"), paths or "-",
                    time.strftime("%d %b %H:%M", time.gmtime(a["last"]))))
        out.append("</tbody></table></div>")

    skipped = _skipped[0]
    note = (" &middot; %s connection%s that read the headers and hung up in under %gs "
            "not listed" % ("{:,}".format(skipped), "" if skipped == 1 else "s",
                            MIN_HOLD)) if skipped else ""
    out.append("<footer>Updated %s UTC &middot; refreshes every %ds &middot; "
               "times are how long each client stayed connected before giving up%s."
               "</footer></div>" % (time.strftime("%d %b %Y %H:%M:%S", time.gmtime()),
                                    int(CACHE_TTL), note))
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
