"""ProxyForge — batch geo/ASN/datacenter enrichment (ip-api.com free batch API)."""
from __future__ import annotations

import asyncio
import logging
from collections import deque

import aiohttp

log = logging.getLogger("geo")

BATCH_URL = "http://ip-api.com/batch"


class GeoEnricher:
    def __init__(self, cfg, pool):
        self.cfg = cfg
        self.pool = pool
        self.q: deque[str] = deque()
        self.inq: set[str] = set()
        self.done = 0

    def enqueue(self, host: str):
        if not self.cfg.geo_enabled or host in self.inq or len(self.inq) >= 5000:
            return
        self.inq.add(host)
        self.q.append(host)

    async def loop(self):
        if not self.cfg.geo_enabled:
            return
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as sess:
            while True:
                await asyncio.sleep(4.2)            # stays under the 15 req/min free limit
                if not self.q:
                    continue
                batch = []
                while self.q and len(batch) < self.cfg.geo_batch_size:
                    h = self.q.popleft()
                    self.inq.discard(h)
                    batch.append(h)

                hostmap: dict[str, list] = {}
                for p in self.pool.proxies.values():
                    hostmap.setdefault(p.host, []).append(p)
                batch = [h for h in batch if h in hostmap]
                if not batch:
                    continue
                try:
                    async with sess.post(BATCH_URL, json=batch,
                                         params={"fields": "status,query,country,countryCode,as,hosting"}) as r:
                        data = await r.json(content_type=None)
                    for d in data:
                        if not isinstance(d, dict) or d.get("status") != "ok":
                            continue
                        for p in hostmap.get(d.get("query"), []):
                            p.country = d.get("country")
                            p.cc = d.get("countryCode")
                            p.asn = d.get("as")
                            p.datacenter = bool(d.get("hosting"))
                            self.done += 1
                except Exception as e:
                    log.debug("geo batch failed: %s", e)
