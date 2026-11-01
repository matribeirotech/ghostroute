"""Carregamento de listas de proxies (um URL por linha)."""
from __future__ import annotations

from pathlib import Path


def load_proxy_list(path: str | Path) -> list[str]:
    """Lê o arquivo ignorando linhas em branco e comentários (``#``)."""
    lines = Path(path).expanduser().read_text(encoding="utf-8").splitlines()
    return [s for line in lines if (s := line.strip()) and not s.startswith("#")]

