"""Testes de resolução e checagem de IP."""
from ghostroute.core.chain import Hop, ProxyChain
from ghostroute.utils.ipcheck import _parse_ip_response, fetch_ip_direct, fetch_ip_via_chain

from .conftest import make_socks_hop


def test_parse_ip_json():
    raw = '{"ip": "198.51.100.42", "country": "BR", "org": "Telecom"}'
    info = _parse_ip_response(raw)
    assert info.ip == "198.51.100.42"
    assert info.country == "BR"
    assert info.org == "Telecom"
    assert "198.51.100.42" in str(info)


def test_parse_ip_plain_text():
    raw = "203.0.113.99\n"
    info = _parse_ip_response(raw)
    assert info.ip == "203.0.113.99"
    assert info.country is None


async def test_fetch_ip_direct_with_mock_server(tcp_server):
    async def fake_ip_service(reader, writer):
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        resp = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{"ip": "1.2.3.4"}'
        writer.write(resp)
        await writer.drain()
        writer.close()

    port = await tcp_server(fake_ip_service)
    info = await fetch_ip_direct(f"http://127.0.0.1:{port}/ip")
    assert info.ip == "1.2.3.4"


async def test_fetch_ip_via_chain(tcp_server):
    async def fake_ip_service(reader, writer):
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        resp = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{"ip": "9.9.9.9"}'
        writer.write(resp)
        await writer.drain()
        writer.close()

    service_port = await tcp_server(fake_ip_service)
    proxy_port = await tcp_server(make_socks_hop())

    chain = ProxyChain([Hop("socks5", "127.0.0.1", proxy_port)])
    info = await fetch_ip_via_chain(chain, f"http://127.0.0.1:{service_port}/ip")
    assert info.ip == "9.9.9.9"
