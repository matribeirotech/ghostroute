"""Configuração do ghostroute (arquivo TOML)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ghostroute.protocols.socks5 import Credentials

MODES = ("strict", "dynamic", "random")

DEFAULT_PATHS = (
    Path("config/ghostroute.toml"),
    Path("~/.config/ghostroute/config.toml").expanduser(),
    Path("/etc/ghostroute/config.toml"),
)


class ConfigError(ValueError):
    pass


@dataclass
class ServerConfig:
    listen_host: str = "127.0.0.1"
    listen_port: int = 1080
    username: str | None = None
    password: str | None = field(default=None, repr=False)

    @property
    def credentials(self) -> Credentials | None:
        if not self.username:
            return None
        return Credentials(self.username, self.password or "")


@dataclass
class ChainConfig:
    mode: str = "strict"
    timeout: float = 10.0
    remote_dns: bool = True
    chain_len: int | None = None
    hops: list[str] = field(default_factory=list)
    proxies_file: str | None = None


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    chain: ChainConfig = field(default_factory=ChainConfig)

    def validate(self) -> None:
        s, c = self.server, self.chain
        if not isinstance(s.listen_port, int) or not 0 <= s.listen_port <= 65535:
            raise ConfigError(f"listen_port inválida: {s.listen_port!r}")
        if s.password and not s.username:
            raise ConfigError("password definido sem username")
        if c.mode not in MODES:
            raise ConfigError(f"mode inválido: {c.mode!r} (use {', '.join(MODES)})")
        if not isinstance(c.timeout, int | float) or c.timeout <= 0:
            raise ConfigError("timeout deve ser um número positivo")
        if c.chain_len is not None and (not isinstance(c.chain_len, int) or c.chain_len < 1):
            raise ConfigError("chain_len deve ser um inteiro >= 1")
        if not c.hops and not c.proxies_file:
            raise ConfigError("nenhum hop configurado (use [[chain.hops]] ou --hop)")


def _build(cls: type, section: str, values: Any, **extra: Any) -> Any:
    if not isinstance(values, dict):
        raise ConfigError(f"[{section}] deve ser uma tabela")
    known = set(cls.__dataclass_fields__)
    unknown = set(values) - known
    if unknown:
        raise ConfigError(f"chave(s) desconhecida(s) em [{section}]: {', '.join(sorted(unknown))}")
    return cls(**values, **extra)


def parse_config(data: dict[str, Any]) -> Config:
    unknown = set(data) - {"server", "chain"}
    if unknown:
        raise ConfigError(f"seção(ões) desconhecida(s): {', '.join(sorted(unknown))}")

    chain = dict(data.get("chain", {}))
    hops: list[str] = []
    for item in chain.pop("hops", []):
        if isinstance(item, str):
            hops.append(item)
        elif isinstance(item, dict) and isinstance(item.get("url"), str):
            hops.append(item["url"])
        else:
            raise ConfigError("cada hop deve ser uma string ou uma tabela com 'url'")

    return Config(
        server=_build(ServerConfig, "server", data.get("server", {})),
        chain=_build(ChainConfig, "chain", chain, hops=hops),
    )


def load_config(path: str | Path) -> Config:
    p = Path(path).expanduser()
    try:
        with p.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"arquivo de configuração não encontrado: {p}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML inválido em {p}: {exc}") from None

    cfg = parse_config(data)
    # proxies_file relativo é resolvido a partir da pasta do arquivo de config
    if cfg.chain.proxies_file and not Path(cfg.chain.proxies_file).expanduser().is_absolute():
        cfg.chain.proxies_file = str(p.parent / cfg.chain.proxies_file)
    return cfg


def find_config() -> Path | None:
    return next((p for p in DEFAULT_PATHS if p.is_file()), None)

