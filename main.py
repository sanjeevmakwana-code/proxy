"""ProxyForge — entry point / orchestrator.   Run: python main.py"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from types import SimpleNamespace

from aiohttp import web

from api import build_app
from checker import Checker
from config import Settings
from fetcher import Fetcher
from gateway import Gateway
from geo import GeoEnricher
from pool import ProxyPool

log = logging.getLogger("main")

BANNER = r"""
 ███╗   ███╗ ██████╗ ██████╗ ██╗  ██╗███████╗ ██████╗ ██████╗  ██████╗ ███████╗
 ████╗ ████║██╔═══██╗██╔══██╗╚██╗██╔╝██╔════╝██╔═══██╗██╔══██╗██╔═══██╗██╔════╝
 ██╔████╔██║██║   ██║██████╔╝ ╚███╔╝ █████╗  ██║   ██║██████╔╝██║   ██║███████╗
 ██║╚██╔╝██║██║   ██║██╔══██╗ ██╔██╗ ██╔══╝  ██║   ██║██╔═══╝ ██║   ██║╚════██║
 ██║ ╚═╝ ██║╚██████╔╝██║  ██║██╔╝ ██╗███████╗╚██████╔╝██║     ╚██████╔╝███████║
 ╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚═╝      ╚═════╝ ╚══════╝
      real-time public proxy harvesting · validation · rotation engine
"""


async def scan_loop(en):
    cfg, fetcher, pool, queue = en.cfg, en.fetcher, en.pool, en.queue
    while True:
        t0 = time.time()
        try:
            found = await fetcher.scan()
        except Exception as e:
            log.error("scan failed: %s", e)
            found = {}
        queued = 0
        for (ip, port), protos in found.items():
            for proto in protos:
                if pool.should_check(ip, port, proto):
                    try:
                        queue.put_nowait((ip, port, proto))
                        queued += 1
                    except asyncio.QueueFull:
                        break
        log.info("scan done in %.1fs → %d unique hosts, %d new checks queued",
                 time.time() - t0, len(found), queued)
        await asyncio.sleep(cfg.source_interval)


async def recheck_loop(en):
    cfg, pool, queue = en.cfg, en.pool, en.queue
    while True:
        await asyncio.sleep(30)
        due = pool.due()
        random.shuffle(due)
        for p in due:
            try:
                queue.put_nowait((p.host, p.port, p.protocol))
            except asyncio.QueueFull:
                log.warning("check queue full — rechecks throttled")
                break


async def persist_loop(en):
    while True:
        await asyncio.sleep(en.cfg.persist_interval)
        en.pool.save()


async def stats_loop(en):
    while True:
        await asyncio.sleep(60)
        s = en.pool.stats()
        g = s["grades"]
        log.info("[LIVE] alive=%s tracked=%s p50=%sms elite=%s | A=%s B=%s C=%s | %.0f checks/s | queue=%s",
                 s["alive"], s["tracked"], s["p50_latency_ms"], s["anonymity"].get("elite", 0),
                 g.get("A", 0), g.get("B", 0), g.get("C", 0),
                 en.checker.rate, en.queue.qsize())


async def amain(cfg: Settings):
    pool = ProxyPool(cfg)
    checker = Checker(cfg, pool)
    fetcher = Fetcher(cfg)
    geo = GeoEnricher(cfg, pool)
    queue = asyncio.Queue(maxsize=100_000)
    en = SimpleNamespace(cfg=cfg, pool=pool, checker=checker, fetcher=fetcher,
                         geo=geo, queue=queue, started=time.time())

    pool.on_alive = lambda p: geo.enqueue(p.host)
    pool.load()

    try:                                        # raise fd limit (posix)
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, 65535), hard))
    except Exception:
        pass

    for _ in range(cfg.concurrency):
        asyncio.create_task(checker.worker(queue))
    for coro in (checker.real_ip_loop(), checker.rate_loop(), geo.loop(),
                 persist_loop(en), recheck_loop(en), stats_loop(en),
                 scan_loop(en), checker.refresh_real_ip()):
        asyncio.create_task(coro)

    if cfg.gateway_enabled:
        await Gateway(cfg, en).start()

    runner = web.AppRunner(build_app(en))
    await runner.setup()
    await web.TCPSite(runner, cfg.api_host, cfg.api_port).start()
    log.info("API + dashboard → http://%s:%d", cfg.api_host, cfg.api_port)
    log.info("Ctrl+C to stop.")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        pool.save()
        log.info("pool persisted (%d proxies) — bye", len(pool.proxies))


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(name)-8s | %(message)s",
                        datefmt="%H:%M:%S")
    print(BANNER)
    cfg = Settings()
    log.info("concurrency=%d | recheck every %ss | sources every %ss | strategy=%s",
             cfg.concurrency, cfg.recheck_interval, cfg.source_interval, cfg.strategy)
    try:
        asyncio.run(amain(cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
