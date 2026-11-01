"""Ponto de entrada da CLI."""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from contextlib import suppress

from ghostroute import __version__
from ghostroute.config import DEFAULT_PATHS, MODES, Config, ConfigError, find_config, load_config
from ghostroute.core import ProxyChain, ProxyServer
from ghostroute.logger import setup_logging
from ghostroute.providers.tor import TorError, new_circuit
from ghostroute.utils.ipcheck import fetch_ip_direct, fetch_ip_via_chain

log = logging.getLogger("ghostroute")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="count", default=0, help="mais detalhes nos logs")
    common.add_argument("-q", "--quiet", action="store_true", help="mostra apenas avisos e erros")

    parser = argparse.ArgumentParser(
        prog="ghostroute", description="Proxy SOCKS5 local com encadeamento de proxies"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    paths = ", ".join(str(p) for p in DEFAULT_PATHS)

    # Comando start
    start = sub.add_parser("start", parents=[common], help="inicia o proxy SOCKS5 local")
    start.add_argument("-c", "--config", help=f"arquivo TOML (padrão: procura em {paths})")
    start.add_argument(
        "-H", "--hop", action="append", default=[], metavar="URL",
        help="hop da cadeia, em ordem (repetível; substitui os do arquivo). "
             "Ex.: -H socks5://127.0.0.1:9050",
    )
    start.add_argument("-l", "--listen", metavar="HOST", help="endereço de escuta")
    start.add_argument("-p", "--port", type=int, help="porta de escuta")
    start.add_argument("-m", "--mode", choices=MODES, help="modo da cadeia")
    start.add_argument("--timeout", type=float, help="timeout por hop (segundos)")
    start.add_argument("--chain-len", type=int, help="nº de hops sorteados no modo random")

    # Comando check-ip
    check_ip = sub.add_parser("check-ip", parents=[common], help="mostra o IP público visto pela cadeia")
    check_ip.add_argument("-c", "--config", help=f"arquivo TOML (padrão: procura em {paths})")
    check_ip.add_argument(
        "-H", "--hop", action="append", default=[], metavar="URL",
        help="hop da cadeia para checagem rápida sem arquivo de configuração",
    )
    check_ip.add_argument(
        "--no-direct", action="store_true", help="não consulta o IP real direto para comparação"
    )
    check_ip.add_argument(
        "--service", default="http://api.ipify.org?format=json", help="URL do serviço de resolução de IP"
    )

    # Comando new-circuit (Tor)
    circuit = sub.add_parser(
        "new-circuit", parents=[common], help="solicita novo circuito/IP de saída ao Tor (SIGNAL NEWNYM)"
    )
    circuit.add_argument("--control-host", default="127.0.0.1", help="host do Tor ControlPort")
    circuit.add_argument("--control-port", type=int, default=9051, help="porta do Tor ControlPort (padrão 9051)")
    circuit.add_argument("--password", default="", help="senha de autenticação do ControlPort (se exigido)")

    return parser


def _load_config(args: argparse.Namespace) -> Config:
    if getattr(args, "config", None):
        cfg = load_config(args.config)
    elif path := find_config():
        log.debug("usando configuração %s", path)
        cfg = load_config(path)
    elif getattr(args, "hop", None):
        cfg = Config()
    else:
        raise ConfigError("nenhuma configuração encontrada; use --config ou --hop")

    if getattr(args, "hop", None):
        cfg.chain.hops = list(args.hop)
        cfg.chain.proxies_file = None
    if getattr(args, "listen", None) is not None:
        cfg.server.listen_host = args.listen
    if getattr(args, "port", None) is not None:
        cfg.server.listen_port = args.port
    if getattr(args, "mode", None) is not None:
        cfg.chain.mode = args.mode
    if getattr(args, "timeout", None) is not None:
        cfg.chain.timeout = args.timeout
    if getattr(args, "chain_len", None) is not None:
        cfg.chain.chain_len = args.chain_len
    cfg.validate()
    return cfg


async def _serve(server: ProxyServer) -> None:
    await server.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError, AttributeError, ValueError):
            loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        await server.close()


def _cmd_start(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    chain = ProxyChain.from_config(cfg.chain)
    server = ProxyServer(
        chain, cfg.server.listen_host, cfg.server.listen_port, cfg.server.credentials
    )
    asyncio.run(_serve(server))
    return 0


async def _run_check_ip(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    chain = ProxyChain.from_config(cfg.chain)

    real_ip = None
    if not args.no_direct:
        print("[*] Verificando IP real local...")
        try:
            real_ip = await fetch_ip_direct(args.service)
            print(f"[-] IP real da máquina : {real_ip}")
        except (OSError, TimeoutError) as exc:
            log.warning("falha ao consultar IP real direto: %s", exc)

    print(f"[*] Roteando verificação através da cadeia ({chain.describe()})...")
    try:
        proxied_ip = await fetch_ip_via_chain(chain, args.service)
        print(f"[+] IP através da cadeia: {proxied_ip}")
    except (OSError, TimeoutError, ValueError) as exc:
        log.error("falha ao consultar IP via cadeia de proxies: %s", exc)
        return 1

    if real_ip and real_ip.ip:
        if real_ip.ip == proxied_ip.ip:
            print("[!] ALERTA: O IP mascarado é IDÊNTICO ao IP real! Tráfego não mascarado!")
            return 1
        print("[✓] SUCESSO: IP mascarado com êxito! O IP real está oculto.")

    return 0


def _cmd_check_ip(args: argparse.Namespace) -> int:
    return asyncio.run(_run_check_ip(args))


def _cmd_new_circuit(args: argparse.Namespace) -> int:
    async def _action():
        print(f"[*] Solicitando novo circuito ao Tor ({args.control_host}:{args.control_port})...")
        await new_circuit(args.control_host, args.control_port, args.password)
        print("[✓] Novo circuito estabelecido com sucesso! Seu IP de saída Tor foi renovado.")

    try:
        asyncio.run(_action())
        return 0
    except TorError as exc:
        log.error("%s", exc)
        return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose, args.quiet)
    try:
        if args.command == "start":
            return _cmd_start(args)
        if args.command == "check-ip":
            return _cmd_check_ip(args)
        if args.command == "new-circuit":
            return _cmd_new_circuit(args)
    except (ConfigError, ValueError) as exc:
        log.error("%s", exc)
        return 2
    except OSError as exc:
        log.error("erro de sistema: %s", exc)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
