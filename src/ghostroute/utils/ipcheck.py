"""Utilitário para verificação de IP público e detecção de vazamento."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from ghostroute.core.chain import ProxyChain

log = logging.getLogger(__name__)

IP_SERVICES = [
    "http://api.ipify.org?format=json",
    "http://ifconfig.me/all.json",
    "http://icanhazip.com",
]


@dataclass(frozen=True, slots=True)
class IPInfo:
    ip: str
    country: str | None = None
    org: str | None = None
    raw: str | None = None

    def __str__(self) -> str:
        details = []
        if self.country:
            details.append(self.country)
        if self.org:
            details.append(self.org)
        extra = f" ({', '.join(details)})" if details else ""
        return f"{self.ip}{extra}"


async def _http_get(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    host: str,
    path: str,
    timeout: float = 8.0,
) -> str:
    """Envia requisição HTTP/1.1 simples e lê o corpo da resposta."""
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "User-Agent: ghostroute-cli\r\n"
        "Accept: application/json, text/plain, */*\r\n"
        "Connection: close\r\n\r\n"
    ).encode()

    try:
        async with asyncio.timeout(timeout):
            writer.write(req)
            await writer.drain()

            status_line = await reader.readline()
            if not status_line:
                raise ConnectionError("resposta vazia do servidor")

            # Pula cabeçalhos até a linha em branco
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break

            body = await reader.read()
            return body.decode("utf-8", errors="replace").strip()
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()


def _parse_ip_response(text: str) -> IPInfo:
    """Extrai informações de IP do texto retornado pelo serviço."""
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        with suppress(json.JSONDecodeError, KeyError, TypeError):
            data = json.loads(text)
            ip = data.get("ip") or data.get("ip_addr") or data.get("query")
            country = data.get("country") or data.get("country_code")
            org = data.get("org") or data.get("isp")
            if ip:
                return IPInfo(ip=str(ip).strip(), country=country, org=org, raw=text)
    # Formato texto puro
    first_line = text.splitlines()[0].strip() if text else ""
    return IPInfo(ip=first_line, raw=text)


async def fetch_ip_direct(service_url: str = "http://api.ipify.org?format=json") -> IPInfo:
    """Obtém o IP público diretamente pela interface de rede local."""
    parsed = urlsplit(service_url)
    host = parsed.hostname or "api.ipify.org"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    reader, writer = await asyncio.open_connection(host, port)
    body = await _http_get(reader, writer, host, path)
    return _parse_ip_response(body)


async def fetch_ip_via_chain(
    chain: ProxyChain,
    service_url: str = "http://api.ipify.org?format=json",
) -> IPInfo:
    """Obtém o IP público roteado através de toda a cadeia de proxies."""
    parsed = urlsplit(service_url)
    host = parsed.hostname or "api.ipify.org"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    reader, writer, _route = await chain.connect(host, port)
    body = await _http_get(reader, writer, host, path)
    return _parse_ip_response(body)
