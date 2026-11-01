"""Integração com o serviço Tor e controle de circuito via ControlPort."""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_SOCKS_PORT = 9050
DEFAULT_CONTROL_PORT = 9051


class TorError(Exception):
    """Erro de comunicação com o Tor ou porta de controle."""


@dataclass
class TorStatus:
    is_running: bool
    control_available: bool
    version: str | None = None
    circuit_established: bool = False


async def check_tor_socks(host: str = "127.0.0.1", port: int = DEFAULT_SOCKS_PORT, timeout: float = 2.0) -> bool:
    """Verifica se o proxy SOCKS do Tor está ativo e escutando."""
    try:
        async with asyncio.timeout(timeout):
            _reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()
            return True
    except (OSError, TimeoutError):
        return False


class TorControlClient:
    """Cliente mínimo para o protocolo Tor ControlPort (RFC-like text protocol)."""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_CONTROL_PORT, password: str = ""):
        self.host = host
        self.port = port
        self.password = password
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self, timeout: float = 3.0) -> None:
        try:
            async with asyncio.timeout(timeout):
                self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        except (OSError, TimeoutError) as exc:
            raise TorError(f"não foi possível conectar ao Tor ControlPort ({self.host}:{self.port}): {exc}") from exc

    async def close(self) -> None:
        if self._writer:
            self._writer.close()
            with suppress(Exception):
                await self._writer.wait_closed()
            self._writer = None
            self._reader = None

    async def _send_cmd(self, cmd: str) -> list[str]:
        if not self._writer or not self._reader:
            raise TorError("não conectado ao Tor ControlPort")

        line = f"{cmd.strip()}\r\n"
        self._writer.write(line.encode("ascii"))
        await self._writer.drain()

        lines: list[str] = []
        while True:
            resp = await self._reader.readline()
            if not resp:
                raise TorError("conexão encerrada pelo Tor ControlPort")
            text = resp.decode("ascii", errors="replace").strip()
            lines.append(text)
            # Fim de resposta do protocolo Tor Control: código de 3 dígitos seguido de espaço
            if len(text) >= 4 and text[:3].isdigit() and text[3] == " ":
                break

        status_code = int(lines[-1][:3])
        if status_code != 250:
            msg = lines[-1][4:] if len(lines[-1]) > 4 else f"status {status_code}"
            raise TorError(f"Tor ControlPort recusou comando '{cmd}': {msg}")

        return lines

    async def authenticate(self) -> None:
        """Autentica na porta de controle usando AUTHENTICATE."""
        # Se houver senha, usa aspas com escape simples; caso contrário vazio ""
        escaped_pwd = self.password.replace('"', '\\"')
        await self._send_cmd(f'AUTHENTICATE "{escaped_pwd}"')

    async def new_nym(self) -> None:
        """Envia sinal NEWNYM para alternar o circuito Tor e obter novo IP de saída."""
        await self._send_cmd("SIGNAL NEWNYM")
        log.info("sinal NEWNYM enviado com sucesso; novo circuito Tor solicitado")

    async def get_info(self, key: str) -> str:
        """Consulta variáveis de estado via GETINFO."""
        lines = await self._send_cmd(f"GETINFO {key}")
        for line in lines:
            if line.startswith((f"250-{key}=", f"250 {key}=")):
                return line.split("=", 1)[1]
        return ""


async def new_circuit(
    control_host: str = "127.0.0.1",
    control_port: int = DEFAULT_CONTROL_PORT,
    password: str = "",
) -> None:
    """Atalho para conectar, autenticar e requisitar novo circuito Tor."""
    client = TorControlClient(control_host, control_port, password)
    await client.connect()
    try:
        await client.authenticate()
        await client.new_nym()
    finally:
        await client.close()
