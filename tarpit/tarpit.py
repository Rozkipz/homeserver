"""HTTP tarpit: answer immediately, then never finish.

Sends a COMPLETE header block at once so the client believes it reached a live
server, then dribbles chunked body forever. The complete header block is the
point — an earlier version never finished its headers, so Caddy buffered the lot
and the client got silence, and scanners hang up quickly on silence.

It also records its own holds. Caddy cannot do that for us: once the response is
streaming, Caddy writes its access-log entry when the headers are flushed, so
every hold lands in that log as duration=0.0000s no matter how long the client
actually stayed. This process is the thing holding the socket, so it is the only
thing that knows the real figure. Measured here, written on close.

The client IP comes from X-Forwarded-For, which Caddy sets — the socket peer here
is only ever Caddy's address on the docker bridge.

Stdlib only, so it runs on a bare python:3-alpine with no build step.

Env:
  TARPIT_DELAY      seconds between body chunks      (default 10)
  TARPIT_MAX_CONN   concurrent connections cap       (default 2000)
  TARPIT_MAX_HOLD   hard cap on a single hold, secs  (default 86400)
  TARPIT_PORT       listen port                      (default 8081)
  TARPIT_HOLD_LOG   where to append hold records     (default /holds/holds.log)
  TARPIT_LOG_MAX    rotate the hold log past N bytes (default 33554432)
  TARPIT_LOG_MODE   octal mode for the hold log      (default 0666)
"""

import asyncio
import json
import os
import random
import string
import time

DELAY = float(os.environ.get("TARPIT_DELAY", "10"))
MAXCONN = int(os.environ.get("TARPIT_MAX_CONN", "2000"))
MAXHOLD = float(os.environ.get("TARPIT_MAX_HOLD", "86400"))
PORT = int(os.environ.get("TARPIT_PORT", "8081"))
HOLD_LOG = os.environ.get("TARPIT_HOLD_LOG", "/holds/holds.log")
LOG_MAX = int(os.environ.get("TARPIT_LOG_MAX", str(32 * 1024 * 1024)))
# This container runs as root, so anything it creates defaults to root:root 0644 and
# the host account cannot touch it. Kept deliberately permissive so the log stays
# readable and writable from the host without sudo, and so rotation cannot silently
# restore a restrictive mode. It holds scanner metadata, nothing sensitive.
LOG_MODE = int(os.environ.get("TARPIT_LOG_MODE", "0666"), 8)

HEAD = ("HTTP/1.1 200 OK\r\n"
        "Server: nginx\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "Transfer-Encoding: chunked\r\n"
        "Cache-Control: no-store\r\n"
        "\r\n")

live = 0


def chunk(s):
    b = s.encode()
    return b"%x\r\n%s\r\n" % (len(b), b)


def parse_request(raw):
    """Pull the bits worth logging out of the raw request head."""
    out = {"method": "", "uri": "", "host": "", "ua": "", "ip": "", "proto": ""}
    try:
        text = raw.decode("latin-1", "replace")
        lines = text.split("\r\n")
        if lines and lines[0]:
            parts = lines[0].split()
            if len(parts) >= 2:
                out["method"], out["uri"] = parts[0][:16], parts[1][:200]
        for line in lines[1:]:
            if not line:
                break
            name, _, value = line.partition(":")
            key, value = name.strip().lower(), value.strip()
            if key == "host":
                out["host"] = value[:120]
            elif key == "user-agent":
                out["ua"] = value[:250]
            elif key == "x-forwarded-for":
                # Caddy appends; the original client is the first entry.
                out["ip"] = value.split(",")[0].strip()[:60]
            elif key == "x-forwarded-proto":
                out["proto"] = value[:10]
    except Exception:
        pass
    return out


def record(info, held, peer):
    """Append one hold record. Never let logging break a connection."""
    try:
        try:
            if os.path.getsize(HOLD_LOG) > LOG_MAX:
                os.replace(HOLD_LOG, HOLD_LOG + ".1")
                try:
                    os.chmod(HOLD_LOG + ".1", LOG_MODE)
                except OSError:
                    pass
        except OSError:
            pass
        row = {"ts": time.time(), "held": round(held, 3),
               "ip": info.get("ip") or peer, "host": info.get("host", ""),
               "uri": info.get("uri", ""), "ua": info.get("ua", ""),
               "proto": info.get("proto", ""), "method": info.get("method", "")}
        with open(HOLD_LOG, "a") as fh:
            fh.write(json.dumps(row) + "\n")
        try:
            if (os.stat(HOLD_LOG).st_mode & 0o777) != LOG_MODE:
                os.chmod(HOLD_LOG, LOG_MODE)
        except OSError:
            pass
    except Exception:
        pass


async def handle(reader, writer):
    global live
    if live >= MAXCONN:
        writer.close()
        return
    live += 1
    loop = asyncio.get_event_loop()
    start = loop.time()
    info, peer = {}, ""
    try:
        try:
            peer = (writer.get_extra_info("peername") or ("", 0))[0]
        except Exception:
            peer = ""
        try:
            raw = await asyncio.wait_for(reader.read(8192), timeout=15)
            info = parse_request(raw or b"")
        except asyncio.TimeoutError:
            pass
        writer.write(HEAD.encode())
        await writer.drain()
        writer.write(chunk("<html><head><title>Loading</title></head><body>"))
        await writer.drain()

        # Drip until the client gives up, and watch for that happening.
        #
        # drain() alone is not enough: a write to a socket the peer has already
        # abandoned lands in the kernel buffer and returns cleanly, so the drip
        # loop can run on long after anyone is listening. It would hold the slot
        # until MAX_HOLD and record a wildly overstated duration. reader.read()
        # returning b"" is the reliable signal, so race the two.
        async def drip():
            while loop.time() - start < MAXHOLD:
                await asyncio.sleep(DELAY)
                pad = "".join(random.choices(string.hexdigits.lower(), k=10))
                writer.write(chunk("<!-- %s -->" % pad))
                await writer.drain()

        tasks = {asyncio.ensure_future(drip()),
                 asyncio.ensure_future(reader.read())}
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            t.exception()  # retrieve, so it is not reported as never-retrieved
    except Exception:
        pass
    finally:
        live -= 1
        held = loop.time() - start
        record(info, held, peer)
        try:
            writer.close()
        except Exception:
            pass


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    os.umask(0)   # so the mode above is what actually lands on disk
    try:
        os.makedirs(os.path.dirname(HOLD_LOG) or ".", exist_ok=True)
        os.chmod(os.path.dirname(HOLD_LOG) or ".", 0o777)
    except OSError:
        pass
    asyncio.run(main())
