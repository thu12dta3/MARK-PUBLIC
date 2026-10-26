#!/usr/bin/env bash
# ==============================================================================
# Script de Construção do Pacote Universal Tarball do MARK para Linux
# ==============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
STAGE_NAME="mark-linux-x86_64"
STAGE_DIR="$DIST_DIR/$STAGE_NAME"
TAR_FILE="$DIST_DIR/$STAGE_NAME.tar.gz"

echo "=== Construindo Pacote Distribuível do MARK ($STAGE_NAME) ==="

# 1. Garante renderização dos ícones multi-resolução se não existirem
echo "[1/5] Verificando ícones multi-resolução..."
mkdir -p "$ROOT_DIR/packaging/icons/hicolor"/{16x16,24x24,32x32,48x48,64x64,128x128,256x256,512x512}/apps "$ROOT_DIR/packaging/icons/hicolor/scalable/apps"
cp "$ROOT_DIR/assets/mark.svg" "$ROOT_DIR/packaging/icons/hicolor/scalable/apps/mark.svg"

if command -v rsvg-convert >/dev/null 2>&1; then
    for s in 16 24 32 48 64 128 256 512; do
        if [ ! -f "$ROOT_DIR/packaging/icons/hicolor/${s}x${s}/apps/mark.png" ]; then
            rsvg-convert -w $s -h $s -f png "$ROOT_DIR/assets/mark.svg" -o "$ROOT_DIR/packaging/icons/hicolor/${s}x${s}/apps/mark.png"
        fi
    done
fi

# 2. Prepara o diretório de staging limpo
echo "[2/5] Preparando diretório de distribuição..."
rm -rf "$STAGE_DIR" "$TAR_FILE" "${TAR_FILE}.sha256"
mkdir -p "$STAGE_DIR"

# 3. Copia os componentes de runtime
echo "[3/5] Copiando componentes essenciais..."
mkdir -p "$STAGE_DIR/src" "$STAGE_DIR/assets" "$STAGE_DIR/config" "$STAGE_DIR/scripts" "$STAGE_DIR/bin" "$STAGE_DIR/packaging"

cp -r "$ROOT_DIR/src/"* "$STAGE_DIR/src/"
cp -r "$ROOT_DIR/assets/"* "$STAGE_DIR/assets/"
cp -r "$ROOT_DIR/config/"* "$STAGE_DIR/config/"
cp -r "$ROOT_DIR/scripts/"* "$STAGE_DIR/scripts/"
cp -r "$ROOT_DIR/bin/"* "$STAGE_DIR/bin/"
cp -r "$ROOT_DIR/packaging/"* "$STAGE_DIR/packaging/"

# Copia arquivos raiz de conveniência
cp "$ROOT_DIR/packaging/install.sh" "$STAGE_DIR/install.sh"
cp "$ROOT_DIR/packaging/uninstall.sh" "$STAGE_DIR/uninstall.sh"
cp "$ROOT_DIR/requirements.txt" "$STAGE_DIR/requirements.txt"
cp "$ROOT_DIR/README.md" "$STAGE_DIR/README.md" 2>/dev/null || true
cp "$ROOT_DIR/INSTALL.md" "$STAGE_DIR/INSTALL.md" 2>/dev/null || true
cp "$ROOT_DIR/LICENSE" "$STAGE_DIR/LICENSE" 2>/dev/null || true

# Garante permissões de execução nos scripts
chmod +x "$STAGE_DIR/install.sh" "$STAGE_DIR/uninstall.sh" "$STAGE_DIR/bin/"* "$STAGE_DIR/scripts/"* 2>/dev/null || true

# 4. Limpeza rigorosa de artefatos de desenvolvimento e cache
echo "[4/5] Higienizando diretório de distribuição..."
find "$STAGE_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$STAGE_DIR" -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
find "$STAGE_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true
find "$STAGE_DIR" -type f -name "*.pyo" -delete 2>/dev/null || true
find "$STAGE_DIR" -type f -name ".DS_Store" -delete 2>/dev/null || true

# 5. Empacotamento no arquivo tar.gz e geração de checksum
echo "[5/5] Compactando tar.gz e gerando checksum SHA256..."
tar -czf "$TAR_FILE" -C "$DIST_DIR" "$STAGE_NAME"
(cd "$DIST_DIR" && sha256sum "$STAGE_NAME.tar.gz" > "$STAGE_NAME.tar.gz.sha256")
rm -rf "$STAGE_DIR"

echo ""
echo "=== Pacote gerado com sucesso! ==="
echo "Arquivo:  $TAR_FILE"
echo "Tamanho:  $(du -h "$TAR_FILE" | cut -f1)"
echo "Checksum: $(cat "${TAR_FILE}.sha256")"
echo ""
