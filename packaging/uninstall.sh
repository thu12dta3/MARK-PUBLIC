#!/usr/bin/env bash
# ==============================================================================
# MARK - Desinstalador Universal Linux
# ==============================================================================
set -euo pipefail

C_RESET=$'\033[0m'
C_BOLD=$'\033[1m'
C_CYAN=$'\033[38;2;0;216;246m'
C_GREEN=$'\033[38;2;63;185;80m'
C_YELLOW=$'\033[38;2;240;136;62m'
C_RED=$'\033[38;2;248;81;73m'
C_GRAY=$'\033[38;2;139;148;158m'

PURGE_DATA=false
AUTO_YES=false

show_help() {
    cat <<EOF
${C_BOLD}${C_CYAN}Desinstalador do MARK${C_RESET}

Uso:
  ./uninstall.sh [opções]

Opções:
  --purge        Remove também as configurações, cofre de chaves e histórico (~/.config/mark)
  --yes, -y      Não pede confirmação interativa
  --help, -h     Exibe esta ajuda
EOF
}

for arg in "$@"; do
    case "$arg" in
        --purge)
            PURGE_DATA=true
            ;;
        --yes|-y)
            AUTO_YES=true
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

echo -e "${C_CYAN}${C_BOLD}Remoção do MARK (Assistente Cognitivo)${C_RESET}"
echo -e "${C_GRAY}------------------------------------------------------------${C_RESET}"

if [ "$AUTO_YES" != true ]; then
    read -r -p "Tem certeza que deseja desinstalar o MARK deste sistema? [s/N]: " confirm
    if [[ ! "$confirm" =~ ^[sSyY]$ ]]; then
        echo "Desinstalação cancelada pelo usuário."
        exit 0
    fi
fi

# Detecta se está em modo system ou user
if [ -d "/opt/mark" ] && [ "$(id -u)" -eq 0 ]; then
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

echo -e "${C_BOLD}Removendo componentes...${C_RESET}"

# 1. Remove executáveis e links
rm -f "$BIN_DIR/mark-desktop" "$BIN_DIR/mark" 2>/dev/null || true
echo -e "  ${C_GREEN}✓${C_RESET} Links em $BIN_DIR removidos."

# 2. Remove entrada de menu .desktop
rm -f "$APPLICATIONS_DIR/mark.desktop" 2>/dev/null || true
echo -e "  ${C_GREEN}✓${C_RESET} Atalho de menu removido."

# 3. Remove atalho da Área de Trabalho
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
if [ -n "$DESKTOP_FOLDER" ] && [ -f "$DESKTOP_FOLDER/mark.desktop" ]; then
    rm -f "$DESKTOP_FOLDER/mark.desktop" 2>/dev/null || true
    echo -e "  ${C_GREEN}✓${C_RESET} Atalho da Área de Trabalho removido."
fi

# 4. Remove ícones instalados
for size_dir in "$ICONS_DIR"/*; do
    if [ -f "$size_dir/apps/mark.png" ]; then
        rm -f "$size_dir/apps/mark.png" 2>/dev/null || true
    fi
    if [ -f "$size_dir/apps/mark.svg" ]; then
        rm -f "$size_dir/apps/mark.svg" 2>/dev/null || true
    fi
done
echo -e "  ${C_GREEN}✓${C_RESET} Ícones do sistema removidos."

# 5. Remove diretório da aplicação
if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR" 2>/dev/null || true
    echo -e "  ${C_GREEN}✓${C_RESET} Diretório de instalação $INSTALL_DIR removido."
fi

# 6. Atualiza caches de desktop e ícones
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS_DIR" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$ICONS_DIR" 2>/dev/null || true
fi

# 7. Purge de configurações se solicitado
CONFIG_DIR="$HOME/.config/mark"
if [ "$PURGE_DATA" = true ]; then
    if [ -d "$CONFIG_DIR" ]; then
        rm -rf "$CONFIG_DIR" 2>/dev/null || true
        echo -e "  ${C_GREEN}✓${C_RESET} Configurações e cofre em $CONFIG_DIR excluídos."
    fi
else
    if [ -d "$CONFIG_DIR" ]; then
        echo -e "  ${C_GRAY}ℹ As configurações e chaves em $CONFIG_DIR foram mantidas.${C_RESET}"
        echo -e "  ${C_GRAY}  Para apagá-las manualmente: rm -rf $CONFIG_DIR${C_RESET}"
    fi
fi

echo ""
echo -e "${C_GREEN}${C_BOLD}MARK desinstalado com sucesso do sistema.${C_RESET}"
echo ""
