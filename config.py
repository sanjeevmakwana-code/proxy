"""ProxyForge — central configuration."""
from dataclasses import dataclass


@dataclass
class Settings:
    # ---- scanning ----
    source_interval: int = 300          # seconds between full source re-scans
    source_timeout: int = 20            # per-source fetch timeout (s)

    # ---- validation ----
    concurrency: int = 500              # simultaneous proxy checks
    check_timeout: float = 10.0         # per-request timeout (s)
    connect_timeout: float = 5.0        # connect phase timeout (s)
    max_latency_ms: float = 2500        # admission threshold
    speed_test: bool = True
    speed_urls: tuple = (
        "http://ipv4.download.thinkbroadband.com/512KB.zip",
        "http://speedtest.tele2.net/512KB.zip",
        "http://cachefly.cachefly.net/512kb.test",
    )
    speed_budget: int = 512 * 1024      # max bytes downloaded per speed test
    target_speed_kbps: float = 400.0    # throughput that earns a perfect speed score
    min_speed_kbps: float = 0.0         # 0 = don't filter, only score
    anonymity_floor: str = "any"        # any | anonymous | elite

    # ---- revalidation ----
    recheck_interval: int = 180         # every live proxy re-probed each N sec
    max_fail_streak: int = 3            # consecutive failures before eviction
    dead_grace: int = 600               # skip re-testing known-dead for N sec

    # ---- geo enrichment (ip-api.com free batch API) ----
    geo_enabled: bool = True
    geo_batch_size: int = 100

    # ---- serving ----
    api_host: str = "0.0.0.0"
    api_port: int = 8899
    gateway_enabled: bool = True
    gateway_host: str = "127.0.0.1"     # bind 0.0.0.0 only on a trusted network
    gateway_port: int = 8898
    gateway_retries: int = 3            # upstream retries per request
    gateway_timeout: float = 75.0

    # ---- pool / rotation ----
    strategy: str = "weighted"          # round_robin | weighted | fastest
    persist_path: str = "proxies.json"
    persist_interval: int = 60
