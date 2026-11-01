"""Testes do protocolo SOCKS5 usando servidores asyncio locais."""
import asyncio

import pytest

from ghostroute.protocols import socks5
from ghostroute.protocols.socks5 import Credentials, Socks5Error


# ----------------------------------------------------------------- encoding
def test_encode_ipv4():
    assert socks5.encode_address("127.0.0.1", 80) == b"\x01\x7f\x00\x00\x01\x00\x50"


def test_encode_ipv6():
    data = socks5.encode_address("::1", 443)
    assert data[0] == socks5.ATYP_IPV6 and len(data) == 1 + 16 + 2
    assert data[-2:] == b"\x01\xbb"


def test_encode_domain():
    assert socks5.encode_address("example.com", 1080) == b"\x03\x0bexample.com\x04\x38"


def test_encode_invalid_port():
    with pytest.raises(ValueError):
        socks5.encode_address("1.2.3.4", 70000)


def test_build_reply():
    assert socks5.build_reply(socks5.REP_SUCCEEDED) == b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"


async def test_read_address_roundtrip():
    for host, port in [("10.0.0.1", 22), ("::1", 8080), ("kali.org", 443)]:
        encoded = socks5.encode_address(host, port)
        reader = asyncio.StreamReader()
        reader.feed_data(encoded[1:])
        reader.feed_eof()
        assert await socks5.read_address(reader, encoded[0]) == (host, port)


# ------------------------------------------------------ server <-> client
async def _run_pair(server_creds=None, client_creds=None, target=("example.com", 443)):
    """Sobe um servidor com server_handshake e conecta usando client_connect."""
    received: dict = {}

    async def handler(reader, writer):
        try:
            req = await socks5.server_handshake(reader, writer, server_creds)
            received["request"] = req
            await socks5.send_reply(writer, socks5.REP_SUCCEEDED, "127.0.0.1", 5555)
        except Socks5Error as exc:
            received["error"] = exc
        finally:
            writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            bound = await socks5.client_connect(reader, writer, *target, credentials=client_creds)
        finally:
            writer.close()
        return bound, received
    finally:
        server.close()
        await server.wait_closed()


async def test_connect_no_auth():
    bound, received = await _run_pair()
    assert bound == ("127.0.0.1", 5555)
    assert received["request"] == socks5.Request(socks5.CMD_CONNECT, "example.com", 443)


async def test_connect_with_auth():
    creds = Credentials("kali", "s3cret")
    bound, received = await _run_pair(creds, creds, ("10.1.2.3", 80))
    assert bound == ("127.0.0.1", 5555)
    assert received["request"].host == "10.1.2.3"


async def test_connect_wrong_password():
    with pytest.raises(Socks5Error):
        await _run_pair(Credentials("kali", "ok"), Credentials("kali", "errada"))


async def test_connect_missing_auth():
    with pytest.raises(Socks5Error):
        await _run_pair(Credentials("kali", "ok"), None)


async def test_upstream_refusal():
    async def handler(reader, writer):
        await socks5.server_handshake(reader, writer)
        await socks5.send_reply(writer, socks5.REP_CONNECTION_REFUSED)
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    with pytest.raises(Socks5Error) as info:
        await socks5.client_connect(reader, writer, "example.com", 80)
    assert info.value.reply == socks5.REP_CONNECTION_REFUSED
    writer.close()
    server.close()
    await server.wait_closed()

