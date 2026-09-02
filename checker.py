"""ProxyForge — validation engine: latency, throughput, anonymity, HTTPS capability."""
from __future__ import annotations

import asyncio
import logging
import random
import time

import aiohttp

from models import Proxy

try:
    from aiohttp_socks import ProxyConnector
except ImportError:
    ProxyConnector = None

log = logging.getLogger("checker")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Judges echo the request back. echoes=True means we can read the headers the
# proxy injected → anonymity detection. Rotate to spread load; broken judges
# are automatically cooled down.
JUDGES = [
    ("http://azenv.net/", True),
    ("http://httpbin.org/get", True),
    ("http://ip-api.com/json/?fields=query", False),
]
HTTPS_PROBES = ("https://checkip.amazonaws.com/", "https://api.ipify.org/")
REALIP_URLS = ("https://checkip.amazonaws.com/", "https://api.ipify.org/")

ANON_MARKERS = ("http_via", "http_x_forwarded", "http_forwarded", "http_client_ip",
                '"via"', '"x-forwarded-for"', '"forwarded"')
ANON_WEIGHT = {"transparent": 0.0, "unknown": 0.4, "anonymous": 0.7, "elite": 1.0}


def detect_anonymity(echoes_headers: bool, body: str, real_ip: str | None) -> str:
    if real_ip and real_ip in body:
        return "transparent"          # judge saw our real IP (directly or echoed)
    if echoes_headers:
        low = body.lower()
        if any(m in low for m in ANON_MARKERS):
            return "anonymous"        # proxy announced itself via hop headers
        return "elite"                # no client IP, no proxy headers
    return "unknown"


class JudgeRotator:
    def __init__(self, judges):
        self.judges = judges
        self.i = random.randrange(len(judges))
        self.fails = [0] * len(judges)
        self.cool = [0.0] * len(judges)

    def get(self):
        now = time.time()
        for _ in range(len(self.judges)):
            self.i = (self.i + 1) % len(self.judges)
            if self.cool[self.i] <= now:
                url, echoes = self.judges[self.i]
                return url, self.i, echoes
        url, echoes = self.judges[self.i]
        return url, self.i, echoes

    def report(self, idx: int, ok: bool):
        if ok:
            self.fails[idx] = 0
            return
        self.fails[idx] += 1
        if self.fails[idx] >= 10:
            self.cool[idx] = time.time() + 600
            self.fails[idx] = 0
            log.warning("judge %s misbehaving — cooling down 10 min", self.judges[idx][0])


