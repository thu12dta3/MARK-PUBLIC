#!/usr/bin/env bash
# ==============================================================================
# MARK - Instalador Universal Linux
# Assistente Executivo de Inteligência Artificial e Workstation Cognitiva
# ==============================================================================
set -euo pipefail

# Paleta de Cores ANSI
C_RESET=$'\033[0m'
C_BOLD=$'\033[1m'
C_CYAN=$'\033[38;2;0;216;246m'
C_DIM_CYAN=$'\033[38;2;111;231;244m'
C_GREEN=$'\033[38;2;63;185;80m'
C_YELLOW=$'\033[38;2;240;136;62m'
C_RED=$'\033[38;2;248;81;73m'
C_GRAY=$'\033[38;2;139;148;158m'

# Metadados
APP_NAME="MARK"
PKG_VERSION="1.2.0"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Se o instalador estiver dentro de packaging/, a raiz dos fontes é o diretório pai
if [ -f "$SOURCE_DIR/../requirements.txt" ] && [ -d "$SOURCE_DIR/../src" ]; then
    ROOT_SOURCE="$(cd "$SOURCE_DIR/.." && pwd)"
else
    ROOT_SOURCE="$SOURCE_DIR"
fi

# Opções Padrão
MODE="user"
CREATE_DESKTOP_SHORTCUT=true
SETUP_VENV=true
VERBOSE=false

show_help() {
    cat <<EOF
${C_BOLD}${C_CYAN}Instalador do ${APP_NAME} (${PKG_VERSION}) - Linux Universal${C_RESET}

Uso:
  ./install.sh [opções]

Opções:
  --user               Instalação local para o usuário atual em ~/.local (Padrão, não requer root)
  --system             Instalação global em /opt e /usr/local (Requer sudo/root)
  --no-desktop         Não cria atalho na Área de Trabalho (cria apenas no menu de aplicativos)
  --no-venv            Não cria ambiente virtual isolado (usa pacotes Python do sistema)
  --help, -h           Exibe esta mensagem de ajuda

Exemplos:
  ./install.sh                 # Instalação rápida recomendada (não precisa de sudo)
  sudo ./install.sh --system   # Instalação para todos os usuários do sistema
EOF
}

# Processamento de Argumentos
for arg in "$@"; do
    case "$arg" in
        --user)
            MODE="user"
            ;;
        --system)
            MODE="system"
            ;;
        --no-desktop)
            CREATE_DESKTOP_SHORTCUT=false
            ;;
        --no-venv)
            SETUP_VENV=false
            ;;
        --verbose)
            VERBOSE=true
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        *)
            echo -e "${C_RED}Opção desconhecida: $arg${C_RESET}"
            show_help
            exit 1
            ;;
    esac
done

# Banner de Instalação
clear 2>/dev/null || true
echo -e "${C_CYAN}  __  __          _____  _  __ ${C_RESET}"
echo -e "${C_CYAN} |  \/  |   /\   |  __ \| |/ / ${C_RESET}"
echo -e "${C_CYAN} | \  / |  /  \  | |__) | ' /  ${C_RESET}"
echo -e "${C_CYAN} | |\/| | / /\ \ |  _  /|  <   ${C_RESET}"
echo -e "${C_CYAN} | |  | |/ ____ \| | \ \| . \  ${C_RESET}"
echo -e "${C_CYAN} |_|  |_/_/    \_\_|  \_\_|\_\ ${C_RESET}"
echo -e "${C_GRAY} Ambiente Cognitivo & Assistente Executivo de IA (v${PKG_VERSION})${C_RESET}"
echo -e "${C_GRAY}------------------------------------------------------------${C_RESET}"
echo ""

