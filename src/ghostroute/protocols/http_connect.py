"""Cliente HTTP CONNECT (RFC 9110, seção 9.3.6) para hops ``http://``."""
from __future__ import annotations

import asyncio
import base64

from ghostroute.protocols.socks5 import Credentials

MAX_HEADER_LINES = 100


class HttpProxyError(Exception):
    """Falha ao abrir túnel via HTTP CONNECT. ``status`` é o código HTTP, se houver."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def build_request(host: str, port: int, credentials: Credentials | None = None) -> bytes:
    target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}", "Proxy-Connection: keep-alive"]
    if credentials:
        token = base64.b64encode(f"{credentials.username}:{credentials.password}".encode()).decode()
        lines.append(f"Proxy-Authorization: Basic {token}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


async def client_connect(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    host: str,
    port: int,
    credentials: Credentials | None = None,
) -> None:
    """Pede ao proxy HTTP que abra um túnel TCP até ``host:port``."""
    writer.write(build_request(host, port, credentials))
    await writer.drain()

    status_line = await reader.readline()
    if not status_line:
        raise HttpProxyError("proxy HTTP fechou a conexão sem responder")
    parts = status_line.decode("latin-1").strip().split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        raise HttpProxyError(f"resposta HTTP inválida: {status_line[:80]!r}")
    status = int(parts[1])
    reason = parts[2] if len(parts) > 2 else ""

    for _ in range(MAX_HEADER_LINES):
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
    else:
        raise HttpProxyError("cabeçalhos de resposta muito longos")

    if not 200 <= status < 300:
        raise HttpProxyError(f"proxy HTTP recusou {host}:{port}: {status} {reason}".strip(), status)

