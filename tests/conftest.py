"""Fixtures: servidores locais que simulam hops SOCKS5/HTTP e um destino echo."""
import asyncio
import socket
from contextlib import suppress

import pytest

from ghostroute.core.relay import relay
from ghostroute.protocols import socks5


async def echo_handler(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


def make_socks_hop(seen: list | None = None, credentials=None):
    """Hop SOCKS5 mínimo: faz o handshake e conecta direto ao destino pedido."""

    async def handler(reader, writer):
        try:
            req = await socks5.server_handshake(reader, writer, credentials)
        except (socks5.Socks5Error, asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return
        if seen is not None:
            seen.append((req.host, req.port))
        try:
            up_r, up_w = await asyncio.open_connection(req.host, req.port)
        except OSError:
            await socks5.send_reply(writer, socks5.REP_CONNECTION_REFUSED)
            writer.close()
            return
        await socks5.send_reply(writer, socks5.REP_SUCCEEDED)
        await relay(reader, writer, up_r, up_w)

    return handler


def make_http_hop(seen: list | None = None):
    """Hop HTTP CONNECT mínimo."""

    async def handler(reader, writer):
        line = await reader.readline()
        _method, target, _ver = line.decode().split(" ")
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        host, port = target.rsplit(":", 1)
        host = host.strip("[]")
        if seen is not None:
            seen.append((host, int(port)))
        try:
            up_r, up_w = await asyncio.open_connection(host, int(port))
        except OSError:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await relay(reader, writer, up_r, up_w)

    return handler


@pytest.fixture
async def tcp_server():
    """Factory: ``port = await tcp_server(handler)``."""
    servers: list[asyncio.Server] = []

    async def factory(handler) -> int:
        srv = await asyncio.start_server(handler, "127.0.0.1", 0)
        servers.append(srv)
        return srv.sockets[0].getsockname()[1]

    yield factory
    for srv in servers:
        srv.close()
        if hasattr(srv, "close_clients"):
            srv.close_clients()
        with suppress(Exception):
            await asyncio.wait_for(srv.wait_closed(), 2)


@pytest.fixture
def dead_port() -> int:
    """Uma porta local onde (quase certamente) ninguém está escutando."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

