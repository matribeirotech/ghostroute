# ghostroute

> Ferramenta CLI open source para Linux que cria um proxy SOCKS5 local e
> encaminha o tráfego por uma cadeia de proxies (Tor, SOCKS5, HTTP CONNECT),
> protegendo o IP real da sua máquina (ex.: VM Kali Linux).

Escrita em **Python 3.11+** com módulos de alto desempenho em **Cython**.

> [!NOTE]
> Projeto em desenvolvimento (v0.1.0). Veja o [Status](#status) para saber o
> que já funciona.

---

## Sumário

- [Como funciona](#como-funciona)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Configuração](#configuração)
- [Uso](#uso)
- [Usando com outros programas](#usando-com-outros-programas)
- [Verificando vazamentos](#verificando-vazamentos)
- [Desenvolvimento](#desenvolvimento)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Status](#status)
- [Uso responsável](#uso-responsável)
- [Licença](#licença)

---

## Como funciona

```
 aplicação ──► ghostroute (127.0.0.1:1080) ──► hop 1 ──► hop 2 ──► ... ──► destino
 (curl,        proxy SOCKS5 local              (Tor)     (SOCKS5/HTTP)
  navegador)
```

1. A aplicação se conecta ao proxy SOCKS5 local do ghostroute.
2. O ghostroute abre um túnel através de cada hop configurado, em sequência.
3. O destino enxerga apenas o IP do **último hop** da cadeia.
4. Nomes de domínio são resolvidos **remotamente** (modo `socks5h`), então as
   consultas DNS não saem pela sua rede local.

Modos de cadeia:

| Modo      | Comportamento                                                     |
|-----------|-------------------------------------------------------------------|
| `strict`  | Usa todos os hops na ordem definida; falha se algum cair.          |
| `dynamic` | Usa os hops na ordem, pulando os que estiverem fora do ar.         |
| `random`  | Sorteia `chain_len` hops da lista a cada conexão.                  |

---

## Requisitos

- Linux (testado no Kali Linux)
- Python **3.11+**
- Compilador C e headers do Python (para as extensões Cython)
- Tor (opcional, mas recomendado como primeiro hop)

No Kali / Debian / Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-dev build-essential git tor
```

---

## Instalação

### A partir do código-fonte

```bash
git clone https://github.com/<seu-usuario>/ghostroute.git
cd ghostroute

python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"      # instala o pacote + ferramentas de desenvolvimento
make build                   # compila as extensões Cython (in-place)
```

Para conferir se a extensão Cython foi carregada:

```bash
python -c "import ghostroute._speedups._socks5 as m; print('Cython OK:', m.__file__)"
```

> [!TIP]
> Se a compilação falhar, o ghostroute continua funcionando com a
> implementação em Python puro — só fica um pouco mais lento.

### Script de instalação

```bash
sudo ./scripts/install.sh
```

---

## Configuração

Copie o exemplo e edite:

```bash
cp config/ghostroute.example.toml config/ghostroute.toml
```

```toml
[server]
listen_host = "127.0.0.1"   # mantenha em localhost para não expor o proxy
listen_port = 1080

[chain]
mode = "strict"             # strict | dynamic | random
timeout = 10.0              # segundos por hop
remote_dns = true           # resolve DNS no último hop (evita DNS leak)
# chain_len = 2             # usado apenas no modo random

[[chain.hops]]
url = "socks5://127.0.0.1:9050"           # Tor local

[[chain.hops]]
url = "socks5://usuario:senha@203.0.113.10:1080"

[[chain.hops]]
url = "http://198.51.100.20:8080"         # proxy HTTP com suporte a CONNECT
```

Formatos de hop aceitos:

| Esquema    | Exemplo                                   |
|------------|-------------------------------------------|
| `socks5`   | `socks5://host:porta`                     |
| `socks5`   | `socks5://usuario:senha@host:porta`       |
| `http`     | `http://host:porta` ou `http://u:s@host:porta` |

Também é possível manter uma lista de proxies em `config/proxies.txt`
(um por linha, no mesmo formato de URL).

### Habilitando o Tor

```bash
sudo systemctl enable --now tor
ss -ltnp | grep 9050        # deve mostrar o Tor escutando
```

---

## Uso

```bash
ghostroute --help
ghostroute --version
```

### Iniciar o proxy

Com arquivo de configuração:

```bash
ghostroute start --config config/ghostroute.toml
```

Sem arquivo, passando os hops direto na linha de comando (em ordem):

```bash
# só pelo Tor
ghostroute start -H socks5://127.0.0.1:9050

# Tor -> proxy SOCKS5 -> proxy HTTP, escutando na porta 9999
ghostroute start -p 9999 \
  -H socks5://127.0.0.1:9050 \
  -H socks5://user:senha@203.0.113.10:1080 \
  -H http://198.51.100.20:8080
```

Se `--config` não for informado, o ghostroute procura, nesta ordem:
`./config/ghostroute.toml`, `~/.config/ghostroute/config.toml` e
`/etc/ghostroute/config.toml`.

Opções do `start`:

| Opção                  | Descrição                                              |
|------------------------|--------------------------------------------------------|
| `-c, --config ARQ`     | Arquivo TOML de configuração                           |
| `-H, --hop URL`        | Hop da cadeia (repetível; substitui os do arquivo)     |
| `-l, --listen HOST`    | Endereço de escuta (padrão `127.0.0.1`)                |
| `-p, --port PORTA`     | Porta de escuta (padrão `1080`)                        |
| `-m, --mode MODO`      | `strict`, `dynamic` ou `random`                        |
| `--timeout SEG`        | Timeout por hop                                        |
| `--chain-len N`        | Nº de hops sorteados no modo `random`                  |
| `-v` / `-q`            | Logs detalhados / apenas avisos e erros                |

Exemplo de saída:

```
20:40:01 [INFO] ghostroute escutando em socks5://127.0.0.1:1080
20:40:01 [INFO] modo strict | cadeia: socks5://127.0.0.1:9050
20:40:05 [INFO] ('127.0.0.1', 51234) -> api.ipify.org:443 via socks5://127.0.0.1:9050
```

Pare com `Ctrl+C` (ou `SIGTERM`).

Também pode ser executado como módulo:

```bash
python -m ghostroute start -c config/ghostroute.toml
```

### Ver o IP público através da cadeia

Verifica tanto o seu IP real local quanto o IP de saída obtido através da cadeia de proxies, alertando se houver vazamento:

```bash
# Usando o arquivo de configuração
ghostroute check-ip -c config/ghostroute.toml

# Ou testando diretamente contra o Tor local
ghostroute check-ip -H socks5://127.0.0.1:9050
```

Exemplo de saída:

```
[*] Verificando IP real local...
[-] IP real da máquina : 177.18.23.45 (BR, Claro Brasil)
[*] Roteando verificação através da cadeia (socks5://127.0.0.1:9050)...
[+] IP através da cadeia: 185.220.101.5 (DE, Tor Exit Node)
[✓] SUCESSO: IP mascarado com êxito! O IP real está oculto.
```

### Renovar IP do Tor (Novo circuito)

Se o primeiro hop for o Tor e o Tor ControlPort (porta 9051) estiver ativo, você pode pedir um novo endereço IP a qualquer momento sem reiniciar o serviço:

```bash
ghostroute new-circuit
```

---

## Usando com outros programas

Com o ghostroute rodando em `127.0.0.1:1080`:

**curl**

```bash
curl --socks5-hostname 127.0.0.1:1080 https://api.ipify.org
```

**Variáveis de ambiente** (programas que respeitam `ALL_PROXY`):

```bash
export ALL_PROXY="socks5h://127.0.0.1:1080"
```

**Firefox**: Configurações → Rede → Configurar proxy manual →
SOCKS Host `127.0.0.1`, porta `1080`, SOCKS v5 e marque
*"Usar proxy DNS ao utilizar SOCKS v5"*.

**proxychains4** (para programas sem suporte nativo a proxy) — em
`/etc/proxychains4.conf`:

```
[ProxyList]
socks5 127.0.0.1 1080
```

```bash
proxychains4 -q curl https://api.ipify.org
```

---

## Verificando vazamentos

Compare o IP com e sem o proxy:

```bash
curl https://api.ipify.org                                   # IP real
curl --socks5-hostname 127.0.0.1:1080 https://api.ipify.org  # IP do último hop
```

Recomendações:

- Use sempre `socks5h://` / `--socks5-hostname` para o DNS ir pelo proxy.
- Desative WebRTC no navegador (`media.peerconnection.enabled = false` no Firefox).
- Lembre que **apenas TCP** é encaminhado; tráfego UDP (ex.: QUIC, DNS direto)
  não passa pelo proxy.

---

## Desenvolvimento

```bash
source .venv/bin/activate
make install   # pip install -e ".[dev]"
make build     # compila os .pyx com Cython
make test      # pytest
make lint      # ruff
make clean     # remove artefatos de build
```

### Cython

Os módulos acelerados ficam em `src/ghostroute/_speedups/*.pyx`. Cada módulo
Python que usa uma extensão faz o import com fallback:

```python
try:
    from ghostroute._speedups._socks5 import pack_port
except ImportError:
    def pack_port(port: int) -> bytes: ...
```

Para adicionar uma nova extensão:

1. Crie `src/ghostroute/_speedups/_nome.pyx`.
2. Registre-a em `setup.py` na lista de `Extension`.
3. Rode `make build` e adicione testes.

---

## Estrutura do projeto

```
ghostroute/
├── src/ghostroute/
│   ├── cli.py              # Ponto de entrada da CLI
│   ├── config.py           # Carregamento da configuração TOML
│   ├── logger.py
│   ├── core/
│   │   ├── server.py       # Servidor SOCKS5 local (asyncio)
│   │   ├── chain.py        # Montagem da cadeia de proxies
│   │   └── relay.py        # Cópia bidirecional de dados
│   ├── protocols/
│   │   ├── socks5.py       # RFC 1928 / RFC 1929 (cliente e servidor)
│   │   └── http_connect.py # Túnel via HTTP CONNECT
│   ├── providers/
│   │   ├── tor.py          # Integração com Tor
│   │   └── proxy_list.py   # Carregamento de listas de proxies
│   ├── utils/
│   │   └── ipcheck.py      # Verificação do IP público
│   └── _speedups/
│       └── _socks5.pyx     # Extensões Cython
├── config/                 # Exemplos de configuração
├── tests/                  # pytest + pytest-asyncio
├── docs/                   # Documentação adicional
├── scripts/install.sh
├── pyproject.toml
├── setup.py                # Build das extensões Cython
└── Makefile
```

---

## Status

| Componente                        | Estado          |
|-----------------------------------|-----------------|
| Protocolo SOCKS5 (cliente/servidor, auth usuário/senha) | ✅ Implementado e testado |
| Protocolo HTTP CONNECT (com auth Basic) | ✅ Implementado e testado |
| Cadeia de proxies (strict / dynamic / random) | ✅ Implementado e testado |
| Servidor SOCKS5 local + relay     | ✅ Implementado e testado |
| Configuração TOML + `ghostroute start` | ✅ Implementado e testado |
| Extensão Cython `_socks5`         | ✅ Implementado (compilar com `make build`) |
| Integração com Tor (`new-circuit` / ControlPort) | ✅ Implementado e testado |
| `check-ip` (detecção de IP e vazamentos) | ✅ Implementado e testado |

---

## Uso responsável

Esta ferramenta foi feita para **privacidade** e para uso em ambientes de
estudo e testes **autorizados**. Use apenas em redes e sistemas que você
possui ou tem permissão explícita para testar, respeitando a legislação
local e os termos de uso dos proxies que utilizar.

---

## Contribuindo

Pull requests são bem-vindos! Veja [CONTRIBUTING.md](CONTRIBUTING.md).

## Licença

Distribuído sob a licença MIT. Veja [LICENSE](LICENSE).
