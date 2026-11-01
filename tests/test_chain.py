"""Testes da cadeia de proxies e do servidor local (ponta a ponta)."""
import asyncio

import pytest

from ghostroute.core import ChainError, ChainMode, Hop, ProxyChain, ProxyServer
from ghostroute.protocols import socks5
from ghostroute.protocols.socks5 import Credentials

from .conftest import echo_handler, make_http_hop, make_socks_hop


async def roundtrip(reader, writer, payload=b"ping-ghostroute") -> bytes:
    writer.write(payload)
    await writer.drain()
    data = await asyncio.wait_for(reader.readexactly(len(payload)), 5)
    writer.close()
    return data


# ------------------------------------------------------------------- Hop
def test_hop_from_url():
    hop = Hop.from_url("socks5h://user:p%40ss@10.0.0.1:9050")
    assert (hop.scheme, hop.host, hop.port) == ("socks5", "10.0.0.1", 9050)
    assert hop.credentials == Credentials("user", "p@ss")


def test_hop_default_ports():
    assert Hop.from_url("socks5://proxy").port == 1080
    assert Hop.from_url("http://proxy").port == 8080


def test_hop_hides_credentials():
    hop = Hop.from_url("http://admin:segredo@proxy:3128")
    assert "segredo" not in str(hop) and "segredo" not in repr(hop)


@pytest.mark.parametrize("url", ["ftp://x:1", "socks5://:1080", "socks4://x:1", "socks5://x:99999"])
def test_hop_invalid(url):
    with pytest.raises(ValueError):
        Hop.from_url(url)


def test_chain_requires_hops():
    with pytest.raises(ValueError):
        ProxyChain([])


def test_random_selects_chain_len():
    hops = [Hop("socks5", "127.0.0.1", p) for p in range(1, 6)]
    chain = ProxyChain(hops, ChainMode.RANDOM, chain_len=2)
    picked = chain.select()
    assert len(picked) == 2 and set(picked) <= set(hops)


# ------------------------------------------------------- cadeia real local
async def test_two_socks_hops(tcp_server):
    seen1, seen2 = [], []
    echo = await tcp_server(echo_handler)
    p1 = await tcp_server(make_socks_hop(seen1))
    p2 = await tcp_server(make_socks_hop(seen2))

    chain = ProxyChain([Hop("socks5", "127.0.0.1", p1), Hop("socks5", "127.0.0.1", p2)])
    reader, writer, route = await chain.connect("127.0.0.1", echo)

    assert await roundtrip(reader, writer) == b"ping-ghostroute"
    assert seen1 == [("127.0.0.1", p2)]   # hop 1 só conhece o hop 2
    assert seen2 == [("127.0.0.1", echo)]  # hop 2 conhece o destino
    assert len(route) == 2


async def test_socks_then_http_hop(tcp_server):
    echo = await tcp_server(echo_handler)
    p1 = await tcp_server(make_socks_hop())
    p2 = await tcp_server(make_http_hop())
    chain = ProxyChain([Hop("socks5", "127.0.0.1", p1), Hop("http", "127.0.0.1", p2)])
    reader, writer, _ = await chain.connect("127.0.0.1", echo)
    assert await roundtrip(reader, writer) == b"ping-ghostroute"


async def test_hop_with_auth(tcp_server):
    creds = Credentials("kali", "s3cret")
    echo = await tcp_server(echo_handler)
    p1 = await tcp_server(make_socks_hop(credentials=creds))
    chain = ProxyChain([Hop("socks5", "127.0.0.1", p1, creds)])
    reader, writer, _ = await chain.connect("127.0.0.1", echo)
    assert await roundtrip(reader, writer) == b"ping-ghostroute"


async def test_domain_is_sent_to_hop_not_resolved(tcp_server):
    seen = []
    p1 = await tcp_server(make_socks_hop(seen))
    chain = ProxyChain([Hop("socks5", "127.0.0.1", p1)])
    with pytest.raises(ChainError):  # domínio .invalid nunca resolve
        await chain.connect("ghostroute-test.invalid", 80)
    assert seen == [("ghostroute-test.invalid", 80)]  # DNS feito no hop, não localmente


async def test_strict_fails_on_dead_hop(tcp_server, dead_port):
    echo = await tcp_server(echo_handler)
    p1 = await tcp_server(make_socks_hop())
    chain = ProxyChain([Hop("socks5", "127.0.0.1", dead_port), Hop("socks5", "127.0.0.1", p1)])
    with pytest.raises(ChainError) as info:
        await chain.connect("127.0.0.1", echo)
    assert info.value.hop == Hop("socks5", "127.0.0.1", dead_port)


@pytest.mark.parametrize("dead_first", [True, False])
async def test_dynamic_skips_dead_hop(tcp_server, dead_port, dead_first):
    echo = await tcp_server(echo_handler)
    live = Hop("socks5", "127.0.0.1", await tcp_server(make_socks_hop()))
    dead = Hop("socks5", "127.0.0.1", dead_port)
    hops = [dead, live] if dead_first else [live, dead]

    chain = ProxyChain(hops, ChainMode.DYNAMIC)
    reader, writer, route = await chain.connect("127.0.0.1", echo)
    assert route == [live]
    assert await roundtrip(reader, writer) == b"ping-ghostroute"


async def test_target_unreachable(tcp_server, dead_port):
    p1 = await tcp_server(make_socks_hop())
    chain = ProxyChain([Hop("socks5", "127.0.0.1", p1)], ChainMode.DYNAMIC)
    with pytest.raises(ChainError) as info:
        await chain.connect("127.0.0.1", dead_port)
    assert info.value.hop is None
    assert info.value.reply == socks5.REP_CONNECTION_REFUSED


# ------------------------------------------------------- servidor completo
async def test_server_end_to_end(tcp_server):
    echo = await tcp_server(echo_handler)
    p1 = await tcp_server(make_socks_hop())
    p2 = await tcp_server(make_http_hop())
    chain = ProxyChain([Hop("socks5", "127.0.0.1", p1), Hop("http", "127.0.0.1", p2)])

    server = ProxyServer(chain, "127.0.0.1", 0)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection(*server.address)
        await socks5.client_connect(reader, writer, "127.0.0.1", echo)
        assert await roundtrip(reader, writer, b"x" * 200_000) == b"x" * 200_000
    finally:
        await server.close()


async def test_server_requires_auth(tcp_server):
    p1 = await tcp_server(make_socks_hop())
    creds = Credentials("kali", "ok")
    server = ProxyServer(ProxyChain([Hop("socks5", "127.0.0.1", p1)]), "127.0.0.1", 0, creds)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection(*server.address)
        with pytest.raises(socks5.Socks5Error):
            await socks5.client_connect(reader, writer, "127.0.0.1", 80)
        writer.close()
    finally:
        await server.close()


async def test_server_reports_chain_failure(tcp_server, dead_port):
    server = ProxyServer(ProxyChain([Hop("socks5", "127.0.0.1", dead_port)]), "127.0.0.1", 0)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection(*server.address)
        with pytest.raises(socks5.Socks5Error) as info:
            await socks5.client_connect(reader, writer, "example.com", 80)
        assert info.value.reply == socks5.REP_NETWORK_UNREACHABLE
        writer.close()
    finally:
        await server.close()

