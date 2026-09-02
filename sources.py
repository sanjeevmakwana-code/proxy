"""ProxyForge — registry of public proxy-list sources + parsers.

Add a source by appending one dict:
    {"name": ..., "url": ..., "type": "raw"|"html"|"json", "protocol": "http"|"socks4"|"socks5"}
  raw  → ip:port lines (also handles 'ip:port|meta...' formats)
  html → <td>ip</td><td>port</td> tables (free-proxy-list.net family)
  json → {"data": [{"ip":..., "port":..., "protocols":[...]}]} (geonode style)
"""

import json
import re

IP_PORT = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})[:|,\s]+(\d{2,5})")
TD_ROW = re.compile(
    r"<td[^>]*>\s*(\d{1,3}(?:\.\d{1,3}){3})\s*</td>\s*<td[^>]*>\s*(\d{2,5})\s*</td>", re.I)

G = "https://raw.githubusercontent.com"
PS = "https://api.proxyscrape.com/v2/?request=displayproxies&timeout=10000&country=all&ssl=all&anonymity=all&protocol="
PLD = "https://www.proxy-list.download/api/v1/get?type="

SOURCES: list[dict] = [
    # ---- GitHub aggregators (highest volume, most reliable feeds) ----
    dict(name="thespeedx-http",    url=f"{G}/TheSpeedX/PROXY-List/master/http.txt",           type="raw", protocol="http"),
    dict(name="thespeedx-socks4",  url=f"{G}/TheSpeedX/PROXY-List/master/socks4.txt",         type="raw", protocol="socks4"),
    dict(name="thespeedx-socks5",  url=f"{G}/TheSpeedX/PROXY-List/master/socks5.txt",         type="raw", protocol="socks5"),
    dict(name="monosans-http",     url=f"{G}/monosans/proxy-list/main/proxies/http.txt",      type="raw", protocol="http"),
    dict(name="monosans-socks4",   url=f"{G}/monosans/proxy-list/main/proxies/socks4.txt",    type="raw", protocol="socks4"),
    dict(name="monosans-socks5",   url=f"{G}/monosans/proxy-list/main/proxies/socks5.txt",    type="raw", protocol="socks5"),
    dict(name="proxifly-http",     url=f"{G}/proxifly/free-proxy-list/main/proxies/http/data.txt",   type="raw", protocol="http"),
    dict(name="proxifly-socks4",   url=f"{G}/proxifly/free-proxy-list/main/proxies/socks4/data.txt", type="raw", protocol="socks4"),
    dict(name="proxifly-socks5",   url=f"{G}/proxifly/free-proxy-list/main/proxies/socks5/data.txt", type="raw", protocol="socks5"),
    dict(name="hideip-http",       url=f"{G}/zloi-user/hideip.me/main/http.txt",              type="raw", protocol="http"),
    dict(name="hideip-https",      url=f"{G}/zloi-user/hideip.me/main/https.txt",             type="raw", protocol="http"),
    dict(name="hideip-socks4",     url=f"{G}/zloi-user/hideip.me/main/socks4.txt",            type="raw", protocol="socks4"),
    dict(name="hideip-socks5",     url=f"{G}/zloi-user/hideip.me/main/socks5.txt",            type="raw", protocol="socks5"),
    dict(name="roosterkid-https",  url=f"{G}/roosterkid/openproxylist/main/HTTPS_RAW.txt",    type="raw", protocol="http"),
    dict(name="roosterkid-socks5", url=f"{G}/roosterkid/openproxylist/main/SOCKS5_RAW.txt",   type="raw", protocol="socks5"),
    dict(name="jetkai-http",       url=f"{G}/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt",   type="raw", protocol="http"),
    dict(name="jetkai-socks4",     url=f"{G}/jetkai/proxy-list/main/online-proxies/txt/proxies-socks4.txt", type="raw", protocol="socks4"),
    dict(name="jetkai-socks5",     url=f"{G}/jetkai/proxy-list/main/online-proxies/txt/proxies-socks5.txt", type="raw", protocol="socks5"),
    dict(name="mmpx12-http",       url=f"{G}/mmpx12/proxy-list/master/http.txt",              type="raw", protocol="http"),
    dict(name="mmpx12-socks4",     url=f"{G}/mmpx12/proxy-list/master/socks4.txt",            type="raw", protocol="socks4"),
    dict(name="mmpx12-socks5",     url=f"{G}/mmpx12/proxy-list/master/socks5.txt",            type="raw", protocol="socks5"),
    dict(name="clarketm-raw",      url=f"{G}/clarketm/proxy-list/master/proxy-list-raw.txt",  type="raw", protocol="http"),
    dict(name="hookzof-socks5",    url=f"{G}/hookzof/socks5_list/master/proxy.txt",           type="raw", protocol="socks5"),
    # ---- aggregator APIs ----
    dict(name="proxyscrape-http",   url=f"{PS}http",   type="raw", protocol="http"),
    dict(name="proxyscrape-socks4", url=f"{PS}socks4", type="raw", protocol="socks4"),
    dict(name="proxyscrape-socks5", url=f"{PS}socks5", type="raw", protocol="socks5"),
    dict(name="proxy-list-dl-http",   url=f"{PLD}http",   type="raw", protocol="http"),
    dict(name="proxy-list-dl-socks4", url=f"{PLD}socks4", type="raw", protocol="socks4"),
    dict(name="proxy-list-dl-socks5", url=f"{PLD}socks5", type="raw", protocol="socks5"),
    dict(name="proxyspace-http",   url="https://proxyspace.pro/http.txt",    type="raw", protocol="http"),
    dict(name="proxyspace-socks5", url="https://proxyspace.pro/socks5.txt",  type="raw", protocol="socks5"),
    # ---- JSON APIs (paginated) ----
    dict(name="geonode",
         url="https://proxylist.geonode.com/api/proxy-list?limit=500&page={page}&sort_by=lastChecked&sort_type=desc",
         type="json", protocol=None, pages=3),
    # ---- HTML tables (free-proxy-list.net family) ----
    dict(name="free-proxy-list", url="https://free-proxy-list.net/", type="html", protocol="http"),
    dict(name="ssl-proxies",     url="https://sslproxies.org/",      type="html", protocol="http"),
    dict(name="us-proxy",        url="https://us-proxy.org/",        type="html", protocol="http"),
]


