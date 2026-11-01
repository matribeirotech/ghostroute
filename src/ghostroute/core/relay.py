"""Cópia bidirecional de dados entre duas conexões."""
from __future__ import annotations

import asyncio
from contextlib import suppress

BUFFER_SIZE = 64 * 1024


async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter, bufsize: int) -> int:
    total = 0
    try:
        while data := await src.read(bufsize):
            dst.write(data)
            total += len(data)
            await dst.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        # Propaga o half-close (FIN) para o outro lado.
        with suppress(Exception):
            if dst.can_write_eof():
                dst.write_eof()
    return total


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with suppress(Exception):
        await writer.wait_closed()


async def relay(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    remote_reader: asyncio.StreamReader,
    remote_writer: asyncio.StreamWriter,
    bufsize: int = BUFFER_SIZE,
) -> tuple[int, int]:
    """Copia dados nos dois sentidos até ambos fecharem.

    Retorna ``(bytes_enviados, bytes_recebidos)`` do ponto de vista do cliente.
    """
    try:
        sent, received = await asyncio.gather(
            _pipe(client_reader, remote_writer, bufsize),
            _pipe(remote_reader, client_writer, bufsize),
        )
    finally:
        await asyncio.gather(_close(client_writer), _close(remote_writer))
    return sent, received

