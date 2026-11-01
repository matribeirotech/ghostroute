"""Testes de configuração e da CLI."""
import pytest

from ghostroute import cli
from ghostroute.config import ConfigError, load_config, parse_config

EXAMPLE = """
[server]
listen_host = "127.0.0.1"
listen_port = 1081
username = "kali"
password = "s3cret"

[chain]
mode = "dynamic"
timeout = 5
proxies_file = "proxies.txt"

[[chain.hops]]
url = "socks5://127.0.0.1:9050"

[[chain.hops]]
url = "http://10.0.0.2:3128"
"""


def test_load_config(tmp_path):
    path = tmp_path / "ghostroute.toml"
    path.write_text(EXAMPLE, encoding="utf-8")
    cfg = load_config(path)
    cfg.validate()
    assert cfg.server.listen_port == 1081
    assert cfg.server.credentials.username == "kali"
    assert "s3cret" not in repr(cfg)
    assert cfg.chain.mode == "dynamic"
    assert cfg.chain.hops == ["socks5://127.0.0.1:9050", "http://10.0.0.2:3128"]
    assert cfg.chain.proxies_file == str(tmp_path / "proxies.txt")


def test_hops_as_strings():
    cfg = parse_config({"chain": {"hops": ["socks5://a:1", "http://b:2"]}})
    assert cfg.chain.hops == ["socks5://a:1", "http://b:2"]


@pytest.mark.parametrize(
    "data",
    [
        {"server": {"porta": 1}},
        {"extra": {}},
        {"chain": {"hops": [123]}},
    ],
)
def test_parse_errors(data):
    with pytest.raises(ConfigError):
        parse_config(data)


@pytest.mark.parametrize(
    "data",
    [
        {"chain": {"hops": []}},
        {"chain": {"hops": ["socks5://a:1"], "mode": "turbo"}},
        {"chain": {"hops": ["socks5://a:1"], "timeout": 0}},
        {"server": {"listen_port": 70000}, "chain": {"hops": ["socks5://a:1"]}},
        {"server": {"password": "x"}, "chain": {"hops": ["socks5://a:1"]}},
    ],
)
def test_validation_errors(data):
    with pytest.raises(ConfigError):
        parse_config(data).validate()


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nao-existe.toml")


def test_cli_overrides(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(EXAMPLE, encoding="utf-8")
    args = cli.build_parser().parse_args(
        ["start", "-c", str(path), "-H", "socks5://127.0.0.1:9150", "-p", "2000", "-m", "strict"]
    )
    cfg = cli._load_config(args)
    assert cfg.chain.hops == ["socks5://127.0.0.1:9150"]
    assert cfg.chain.proxies_file is None
    assert cfg.server.listen_port == 2000
    assert cfg.chain.mode == "strict"


def test_cli_invalid_hop_returns_error():
    assert cli.main(["start", "-H", "ftp://x:1", "-q"]) == 2

