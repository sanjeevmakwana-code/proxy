"""ProxyForge — live pool: admission, scoring state, TTL rechecks, rotation strategies."""
from __future__ import annotations

import json
import logging
import os
import random
import time
from collections import Counter

from models import Proxy, ANON_LEVEL

log = logging.getLogger("pool")


class ProxyPool:
    def __init__(self, cfg):
        self.cfg = cfg
        self.proxies: dict[str, Proxy] = {}
        self.recent_dead: dict[str, float] = {}
        self._rr = 0
        self.on_alive = None                     # hook: called on (new) alive proxies

    # -------------------------------------------------- lifecycle

    def get_or_create(self, host, port, protocol) -> Proxy:
        return self.proxies.get(f"{protocol}://{host}:{port}") \
            or Proxy(host=host, port=port, protocol=protocol)

    def should_check(self, host, port, protocol) -> bool:
        key = f"{protocol}://{host}:{port}"
        if key in self.proxies:
            return False                          # managed by the recheck loop
        t = self.recent_dead.get(key)
        return t is None or time.time() - t >= self.cfg.dead_grace

    def commit(self, p: Proxy, alive: bool):
        now = time.time()
        p.last_checked = now
        if alive:
            p.streak = p.streak + 1 if p.streak > 0 else 1
            p.ok += 1
            p.last_alive = now
            was_new = p.key not in self.proxies
            self.proxies[p.key] = p
            if self.on_alive and (was_new or p.country is None):
                try:
                    self.on_alive(p)
                except Exception:
                    pass
        else:
            p.streak = p.streak - 1 if p.streak < 0 else -1
            p.fail += 1
            if p.key in self.proxies:
                if p.streak <= -self.cfg.max_fail_streak:
                    self.proxies.pop(p.key, None)
                    self.recent_dead[p.key] = now
            else:
                self.recent_dead.setdefault(p.key, now)
        if len(self.recent_dead) > 50000:         # bounded memory
            cutoff = now - self.cfg.dead_grace * 2
            self.recent_dead = {k: t for k, t in self.recent_dead.items() if t > cutoff}

    def report(self, key: str, ok: bool) -> bool:
        """Consumer feedback via POST /report — feeds the quality score."""
        p = self.proxies.get(key)
        if not p:
            return False
        now = time.time()
        if ok:
            p.streak = p.streak + 1 if p.streak > 0 else 1
            p.ok += 1
            p.last_alive = now
        else:
            p.streak = p.streak - 1 if p.streak < 0 else -1
            p.fail += 1
            if p.streak <= -self.cfg.max_fail_streak:
                self.proxies.pop(key, None)
                self.recent_dead[key] = now
        return True

    def due(self) -> list[Proxy]:
        horizon = time.time() - self.cfg.recheck_interval
        return [p for p in self.proxies.values() if p.last_checked <= horizon]

    # -------------------------------------------------- rotation / queries

    def _matching(self, protocol=None, country=None, anonymity=None,
                  min_score=None, https_only=False) -> list[Proxy]:
        floor = ANON_LEVEL.get(self.cfg.anonymity_floor, 0)
        out = []
        for p in self.proxies.values():
            if not p.alive:
                continue
            if protocol and p.protocol != protocol:
                continue
            if https_only and p.protocol == "http" and not p.connect_https:
                continue
            if floor and ANON_LEVEL.get(p.anonymity, 0) < floor:
                continue
            if country and (p.cc or "").upper() != country.upper() \
                    and (p.country or "").lower() != country.lower():
                continue
            if min_score is not None and p.score < min_score:
                continue
            if self.cfg.min_speed_kbps and p.speed_kbps \
                    and p.speed_kbps < self.cfg.min_speed_kbps:
                continue
            out.append(p)
        return out

    def acquire(self, protocol=None, country=None, anonymity=None,
                min_score=None, https_only=False, strategy=None) -> Proxy | None:
        cands = self._matching(protocol, country, anonymity, min_score, https_only)
        if not cands:
            return None
        st = strategy or self.cfg.strategy
        if st == "fastest":
            p = min(cands, key=lambda x: (x.uses, x.latency_ms or 9999))
        elif st == "round_robin":
            cands.sort(key=lambda x: x.key)
            self._rr += 1
            p = cands[self._rr % len(cands)]
        else:  # weighted — quality-biased, usage-damped
            weights = [(max(x.score, 0.05) ** 2) / (1 + x.uses) + 1e-4 for x in cands]
            p = random.choices(cands, weights=weights, k=1)[0]
        p.uses += 1
        return p

    def query(self, protocol=None, country=None, anonymity=None, min_score=None,
              https_only=False, order="score", limit=100) -> list[dict]:
        out = self._matching(protocol, country, anonymity, min_score, https_only)
        keyf = {"latency": lambda x: x.latency_ms if x.latency_ms is not None else 1e9,
                "speed": lambda x: -(x.speed_kbps or 0),
                "score": lambda x: -x.score}.get(order) or (lambda x: -x.score)
        out.sort(key=keyf)
        if limit:
            out = out[:limit]
        return [p.to_dict() for p in out]

    def stats(self) -> dict:
        alive = [p for p in self.proxies.values() if p.alive]
        lats = sorted(p.latency_ms for p in alive if p.latency_ms is not None)
        return {
            "alive": len(alive), "tracked": len(self.proxies),
            "protocols": dict(Counter(p.protocol for p in alive)),
            "anonymity": dict(Counter(p.anonymity for p in alive)),
            "grades": dict(Counter(p.grade for p in alive)),
            "countries": dict(Counter(p.cc or "??" for p in alive).most_common(10)),
            "avg_score": round(sum(p.score for p in alive) / len(alive), 3) if alive else 0,
            "p50_latency_ms": round(lats[len(lats) // 2]) if lats else None,
            "p90_latency_ms": round(lats[int(len(lats) * 0.9)]) if lats else None,
            "avg_speed_kbps": round(sum(p.speed_kbps or 0 for p in alive) / len(alive)) if alive else 0,
        }

    # -------------------------------------------------- persistence

    def save(self):
        try:
            tmp = self.cfg.persist_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump([p.to_dict() for p in self.proxies.values()], f)
            os.replace(tmp, self.cfg.persist_path)
        except Exception as e:
            log.debug("persist failed: %s", e)

    def load(self) -> int:
        try:
            with open(self.cfg.persist_path) as f:
                data = json.load(f)
        except Exception:
            return 0
        n = 0
        for d in data:
            try:
                p = Proxy(host=d["host"], port=int(d["port"]), protocol=d["protocol"],
                          connect_https=bool(d.get("https")),
                          anonymity=d.get("anonymity", "unknown"),
                          country=d.get("country"), cc=d.get("country_code"),
                          asn=d.get("asn"), datacenter=bool(d.get("datacenter")),
                          ok=int(d.get("ok", 0)), fail=int(d.get("fail", 0)),
                          score=float(d.get("score", 0)))
                if p.protocol in ("http", "socks4", "socks5") and p.host:
                    self.proxies[p.key] = p     # last_checked=0 → rechecked immediately
                    n += 1
            except Exception:
                continue
        if n:
            log.info("warm start: %d proxies restored (all requeued for validation)", n)
        return n