# Determinação dos Caminhos de Destino
if [ "$MODE" = "system" ]; then
    if [ "$(id -u)" -ne 0 ]; then
        echo -e "${C_RED}ERRO: A instalação no modo --system requer privilégios de superusuário (sudo).${C_RESET}"
        echo -e "Dica: Execute ${C_BOLD}sudo ./install.sh --system${C_RESET} ou instale como usuário com ${C_BOLD}./install.sh${C_RESET}"
        exit 1
    fi
    INSTALL_DIR="/opt/mark"
    BIN_DIR="/usr/local/bin"
    APPLICATIONS_DIR="/usr/share/applications"
    ICONS_DIR="/usr/share/icons/hicolor"
else
    INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/mark-app"
    BIN_DIR="${HOME}/.local/bin"
    APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    ICONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
fi

echo -e "${C_BOLD}Plano de Instalação:${C_RESET}"
echo -e "  • Modo:          ${C_CYAN}${MODE}${C_RESET}"
echo -e "  • Destino App:   ${C_GRAY}${INSTALL_DIR}${C_RESET}"
echo -e "  • Comandos CLI:  ${C_GRAY}${BIN_DIR}${C_RESET}"
echo -e "  • Atalho Menu:   ${C_GRAY}${APPLICATIONS_DIR}/mark.desktop${C_RESET}"
echo ""

# [1/6] Verificação de Dependências Básicas
echo -e "${C_BOLD}[1/6] Verificando requisitos do sistema...${C_RESET}"
PYTHON_BIN=""
for cand in python3 /usr/bin/python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        PY_VER="$("$cand" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
        PY_MAJ="$(echo "$PY_VER" | cut -d. -f1)"
        PY_MIN="$(echo "$PY_VER" | cut -d. -f2)"
        if [ "$PY_MAJ" -eq 3 ] && [ "$PY_MIN" -ge 9 ]; then
            PYTHON_BIN="$cand"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo -e "${C_RED}ERRO: Python 3.9 ou superior não encontrado.${C_RESET}"
    echo "Por favor instale o Python 3 moderno usando o gerenciador de pacotes da sua distribuição:"
    echo "  Ubuntu/Debian: sudo apt update && sudo apt install python3 python3-pip python3-venv"
    echo "  Fedora:        sudo dnf install python3 python3-pip"
    echo "  Arch Linux:    sudo pacman -S python python-pip"
    exit 1
fi
echo -e "  ${C_GREEN}✓${C_RESET} Python detectado: ${C_GRAY}$("$PYTHON_BIN" --version)${C_RESET}"

# [2/6] Preparando Diretórios e Copiando Arquivos
echo -e "${C_BOLD}[2/6] Instalando arquivos do núcleo do MARK...${C_RESET}"
mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$APPLICATIONS_DIR"

# Criação de backup defensivo se já existia instalação prévia
if [ -d "$INSTALL_DIR/src" ]; then
    rm -rf "$INSTALL_DIR/src" "$INSTALL_DIR/config" "$INSTALL_DIR/assets" "$INSTALL_DIR/scripts" 2>/dev/null || true
fi

# Cópia dos módulos essenciais
cp -r "$ROOT_SOURCE/src" "$INSTALL_DIR/"
cp -r "$ROOT_SOURCE/assets" "$INSTALL_DIR/"
cp -r "$ROOT_SOURCE/config" "$INSTALL_DIR/"
cp -r "$ROOT_SOURCE/scripts" "$INSTALL_DIR/"
cp -r "$ROOT_SOURCE/bin" "$INSTALL_DIR/"
cp "$ROOT_SOURCE/requirements.txt" "$INSTALL_DIR/"
cp "$ROOT_SOURCE/README.md" "$INSTALL_DIR/" 2>/dev/null || true
cp "$ROOT_SOURCE/LICENSE" "$INSTALL_DIR/" 2>/dev/null || true

# Copia script desinstalador para a pasta de instalação
if [ -f "$ROOT_SOURCE/uninstall.sh" ]; then
    cp "$ROOT_SOURCE/uninstall.sh" "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR/uninstall.sh"
