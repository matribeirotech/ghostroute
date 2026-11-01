"""Testes para o cliente de controle e integração do Tor."""
import pytest

from ghostroute.providers.tor import TorControlClient, TorError, check_tor_socks, new_circuit


async def test_check_tor_socks_inactive(dead_port):
    active = await check_tor_socks("127.0.0.1", dead_port, timeout=0.5)
    assert active is False


async def test_check_tor_socks_active(tcp_server):
    async def dummy(_reader, writer):
        writer.close()

    port = await tcp_server(dummy)
    active = await check_tor_socks("127.0.0.1", port, timeout=1.0)
    assert active is True


async def test_tor_control_authenticate_and_newnym(tcp_server):
    received_cmds = []

    async def mock_tor_control(reader, writer):
        while line := await reader.readline():
            cmd = line.decode().strip()
            received_cmds.append(cmd)
            if cmd.startswith("AUTHENTICATE") or cmd == "SIGNAL NEWNYM":
                writer.write(b"250 OK\r\n")
            elif cmd.startswith("GETINFO version"):
                writer.write(b"250-version=0.4.8.10\r\n250 OK\r\n")
            else:
                writer.write(b"510 Unrecognized command\r\n")
            await writer.drain()
        writer.close()

    port = await tcp_server(mock_tor_control)

    client = TorControlClient("127.0.0.1", port, password="test")
    await client.connect()
    try:
        await client.authenticate()
        await client.new_nym()
        ver = await client.get_info("version")
        assert ver == "0.4.8.10"
    finally:
        await client.close()

    assert any(c.startswith("AUTHENTICATE") for c in received_cmds)
    assert "SIGNAL NEWNYM" in received_cmds


async def test_tor_control_error_response(tcp_server):
    async def mock_tor_reject(reader, writer):
        await reader.readline()
        writer.write(b"515 Authentication failed: password incorrect\r\n")
        await writer.drain()
        writer.close()

    port = await tcp_server(mock_tor_reject)
    client = TorControlClient("127.0.0.1", port, password="wrong")
    await client.connect()
    with pytest.raises(TorError) as exc_info:
        await client.authenticate()
    assert "recusou comando" in str(exc_info.value)
    await client.close()


async def test_new_circuit_helper(tcp_server):
    async def mock_tor(reader, writer):
        while await reader.readline():
            writer.write(b"250 OK\r\n")
            await writer.drain()
        writer.close()

    port = await tcp_server(mock_tor)
    await new_circuit("127.0.0.1", port)
