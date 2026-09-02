"""ProxyForge — local rotating forward proxy.

Point any tool at http://127.0.0.1:8898 and every request is relayed through a
different pool proxy (auto-retried on failure). Supports:
  • CONNECT tunneling (browsers, HTTPS, most clients)
  • plain HTTP with absolute or origin-form request targets
Limitations: one request per connection (Connection: close is injected);
chunked client request bodies are streamed best-effort.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import urllib.parse

from models import Proxy

log = logging.getLogger("gateway")

HOP_HEADERS = {"connection", "keep-alive", "proxy-connection", "proxy-authorization",
               "proxy-authenticate", "te", "trailer", "transfer-encoding", "upgrade"}


class Gateway:
    def __init__(self, cfg, engine):
        self.cfg = cfg
        self.pool = engine.pool
        self.conn_count = 0

    async def start(self):
        server = await asyncio.start_server(
            self._handle, self.cfg.gateway_host, self.cfg.gateway_port, limit=1 << 16)
        log.info("rotating gateway listening on %s:%d", self.cfg.gateway_host, self.cfg.gateway_port)
        return server

    # ------------------------------------------------------------ client side

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.conn_count += 1
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
        except Exception:
            writer.close()
            return
        try:
            lines = head.decode("latin1").split("\r\n")
            method, target, _ver = (lines[0].split(" ") + ["HTTP/1.1"])[:3]
            headers = {}
            for ln in lines[1:]:
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            if method.upper() == "CONNECT":
                host, _, port = target.partition(":")
                await self._tunnel(reader, writer, host, int(port or 443))
            else:
                await self._plain_http(reader, writer, method, target, headers)
        except Exception as e:
            log.debug("gateway conn error: %s", e)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _plain_http(self, reader, writer, method, target, headers):
        if target.lower().startswith(("http://", "https://")):
            u = urllib.parse.urlsplit(target)
            host = u.hostname
            port = u.port or (443 if u.scheme == "https" else 80)
            path = urllib.parse.urlunsplit(("", "", u.path or "/", u.query, ""))
        else:
            host, _, port_s = headers.get("host", "").partition(":")
            port = int(port_s) if port_s else 80
            path = target or "/"

        body = b""
        cl = headers.get("content-length")
        if cl and cl.isdigit():
            body = await reader.readexactly(int(cl))

        out = [f"{method} {path} HTTP/1.1"]
        for k, v in headers.items():
            if k not in HOP_HEADERS:
                out.append(f"{k}: {v}")
        out.append("Connection: close")
        req = ("\r\n".join(out) + "\r\n\r\n").encode("latin1") + body

        await self._relay(reader, writer, host, port, first_payload=req)

    async def _tunnel(self, reader, writer, host, port):
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await self._relay(reader, writer, host, port, first_payload=None)

    # ------------------------------------------------------------ upstream

    async def _relay(self, c_reader, c_writer, host, port, first_payload):
        last_err = None
        for _ in range(self.cfg.gateway_retries):
            p = self.pool.acquire(https_only=(port == 443))
            if p is None:
                break
            try:
                up_r, up_w = await asyncio.wait_for(
                    self._open_upstream(p, host, port), timeout=10)
            except Exception as e:
                last_err = e
                self.pool.report(p.key, False)
                continue
            try:
                if first_payload:
                    up_w.write(first_payload)
                    await up_w.drain()
                c2u = asyncio.ensure_future(self._pipe(c_reader, up_w))
                u2c = asyncio.ensure_future(self._pipe(up_r, c_writer))
                done, pending = await asyncio.wait(
                    {c2u, u2c}, timeout=self.cfg.gateway_timeout,
                    return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                self.pool.report(p.key, bool(done))
                return
            except Exception as e:
                last_err = e
                self.pool.report(p.key, False)
            finally:
                try:
                    up_w.close()
                except Exception:
                    pass

        if first_payload is not None:   # plain HTTP: client still awaits a response
            try:
                c_writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n"
                               b"Connection: close\r\n\r\n")
                await c_writer.drain()
            except Exception:
                pass
        if last_err:
            log.debug("relay failed for %s:%d: %s", host, port, last_err)

    @staticmethod
    async def _pipe(src_reader, dst_writer):
        try:
            while True:
                data = await src_reader.read(65536)
                if not data:
                    break
                dst_writer.write(data)
                await dst_writer.drain()
        except Exception:
            pass
        finally:
            try:
                if dst_writer.can_write_eof():
                    dst_writer.write_eof()
            except Exception:
                pass

    async def _open_upstream(self, p: Proxy, host: str, port: int):
        if p.protocol == "http":
            r, w = await asyncio.open_connection(p.host, p.port, limit=1 << 16)
            w.write((f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
                     f"User-Agent: ProxyForge\r\n\r\n").encode())
            await w.drain()
            resp = await r.readuntil(b"\r\n\r\n")
            status = resp.split(b"\r\n", 1)[0]
            if b" 200" not in status and b" 201" not in status:
                w.close()
                raise RuntimeError(f"upstream CONNECT refused: {status[:60]!r}")
            return r, w

        if p.protocol == "socks5":
            r, w = await asyncio.open_connection(p.host, p.port, limit=1 << 16)
            w.write(b"\x05\x01\x00")
            await w.drain()
            ver, meth = await r.readexactly(2)
            if ver != 5 or meth != 0:
                w.close()
                raise RuntimeError("socks5 handshake failed")
            try:
                addr, atyp = bytes(int(x) for x in host.split(".")), b"\x01"
                if len(addr) != 4:
                    raise ValueError
            except Exception:
                addr, atyp = len(host).to_bytes(1, "big") + host.encode(), b"\x03"
            w.write(b"\x05\x01\x00" + atyp + addr + port.to_bytes(2, "big"))
            await w.drain()
            rep = await r.readexactly(4)
            if rep[1] != 0:
                w.close()
                raise RuntimeError(f"socks5 connect refused ({rep[1]})")
            if rep[3:4] == b"\x01":
                await r.readexactly(6)
            elif rep[3:4] == b"\x03":
                n = (await r.readexactly(1))[0]
                await r.readexactly(n + 2)
            else:
                await r.readexactly(18)
            return r, w

        if p.protocol == "socks4":
            loop = asyncio.get_running_loop()
            ip_bytes = await loop.run_in_executor(
                None, lambda: socket.inet_aton(socket.gethostbyname(host)))
            r, w = await asyncio.open_connection(p.host, p.port, limit=1 << 16)
            w.write(b"\x04\x01" + port.to_bytes(2, "big") + ip_bytes + b"\x00")
            await w.drain()
            resp = await r.readexactly(8)
            if resp[1] != 0x5A:
                w.close()
                raise RuntimeError("socks4 connect refused")
            return r, w

        raise RuntimeError(f"unsupported upstream protocol {p.protocol}")