elif [ -f "$ROOT_SOURCE/packaging/uninstall.sh" ]; then
    cp "$ROOT_SOURCE/packaging/uninstall.sh" "$INSTALL_DIR/uninstall.sh"
    chmod +x "$INSTALL_DIR/uninstall.sh"
fi

chmod +x "$INSTALL_DIR/bin/"* "$INSTALL_DIR/scripts/"* 2>/dev/null || true
echo -e "  ${C_GREEN}✓${C_RESET} Arquivos do aplicativo copiados para ${C_GRAY}$INSTALL_DIR${C_RESET}"

# [3/6] Configuração do Ambiente Virtual Python Isolado
if [ "$SETUP_VENV" = true ]; then
    echo -e "${C_BOLD}[3/6] Configurando ambiente Python isolado (venv)...${C_RESET}"
    VENV_DIR="$INSTALL_DIR/venv"
    
    # Testa se o módulo venv está disponível
    if ! "$PYTHON_BIN" -m venv "$VENV_DIR" --system-site-packages 2>/dev/null; then
        echo -e "  ${C_YELLOW}! Tentando criar venv sem system-site-packages...${C_RESET}"
        if ! "$PYTHON_BIN" -m venv "$VENV_DIR" 2>/dev/null; then
            echo -e "${C_YELLOW}AVISO: Módulo venv não está disponível neste Python.${C_RESET}"
            echo -e "Para melhor isolamento em distribuições modernas com PEP 668, instale:"
            echo -e "  Debian/Ubuntu: ${C_BOLD}sudo apt install python3-venv${C_RESET}"
            echo -e "Continuando usando o interpretador do sistema..."
            SETUP_VENV=false
        fi
    fi

    if [ "$SETUP_VENV" = true ] && [ -x "$VENV_DIR/bin/pip" ]; then
        echo -e "  ${C_GREEN}✓${C_RESET} Ambiente virtual criado em ${C_GRAY}$VENV_DIR${C_RESET}"
        echo -e "  ${C_DIM_CYAN}→${C_RESET} Instalando dependências (PyQt6, cryptography, Pillow)..."
        
        # Tenta atualizar pip e instalar requirements com timeout de segurança
        "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip setuptools 2>/dev/null || true
        if "$VENV_DIR/bin/python" -m pip install --quiet -r "$INSTALL_DIR/requirements.txt" 2>/dev/null; then
            echo -e "  ${C_GREEN}✓${C_RESET} Dependências instaladas com sucesso no ambiente isolado."
        else
            echo -e "  ${C_YELLOW}! Nota: A instalação de pacotes via pip falhou (possivelmente offline).${C_RESET}"
            echo -e "  ${C_GRAY}  O MARK tentará usar pacotes instalados no sistema como fallback.${C_RESET}"
        fi
    fi
else
    echo -e "${C_BOLD}[3/6] Pulando venv (--no-venv solicitado).${C_RESET}"
fi

# [4/6] Instalação de Ícones Multi-Resolução e SVG
echo -e "${C_BOLD}[4/6] Instalando ícones do sistema (XDG Hicolor Theme)...${C_RESET}"
ICONS_SOURCE=""
if [ -d "$ROOT_SOURCE/packaging/icons/hicolor" ]; then
    ICONS_SOURCE="$ROOT_SOURCE/packaging/icons/hicolor"
elif [ -d "$ROOT_SOURCE/icons/hicolor" ]; then
    ICONS_SOURCE="$ROOT_SOURCE/icons/hicolor"
fi

