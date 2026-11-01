#!/usr/bin/env bash
# Script de instalação do ghostroute no Kali Linux / Debian / Ubuntu
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Instalador do ghostroute para Kali Linux ===${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[!] Por favor, execute este script com sudo ou como root.${NC}"
    exit 1
fi

echo -e "${BLUE}[*] Instalando dependências do sistema...${NC}"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-dev python3-pip build-essential tor git

INSTALL_DIR="/opt/ghostroute"
echo -e "${BLUE}[*] Preparando diretório em ${INSTALL_DIR}...${NC}"
mkdir -p "${INSTALL_DIR}"
cp -r . "${INSTALL_DIR}/"
cd "${INSTALL_DIR}"

echo -e "${BLUE}[*] Criando ambiente virtual Python...${NC}"
python3 -m venv .venv
source .venv/bin/activate

echo -e "${BLUE}[*] Compilando e instalando o ghostroute (com extensões Cython)...${NC}"
pip install --upgrade pip setuptools wheel
pip install Cython>=3.0
pip install -e .
python setup.py build_ext --inplace

echo -e "${BLUE}[*] Criando link simbólico em /usr/local/bin/ghostroute...${NC}"
ln -sf "${INSTALL_DIR}/.venv/bin/ghostroute" /usr/local/bin/ghostroute

CONFIG_DIR="/etc/ghostroute"
mkdir -p "${CONFIG_DIR}"
if [ ! -f "${CONFIG_DIR}/config.toml" ]; then
    cp config/ghostroute.example.toml "${CONFIG_DIR}/config.toml"
    echo -e "${GREEN}[+] Configuração padrão criada em ${CONFIG_DIR}/config.toml${NC}"
fi

echo -e "${BLUE}[*] Ativando serviço Tor...${NC}"
systemctl enable --now tor || true

echo -e "${GREEN}=== Instalação concluída com sucesso! ===${NC}"
echo -e "Você já pode executar: ${GREEN}ghostroute --help${NC}"
echo -e "Para testar a saída Tor: ${GREEN}ghostroute check-ip -H socks5://127.0.0.1:9050${NC}"

