"""Implementação do protocolo SOCKS5 (RFC 1928 + RFC 1929).

Contém os dois lados do protocolo:

* ``server_handshake`` - usado pelo proxy local para atender aplicações
  (navegador, curl, etc.) que se conectam em ``127.0.0.1:1080``.
* ``client_connect`` - usado para conversar com proxies SOCKS5 upstream
  (por exemplo o Tor em ``127.0.0.1:9050``).

Nomes de domínio são enviados ao upstream sem resolução local
(equivalente a ``socks5h``), evitando vazamento de DNS.
"""
from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass, field

try:  # Extensão Cython (opcional)
    from ghostroute._speedups._socks5 import pack_port
except ImportError:  # pragma: no cover - fallback em Python puro
    def pack_port(port: int) -> bytes:
        if not 0 <= port <= 65535:
            raise ValueError("porta inválida")
        return port.to_bytes(2, "big")


VERSION = 0x05

# Métodos de autenticação
METHOD_NO_AUTH = 0x00
METHOD_USERPASS = 0x02
METHOD_NO_ACCEPTABLE = 0xFF
USERPASS_VERSION = 0x01

# Comandos
CMD_CONNECT = 0x01
CMD_BIND = 0x02
CMD_UDP_ASSOCIATE = 0x03

# Tipos de endereço
ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04

# Códigos de resposta
REP_SUCCEEDED = 0x00
REP_GENERAL_FAILURE = 0x01
REP_NOT_ALLOWED = 0x02
REP_NETWORK_UNREACHABLE = 0x03
REP_HOST_UNREACHABLE = 0x04
REP_CONNECTION_REFUSED = 0x05
REP_TTL_EXPIRED = 0x06
REP_COMMAND_NOT_SUPPORTED = 0x07
REP_ADDRESS_NOT_SUPPORTED = 0x08

REPLY_MESSAGES = {
    REP_SUCCEEDED: "sucesso",
    REP_GENERAL_FAILURE: "falha geral do servidor SOCKS",
    REP_NOT_ALLOWED: "conexão não permitida pelas regras",
    REP_NETWORK_UNREACHABLE: "rede inalcançável",
    REP_HOST_UNREACHABLE: "host inalcançável",
    REP_CONNECTION_REFUSED: "conexão recusada",
    REP_TTL_EXPIRED: "TTL expirado",
    REP_COMMAND_NOT_SUPPORTED: "comando não suportado",
    REP_ADDRESS_NOT_SUPPORTED: "tipo de endereço não suportado",
}


class Socks5Error(Exception):
    """Erro de protocolo SOCKS5, carregando o código de resposta adequado."""

    def __init__(self, message: str, reply: int = REP_GENERAL_FAILURE) -> None:
        super().__init__(message)
        self.reply = reply


@dataclass(frozen=True, slots=True)
class Credentials:
    username: str
    password: str = field(repr=False)  # nunca aparece em logs


@dataclass(frozen=True, slots=True)
class Request:
    """Pedido CONNECT recebido de uma aplicação cliente."""

    command: int
    host: str
    port: int