def valid_ip(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        n = [int(p) for p in parts]
    except ValueError:
        return False
    if any(x > 255 for x in n):
        return False
    if n[0] in (0, 10, 127):
        return False
    if n[0] == 192 and n[1] == 168:
        return False
    if n[0] == 172 and 16 <= n[1] <= 31:
        return False
    if n[0] == 169 and n[1] == 254:
        return False
    return True


def parse_source(src: dict, text: str) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    kind, proto = src["type"], src.get("protocol")

    if kind == "raw":
        for ip, port in IP_PORT.findall(text):
            out.append((ip, int(port), proto))

    elif kind == "html":
        for ip, port in TD_ROW.findall(text):
            out.append((ip, int(port), proto))

    elif kind == "json":
        try:
            data = json.loads(text)
        except ValueError:
            return out
        items = data.get("data") if isinstance(data, dict) else data
        for it in items or []:
            ip = str(it.get("ip", "")).strip()
            try:
                port = int(it.get("port"))
            except (TypeError, ValueError):
                continue
            protos = it.get("protocols") or [it.get("protocol") or "http"]
            for pr in protos:
                pr = str(pr).lower()
                if pr == "https":
                    pr = "http"                     # 'https' lists = CONNECT-capable http
                if pr in ("http", "socks4", "socks5"):
                    out.append((ip, port, pr))
    return out
