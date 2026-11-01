"""Servidor SOCKS5 local que encaminha conexões pela cadeia de proxies."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
from contextlib import suppress

from ghostroute.core.chain import ChainError, ProxyChain
from ghostroute.core.relay import relay
from ghostroute.protocols import socks5
from ghostroute.protocols.socks5 import Credentials, Socks5Error

log = logging.getLogger(__name__)

HANDSHAKE_TIMEOUT = 15.0


class ProxyServer:
    def __init__(
        self,
        chain: ProxyChain,
        host: str = "127.0.0.1",
        port: int = 1080,
        credentials: Credentials | None = None,
    ) -> None:
        self.chain = chain
        self.host = host
        self.port = port
        self.credentials = credentials
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()

    @property
    def address(self) -> tuple[str, int]:
        return self.host, self.port

    async def start(self) -> None:
        if not _is_loopback(self.host) and self.credentials is None:
            log.warning(
                "escutando em %s sem autenticação: qualquer máquina da rede poderá usar o proxy",
                self.host,
            )
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]  # resolve porta 0
        log.info("ghostroute escutando em socks5://%s:%d", self.host, self.port)
        log.info("modo %s | cadeia: %s", self.chain.mode.value, self.chain.describe())

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        for writer in list(self._clients):
            writer.close()
        with suppress(Exception):
            await asyncio.wait_for(self._server.wait_closed(), timeout=5)
        self._server = None
        log.info("servidor encerrado")

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._clients.add(writer)
        peer = writer.get_extra_info("peername")
        try:
            await self._serve_client(reader, writer, peer)
        except Exception:  # nunca derrubar o servidor por causa de um cliente
            log.exception("erro inesperado atendendo %s", peer)
        finally:
            self._clients.discard(writer)
            writer.close()

    async def _serve_client(self, reader, writer, peer) -> None:
        try:
            async with asyncio.timeout(HANDSHAKE_TIMEOUT):
                request = await socks5.server_handshake(reader, writer, self.credentials)
        except (Socks5Error, asyncio.IncompleteReadError, ConnectionError, TimeoutError) as exc:
            log.debug("handshake falhou com %s: %s", peer, exc or type(exc).__name__)
            return

        target = f"{request.host}:{request.port}"
        try:
            remote_reader, remote_writer, route = await self.chain.connect(
                request.host, request.port
            )
        except ChainError as exc:
            log.warning("%s -> %s falhou: %s", peer, target, exc)
            with suppress(ConnectionError):
                await socks5.send_reply(writer, exc.reply)
            return

        try:
            await socks5.send_reply(writer, socks5.REP_SUCCEEDED)
        except ConnectionError:
            remote_writer.close()
            return

        log.info("%s -> %s via %s", peer, target, " -> ".join(map(str, route)))
        sent, received = await relay(reader, writer, remote_reader, remote_writer)
        log.debug("%s fechado (enviado %d B, recebido %d B)", target, sent, received)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