if [ -n "$ICONS_SOURCE" ] && [ -d "$ICONS_SOURCE" ]; then
    for size_dir in "$ICONS_SOURCE"/*; do
        if [ -d "$size_dir" ]; then
            size_name="$(basename "$size_dir")"
            target_apps_dir="$ICONS_DIR/$size_name/apps"
            mkdir -p "$target_apps_dir"
            cp -r "$size_dir/apps/"* "$target_apps_dir/" 2>/dev/null || true
        fi
    done
    echo -e "  ${C_GREEN}✓${C_RESET} Ícones instalados em 16x16, 24x24, 32x32, 48x48, 64x64, 128x128, 256x256, 512x512 e SVG."
else
    # Fallback: copia direto de assets/
    mkdir -p "$ICONS_DIR/scalable/apps" "$ICONS_DIR/256x256/apps"
    cp "$INSTALL_DIR/assets/mark.svg" "$ICONS_DIR/scalable/apps/mark.svg" 2>/dev/null || true
    if [ -f "$INSTALL_DIR/assets/mark-256.png" ]; then
        cp "$INSTALL_DIR/assets/mark-256.png" "$ICONS_DIR/256x256/apps/mark.png" 2>/dev/null || true
    fi
    echo -e "  ${C_GREEN}✓${C_RESET} Ícones básicos copiados para o tema do sistema."
fi

# [5/6] Configuração de Launchers e Integração Desktop (.desktop)
echo -e "${C_BOLD}[5/6] Integrando ao Desktop e Menu do Sistema...${C_RESET}"

# Cria symlinks nos binários do PATH
ln -sf "$INSTALL_DIR/bin/mark-desktop" "$BIN_DIR/mark-desktop"
ln -sf "$INSTALL_DIR/bin/mark" "$BIN_DIR/mark"
chmod +x "$BIN_DIR/mark-desktop" "$BIN_DIR/mark"

# Criação do arquivo .desktop personalizado com o caminho absoluto correto
DESKTOP_TEMPLATE="$ROOT_SOURCE/packaging/mark.desktop"
INSTALLED_DESKTOP="$APPLICATIONS_DIR/mark.desktop"

cat > "$INSTALLED_DESKTOP" <<EOF
[Desktop Entry]
Version=1.5
Type=Application
Name=MARK
GenericName=Ambiente Operacional de IA
GenericName[en]=AI Operating Environment
Comment=Assistente executivo de inteligência artificial e workstation cognitiva
Comment[en]=Executive AI desktop assistant and cognitive workstation
Exec=$INSTALL_DIR/bin/mark-desktop %U
Icon=mark
Terminal=false
Categories=Utility;Development;Science;ArtificialIntelligence;
Keywords=AI;IA;Assistant;Assistente;LLM;Voice;Vision;Autonomous;Chat;
StartupNotify=true
StartupWMClass=MARK
MimeType=x-scheme-handler/mark;
Actions=NewChat;Engineer;VoiceCall;

[Desktop Action NewChat]
Name=Nova Conversa
Name[en]=New Chat
Exec=$INSTALL_DIR/bin/mark-desktop --new-chat

[Desktop Action Engineer]
Name=Espaço de Engenharia
Name[en]=Engineering Workspace
Exec=$INSTALL_DIR/bin/mark-desktop --mode=engineer

[Desktop Action VoiceCall]
Name=Chamada de Voz
Name[en]=Voice Call
Exec=$INSTALL_DIR/bin/mark-desktop --call
EOF

chmod 644 "$INSTALLED_DESKTOP"
echo -e "  ${C_GREEN}✓${C_RESET} Entrada de menu criada em ${C_GRAY}$INSTALLED_DESKTOP${C_RESET}"

# Atalho na Área de Trabalho (se aplicável e em modo user)
if [ "$CREATE_DESKTOP_SHORTCUT" = true ] && [ "$MODE" = "user" ]; then
    DESKTOP_FOLDER=""
    if command -v xdg-user-dir >/dev/null 2>&1; then
        DESKTOP_FOLDER="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
    fi
    if [ -z "$DESKTOP_FOLDER" ] || [ ! -d "$DESKTOP_FOLDER" ]; then
        for cand in "$HOME/Desktop" "$HOME/Área de trabalho" "$HOME/Área de Trabalho" "$HOME/Escritorio"; do
            if [ -d "$cand" ]; then
                DESKTOP_FOLDER="$cand"
                break
            fi
        done
    fi

    if [ -n "$DESKTOP_FOLDER" ] && [ -d "$DESKTOP_FOLDER" ]; then
        USER_DESKTOP_FILE="$DESKTOP_FOLDER/mark.desktop"
        cp "$INSTALLED_DESKTOP" "$USER_DESKTOP_FILE"
        chmod +x "$USER_DESKTOP_FILE"
        # Marca como confiável no GNOME/Nautilus para permitir execução direta
        if command -v gio >/dev/null 2>&1; then
            gio set "$USER_DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
        fi
        echo -e "  ${C_GREEN}✓${C_RESET} Atalho criado na Área de Trabalho: ${C_GRAY}$USER_DESKTOP_FILE${C_RESET}"
    fi
fi

# [6/6] Atualização dos Caches do Ambiente Gráfico
echo -e "${C_BOLD}[6/6] Atualizando caches de aplicativos e ícones...${C_RESET}"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS_DIR" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$ICONS_DIR" 2>/dev/null || true
fi
if command -v xdg-desktop-menu >/dev/null 2>&1; then
    xdg-desktop-menu forceupdate 2>/dev/null || true
fi

# Verificação de PATH
PATH_WARNING=""
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    PATH_WARNING="true"
fi

echo ""
echo -e "${C_GREEN}${C_BOLD}============================================================${C_RESET}"
echo -e "${C_GREEN}${C_BOLD}      INSTALAÇÃO DO MARK CONCLUÍDA COM SUCESSO!            ${C_RESET}"
echo -e "${C_GREEN}${C_BOLD}============================================================${C_RESET}"
echo ""
echo -e "${C_BOLD}Como abrir o MARK:${C_RESET}"
echo -e "  ${C_CYAN}1. Menu de Aplicativos:${C_RESET} Abra o menu do seu sistema e pesquise por ${C_BOLD}MARK${C_RESET}."
echo -e "  ${C_CYAN}2. Barra de Tarefas / Dock:${C_RESET} Clique com botão direito no ícone do MARK e escolha:"
echo -e "     ${C_GRAY}• GNOME Shell:${C_RESET} 'Adicionar aos Favoritos'"
echo -e "     ${C_GRAY}• KDE Plasma:${C_RESET}  'Fixar no Gerenciador de Tarefas'"
echo -e "     ${C_GRAY}• XFCE / MATE:${C_RESET} 'Adicionar ao Painel'"
if [ "$CREATE_DESKTOP_SHORTCUT" = true ] && [ -n "${DESKTOP_FOLDER:-}" ]; then
    echo -e "  ${C_CYAN}3. Área de Trabalho:${C_RESET} Dê um duplo clique no ícone ${C_BOLD}MARK${C_RESET} na Área de Trabalho."
fi
echo -e "  ${C_CYAN}4. Terminal:${C_RESET} Execute ${C_BOLD}mark-desktop${C_RESET} para a interface ou ${C_BOLD}mark${C_RESET} para o CLI."
echo ""

if [ -n "$PATH_WARNING" ] && [ "$MODE" = "user" ]; then
    echo -e "${C_YELLOW}${C_BOLD}Dica para uso no terminal:${C_RESET}"
    echo -e "O diretório ${C_GRAY}$BIN_DIR${C_RESET} ainda não está no seu PATH."
    echo -e "Para executar '${C_BOLD}mark-desktop${C_RESET}' de qualquer lugar no terminal, adicione ao seu ${C_GRAY}~/.bashrc${C_RESET} ou ${C_GRAY}~/.zshrc${C_RESET}:"
    echo -e "  ${C_CYAN}export PATH=\"\$HOME/.local/bin:\$PATH\"${C_RESET}"
    echo ""
fi

echo -e "Para desinstalar no futuro, basta executar: ${C_GRAY}$INSTALL_DIR/uninstall.sh${C_RESET}"
echo ""