class Checker:
    def __init__(self, cfg, pool):
        self.cfg = cfg
        self.pool = pool
        self.real_ip: str | None = None
        self.rot = JudgeRotator(JUDGES)
        self.total_checked = 0
        self.rate = 0.0
        self.speed_fail_streak = 0
        self.speed_disabled = not cfg.speed_test
        if ProxyConnector is None:
            log.warning("aiohttp-socks missing → SOCKS4/5 validation disabled")

    # ------------------------------------------------------------ loops

    async def worker(self, queue: asyncio.Queue):
        while True:
            host, port, proto = await queue.get()
            try:
                p = self.pool.get_or_create(host, port, proto)
                alive, p = await self.check(p)
                self.pool.commit(p, alive)
            except Exception as e:                    # never let a worker die
                log.debug("worker error: %s", e)
            finally:
                queue.task_done()

    async def refresh_real_ip(self):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10),
                                             trust_env=False) as sess:
                for u in REALIP_URLS:
                    try:
                        async with sess.get(u) as r:
                            ip = (await r.text()).strip()
                        if ip.count(".") == 3:
                            self.real_ip = ip
                            log.info("our real exit IP: %s (used for anonymity detection)", ip)
                            return
                    except Exception:
                        continue
        except Exception:
            pass

    async def real_ip_loop(self):
        while True:
            await self.refresh_real_ip()
            await asyncio.sleep(3600)

    async def rate_loop(self):
        last = 0
        while True:
            await asyncio.sleep(10)
            cur = self.total_checked
            self.rate = (cur - last) / 10.0
            last = cur

    # ------------------------------------------------------------ core check

    async def check(self, p: Proxy) -> tuple[bool, Proxy]:
        cfg = self.cfg
        self.total_checked += 1
        proxy_arg = f"http://{p.host}:{p.port}" if p.protocol == "http" else None

        connector = None
        if p.protocol != "http":
            if ProxyConnector is None:
                return False, p
            try:
                connector = ProxyConnector.from_url(f"{p.protocol}://{p.host}:{p.port}", rdns=True)
            except Exception:
                return False, p

        timeout = aiohttp.ClientTimeout(total=cfg.check_timeout, connect=cfg.connect_timeout)
        try:
            async with aiohttp.ClientSession(connector=connector, timeout=timeout,
                                             trust_env=False,
                                             headers={"User-Agent": UA, "Accept": "*/*"}) as sess:
                # -- stage 1: judge → latency, exit IP, header leakage --
                judge, ji, echoes = self.rot.get()
                t0 = time.perf_counter()
                try:
                    async with sess.get(judge, proxy=proxy_arg) as r:
                        body = await r.text(errors="ignore")
                        status = r.status
                except Exception:
                    self.rot.report(ji, False)
                    raise
                self.rot.report(ji, True)
                latency_ms = (time.perf_counter() - t0) * 1000
                if status != 200:
                    raise RuntimeError(f"judge HTTP {status}")

                anon = detect_anonymity(echoes, body, self.real_ip)
                if anon != "unknown":
                    p.anonymity = anon
                p.latency_ms = latency_ms if p.latency_ms is None \
                    else 0.4 * p.latency_ms + 0.6 * latency_ms

                # -- stage 2: HTTPS/CONNECT capability (http proxies, verified once) --
                if p.protocol == "http" and not p.connect_https and p.ok == 0:
                    for u in HTTPS_PROBES:
                        try:
                            async with sess.get(u, proxy=proxy_arg) as r2:
                                await r2.read()
                            p.connect_https = True
                            break
                        except Exception:
                            continue

                # -- stage 3: throughput --
                if not self.speed_disabled:
                    kbps = await self._measure_speed(sess, proxy_arg)
                    if kbps:
                        p.speed_kbps = kbps if p.speed_kbps is None \
                            else 0.3 * p.speed_kbps + 0.7 * kbps

                if cfg.max_latency_ms and latency_ms > cfg.max_latency_ms:
                    return False, p                     # too slow to keep

                p.score = self._score(p)
                return True, p

        except Exception as e:
            log.debug("dead %-24s %s", p.key, str(e)[:90])
            return False, p

    async def _measure_speed(self, sess, proxy_arg) -> float | None:
        for url in self.cfg.speed_urls:
            try:
                t0 = time.perf_counter()
                n = 0
                async with sess.get(url, proxy=proxy_arg) as r:
                    async for chunk in r.content.iter_chunked(16384):
                        n += len(chunk)
                        if n >= self.cfg.speed_budget:
                            break
                dt = time.perf_counter() - t0
                if n >= 8192 and dt > 0:
                    self.speed_fail_streak = 0
                    return (n / 1024.0) / dt
            except Exception:
                continue
        self.speed_fail_streak += 1
        if self.speed_fail_streak > 200 and not self.speed_disabled:
            self.speed_disabled = True
            log.warning("speed targets unreachable through proxies — throughput scoring off")
        return None

    def _score(self, p: Proxy) -> float:
        cfg = self.cfg
        comps = []
        lat = max(0.0, 1.0 - (p.latency_ms or cfg.max_latency_ms) / cfg.max_latency_ms)
        comps.append((lat, 0.40))
        if p.speed_kbps:
            comps.append((min(p.speed_kbps / cfg.target_speed_kbps, 1.0), 0.25))
        total = p.ok + p.fail
        comps.append(((p.ok / total) if total else 0.5, 0.25))
        comps.append((ANON_WEIGHT.get(p.anonymity, 0.4), 0.10))
        wsum = sum(w for _, w in comps)
        return sum(v * w for v, w in comps) / wsum
