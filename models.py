"""ProxyForge — data model."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

ANON_LEVEL = {"transparent": 0, "unknown": 0, "anonymous": 1, "elite": 2}


@dataclass
class Proxy:
    host: str
    port: int
    protocol: str                       # http | socks4 | socks5
    connect_https: bool = False         # http proxies: verified CONNECT/HTTPS support

    latency_ms: float | None = None
    speed_kbps: float | None = None
    anonymity: str = "unknown"          # elite | anonymous | transparent | unknown
    country: str | None = None
    cc: str | None = None
    asn: str | None = None
    datacenter: bool = False

    score: float = 0.0
    ok: int = 0
    fail: int = 0
    streak: int = 0                     # >0 consecutive alive, <0 consecutive dead
    uses: int = 0
    last_checked: float = 0.0
    last_alive: float = 0.0
    added: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return f"{self.protocol}://{self.host}:{self.port}"

    @property
    def alive(self) -> bool:
        return self.streak > 0

    @property
    def grade(self) -> str:
        if self.score >= 0.75:
            return "A"
        if self.score >= 0.50:
            return "B"
        return "C"

    def to_dict(self) -> dict:
        return {
            "proxy": self.key, "host": self.host, "port": self.port,
            "protocol": self.protocol, "https": self.connect_https,
            "latency_ms": round(self.latency_ms) if self.latency_ms is not None else None,
            "speed_kbps": round(self.speed_kbps) if self.speed_kbps is not None else None,
            "anonymity": self.anonymity, "country": self.country,
            "country_code": self.cc, "asn": self.asn, "datacenter": self.datacenter,
            "score": round(self.score, 3), "grade": self.grade,
            "ok": self.ok, "fail": self.fail, "uses": self.uses,
            "last_checked": int(self.last_checked),
        }
