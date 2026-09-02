"""ProxyForge — concurrent source scanner with per-source health tracking."""
from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from sources import SOURCES, parse_source, valid_ip

log = logging.getLogger("fetcher")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class _Health:
    __slots__ = ("ok", "fails", "last_ok", "last_count", "last_error")

    def __init__(self):
        self.ok = 0
        self.fails = 0
        self.last_ok = 0.0
        self.last_count = 0
        self.last_error: str | None = None


class Fetcher:
    def __init__(self, cfg):
        self.cfg = cfg
        self.health = {s["name"]: _Health() for s in SOURCES}

    async def _fetch_one(self, session: aiohttp.ClientSession, src: dict):
        h = self.health[src["name"]]
        try:
            async with session.get(src["url"], ssl=False,
                                   timeout=aiohttp.ClientTimeout(total=self.cfg.source_timeout)) as r:
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}")
                text = await r.text(errors="ignore")
            items = parse_source(src, text)
            h.ok += 1
            h.last_ok = time.time()
            h.last_count = len(items)
            return items
        except Exception as e:
            h.fails += 1
            h.last_error = str(e)[:120]
            log.warning("source %-18s failed: %s", src["name"], h.last_error)
            return []

    async def scan(self) -> dict[tuple[str, int], set[str]]:
        """Fetch every source concurrently → {(ip, port): {protocols}}."""
        tasks = []
        async with aiohttp.ClientSession(headers={"User-Agent": UA}, trust_env=False) as session:
            for src in SOURCES:
                if src.get("pages"):
                    for page in range(1, src["pages"] + 1):
                        tasks.append(self._fetch_one(session, {**src, "url": src["url"].format(page=page)}))
                else:
                    tasks.append(self._fetch_one(session, src))
            results = await asyncio.gather(*tasks)

        found: dict[tuple[str, int], set[str]] = {}
        raw_total = 0
        for items in results:
            raw_total += len(items)
            for ip, port, proto in items:
                if valid_ip(ip):
                    found.setdefault((ip, port), set()).add(proto)
        log.info("scan: %d raw entries → %d unique hosts across %d sources",
                 raw_total, len(found), len(SOURCES))
        return found

    def health_summary(self) -> list[dict]:
        now = time.time()
        return [{
            "name": s["name"], "ok": h.ok, "fails": h.fails, "last_count": h.last_count,
            "last_ok_ago": int(now - h.last_ok) if h.last_ok else None,
            "error": h.last_error,
        } for s, h in ((s, self.health[s["name"]]) for s in SOURCES)]
