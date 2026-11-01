"""Montagem da cadeia de proxies (hop 1 -> hop 2 -> ... -> destino)."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import random
import socket
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from ghostroute.protocols import http_connect, socks5
from ghostroute.protocols.http_connect import HttpProxyError
from ghostroute.protocols.socks5 import Credentials, Socks5Error
from ghostroute.providers.proxy_list import load_proxy_list

if TYPE_CHECKING:
    from ghostroute.config import ChainConfig

log = logging.getLogger(__name__)

SCHEMES = {"socks5": "socks5", "socks5h": "socks5", "http": "http"}
DEFAULT_PORTS = {"socks5": 1080, "http": 8080}


class ChainMode(str, Enum):
    STRICT = "strict"
    DYNAMIC = "dynamic"
    RANDOM = "random"


class ChainError(Exception):
    """Falha ao montar a cadeia.

    ``reply`` é o código SOCKS5 a devolver ao cliente; ``hop`` é o hop
    considerado culpado (``None`` quando o problema é o destino final).
    """

    def __init__(
        self, message: str, reply: int = socks5.REP_GENERAL_FAILURE, hop: Hop | None = None
    ) -> None:
        super().__init__(message)
        self.reply = reply
        self.hop = hop


@dataclass(frozen=True, slots=True, repr=False)
class Hop:
    scheme: str
    host: str
    port: int
    credentials: Credentials | None = None

    @classmethod
    def from_url(cls, url: str) -> Hop:
        parts = urlsplit(url.strip())
        scheme = SCHEMES.get(parts.scheme.lower())
        if scheme is None:
            raise ValueError(f"esquema não suportado em {url!r} (use socks5:// ou http://)")
        if not parts.hostname:
            raise ValueError(f"host ausente em {url!r}")
        try:
            port = parts.port or DEFAULT_PORTS[scheme]
        except ValueError as exc:
            raise ValueError(f"porta inválida em {url!r}") from exc
        creds = None
        if parts.username is not None:
            creds = Credentials(unquote(parts.username), unquote(parts.password or ""))
        return cls(scheme, parts.hostname, port, creds)

    def __str__(self) -> str:  # sem credenciais
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}"

    def __repr__(self) -> str:
        return f"Hop({self})"


Connection = tuple[asyncio.StreamReader, asyncio.StreamWriter, list[Hop]]


class ProxyChain:
    def __init__(
        self,
        hops: list[Hop],
        mode: ChainMode | str = ChainMode.STRICT,
        timeout: float = 10.0,
        chain_len: int | None = None,
        remote_dns: bool = True,
    ) -> None:
        if not hops:
            raise ValueError("a cadeia precisa de pelo menos um hop")
        self.hops = list(hops)
        self.mode = ChainMode(mode)
        self.timeout = timeout
        self.chain_len = chain_len or len(self.hops)
        if not 1 <= self.chain_len <= len(self.hops):
            raise ValueError(f"chain_len deve estar entre 1 e {len(self.hops)}")
        self.remote_dns = remote_dns

    @classmethod
    def from_config(cls, cfg: ChainConfig) -> ProxyChain:
        urls = list(cfg.hops)
        if cfg.proxies_file:
            urls += load_proxy_list(cfg.proxies_file)
        return cls(
            [Hop.from_url(u) for u in urls],
            mode=cfg.mode,
            timeout=cfg.timeout,
            chain_len=cfg.chain_len,
            remote_dns=cfg.remote_dns,
        )

    def describe(self) -> str:
        route = " -> ".join(str(h) for h in self.hops)
        if self.mode is ChainMode.RANDOM:
            return f"{self.chain_len} de [{route}]"
        return route

    def select(self) -> list[Hop]:
        if self.mode is ChainMode.RANDOM:
            return random.sample(self.hops, self.chain_len)
        return list(self.hops)

    async def connect(self, host: str, port: int) -> Connection:
        """Abre um túnel até ``host:port`` atravessando a cadeia."""
        if not self.remote_dns:
            host = await self._resolve(host, port)
        hops = self.select()
        if self.mode is not ChainMode.DYNAMIC:
            return await self._build(hops, host, port)

        last_error: ChainError | None = None
        while hops:
            try:
                return await self._build(hops, host, port)
            except ChainError as exc:
                if exc.hop is None or exc.hop not in hops:
                    raise
                log.warning("hop %s indisponível (%s); tentando sem ele", exc.hop, exc)
                hops.remove(exc.hop)
                last_error = exc
        raise ChainError(f"nenhum hop disponível ({last_error})", socks5.REP_NETWORK_UNREACHABLE)

    async def _build(self, hops: list[Hop], host: str, port: int) -> Connection:
        first = hops[0]
        try:
            async with asyncio.timeout(self.timeout):
                reader, writer = await asyncio.open_connection(first.host, first.port)
        except (OSError, TimeoutError) as exc:
            raise ChainError(
                f"falha ao conectar em {first}: {exc or 'timeout'}",
                socks5.REP_NETWORK_UNREACHABLE,
                hop=first,
            ) from exc

        try:
            for index, hop in enumerate(hops):
                nxt = hops[index + 1] if index + 1 < len(hops) else None
                target_host, target_port = (nxt.host, nxt.port) if nxt else (host, port)
                await self._handshake(hop, nxt, reader, writer, target_host, target_port)
        except BaseException:
            writer.close()
            raise
        return reader, writer, hops

    async def _handshake(
        self,
        hop: Hop,
        nxt: Hop | None,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        host: str,
        port: int,
    ) -> None:
        try:
            async with asyncio.timeout(self.timeout):
                if hop.scheme == "socks5":
                    await socks5.client_connect(reader, writer, host, port, hop.credentials)
                else:
                    await http_connect.client_connect(reader, writer, host, port, hop.credentials)
        except Socks5Error as exc:
            # Auth/protocolo: culpa do hop. Recusa ao conectar: culpa do próximo.
            own_fault = exc.reply in (socks5.REP_NOT_ALLOWED, socks5.REP_GENERAL_FAILURE)
            raise ChainError(str(exc), exc.reply, hop=hop if own_fault else nxt) from exc
        except HttpProxyError as exc:
            if exc.status in (None, 407):
                raise ChainError(f"{hop}: {exc}", socks5.REP_NOT_ALLOWED, hop=hop) from exc
            raise ChainError(f"{hop}: {exc}", socks5.REP_HOST_UNREACHABLE, hop=nxt) from exc
        except (TimeoutError, asyncio.IncompleteReadError, OSError) as exc:
            reason = "timeout" if isinstance(exc, TimeoutError) else (str(exc) or type(exc).__name__)
            raise ChainError(f"{hop}: {reason}", socks5.REP_HOST_UNREACHABLE, hop=hop) from exc

    @staticmethod
    async def _resolve(host: str, port: int) -> str:
        try:
            ipaddress.ip_address(host)
            return host
        except ValueError:
            pass
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
        except socket.gaierror as exc:
            raise ChainError(f"não foi possível resolver {host}: {exc}",
                             socks5.REP_HOST_UNREACHABLE) from exc
        return infos[0][4][0]