# --------------------------------------------------------------------------- #
# Codificação de endereços
# --------------------------------------------------------------------------- #
def encode_address(host: str, port: int) -> bytes:
    """Codifica ``ATYP + ADDR + PORT`` conforme a RFC 1928."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raw = host.encode("idna")
        if not 0 < len(raw) <= 255:
            raise Socks5Error("nome de domínio inválido", REP_ADDRESS_NOT_SUPPORTED) from None
        return bytes((ATYP_DOMAIN, len(raw))) + raw + pack_port(port)
    atyp = ATYP_IPV4 if ip.version == 4 else ATYP_IPV6
    return bytes((atyp,)) + ip.packed + pack_port(port)


async def read_address(reader: asyncio.StreamReader, atyp: int) -> tuple[str, int]:
    """Lê ``ADDR + PORT`` do stream de acordo com o ``atyp``."""
    if atyp == ATYP_IPV4:
        host = str(ipaddress.IPv4Address(await reader.readexactly(4)))
    elif atyp == ATYP_IPV6:
        host = str(ipaddress.IPv6Address(await reader.readexactly(16)))
    elif atyp == ATYP_DOMAIN:
        length = (await reader.readexactly(1))[0]
        if length == 0:
            raise Socks5Error("domínio vazio", REP_ADDRESS_NOT_SUPPORTED)
        host = (await reader.readexactly(length)).decode("idna")
    else:
        raise Socks5Error(f"tipo de endereço desconhecido: {atyp:#04x}", REP_ADDRESS_NOT_SUPPORTED)
    port = int.from_bytes(await reader.readexactly(2), "big")
    return host, port


def build_reply(reply: int, host: str = "0.0.0.0", port: int = 0) -> bytes:
    """Monta a resposta ``VER REP RSV ATYP BND.ADDR BND.PORT``."""
    return bytes((VERSION, reply, 0x00)) + encode_address(host, port)


# --------------------------------------------------------------------------- #
# Lado servidor (proxy local)
# --------------------------------------------------------------------------- #
async def server_handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    credentials: Credentials | None = None,
) -> Request:
    """Negocia autenticação e lê o pedido do cliente.

    Não envia a resposta final: quem chama deve usar :func:`send_reply`
    após tentar abrir a conexão upstream.
    """
    version, nmethods = await reader.readexactly(2)
    if version != VERSION:
        raise Socks5Error(f"versão SOCKS não suportada: {version}")
    methods = await reader.readexactly(nmethods)

    wanted = METHOD_USERPASS if credentials else METHOD_NO_AUTH
    if wanted not in methods:
        writer.write(bytes((VERSION, METHOD_NO_ACCEPTABLE)))
        await writer.drain()
        raise Socks5Error("nenhum método de autenticação aceitável", REP_NOT_ALLOWED)

    writer.write(bytes((VERSION, wanted)))
    await writer.drain()

    if credentials:
        await _server_userpass_auth(reader, writer, credentials)

    version, command, _rsv, atyp = await reader.readexactly(4)
    if version != VERSION:
        raise Socks5Error(f"versão SOCKS não suportada: {version}")
    host, port = await read_address(reader, atyp)

    if command != CMD_CONNECT:
        await send_reply(writer, REP_COMMAND_NOT_SUPPORTED)
        raise Socks5Error(f"comando não suportado: {command:#04x}", REP_COMMAND_NOT_SUPPORTED)

    return Request(command=command, host=host, port=port)


async def _server_userpass_auth(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    credentials: Credentials,
) -> None:
    version = (await reader.readexactly(1))[0]
    if version != USERPASS_VERSION:
        raise Socks5Error("versão de autenticação inválida", REP_NOT_ALLOWED)
    username = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
    password = (await reader.readexactly((await reader.readexactly(1))[0])).decode()

    ok = username == credentials.username and password == credentials.password
    writer.write(bytes((USERPASS_VERSION, 0x00 if ok else 0x01)))
    await writer.drain()
    if not ok:
        raise Socks5Error("usuário ou senha inválidos", REP_NOT_ALLOWED)


async def send_reply(
    writer: asyncio.StreamWriter, reply: int, host: str = "0.0.0.0", port: int = 0
) -> None:
    writer.write(build_reply(reply, host, port))
    await writer.drain()


# --------------------------------------------------------------------------- #
# Lado cliente (proxy upstream)
# --------------------------------------------------------------------------- #
async def client_connect(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    host: str,
    port: int,
    credentials: Credentials | None = None,
) -> tuple[str, int]:
    """Pede a um proxy SOCKS5 upstream que abra um túnel até ``host:port``.

    ``reader``/``writer`` já devem estar conectados ao upstream (ou ao
    túnel do hop anterior, no caso de cadeias). Retorna o endereço
    ``BND`` informado pelo upstream.
    """
    methods = [METHOD_NO_AUTH] + ([METHOD_USERPASS] if credentials else [])
    writer.write(bytes((VERSION, len(methods), *methods)))
    await writer.drain()

    version, method = await reader.readexactly(2)
    if version != VERSION:
        raise Socks5Error(f"upstream respondeu versão inválida: {version}")
    if method == METHOD_USERPASS and credentials:
        user = credentials.username.encode()
        pwd = credentials.password.encode()
        if len(user) > 255 or len(pwd) > 255:
            raise Socks5Error("credenciais muito longas")
        writer.write(bytes((USERPASS_VERSION, len(user))) + user + bytes((len(pwd),)) + pwd)
        await writer.drain()
        _ver, status = await reader.readexactly(2)
        if status != 0x00:
            raise Socks5Error("upstream rejeitou as credenciais", REP_NOT_ALLOWED)
    elif method != METHOD_NO_AUTH:
        raise Socks5Error("upstream não aceitou nenhum método de autenticação", REP_NOT_ALLOWED)

    writer.write(bytes((VERSION, CMD_CONNECT, 0x00)) + encode_address(host, port))
    await writer.drain()

    version, reply, _rsv, atyp = await reader.readexactly(4)
    if version != VERSION:
        raise Socks5Error(f"upstream respondeu versão inválida: {version}")
    bound = await read_address(reader, atyp)
    if reply != REP_SUCCEEDED:
        reason = REPLY_MESSAGES.get(reply, f"código {reply:#04x}")
        raise Socks5Error(f"upstream recusou {host}:{port}: {reason}", reply)
    return bound

