#!/usr/bin/env bash
# ==============================================================================
# MARK Ecosystem - Deploy Otimizado no TicWatch Pro via USB (Fase 4)
# Implementa estratégias de estabilização de conexão e `--no-streaming`
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WEAR_APK="$REPO_ROOT/build/mark-wear-release.apk"
PKG_NAME="br.com.bannaman.markmobile"
MAIN_ACTIVITY="br.com.bannaman.markwear.MainActivity"

if [ ! -f "$WEAR_APK" ]; then
    echo "Compilando APK do Wear OS..."
    "$SCRIPT_DIR/build_apks.sh"
fi

echo "=== MARK Wear: Preparando Deploy Físico no TicWatch Pro ==="

wait_for_device() {
    echo "Aguardando estabilização do dispositivo TicWatch no ADB..." >&2
    for i in $(seq 1 60); do
        DEV=$(adb devices | grep -v "List of" | grep -E "\bdevice\b" | awk '{print $1}' | head -n 1 || true)
        if [ -n "$DEV" ]; then
            # Testa comando simples para confirmar responsividade do adb daemon no relógio
            if adb -s "$DEV" shell echo "ping" >/dev/null 2>&1; then
                echo "$DEV"
                return 0
            fi
        fi
        sleep 1
    done
    return 1
}

DEVICE_ID=$(wait_for_device || true)

if [ -z "$DEVICE_ID" ]; then
    echo "Reiniciando servidor ADB para reestabelecer conexão com o hardware..."
    adb kill-server
    adb start-server
    sleep 2
    DEVICE_ID=$(wait_for_device || true)
fi

if [ -z "$DEVICE_ID" ]; then
    echo "ERRO: TicWatch não detectado ou contato instável no berço magnético." >&2
    echo "Verifique o posicionamento dos pinos (pogo pins) no relógio." >&2
    exit 1
fi

echo "✓ Dispositivo conectado e responsivo: $DEVICE_ID"
echo "Modelo: $(adb -s "$DEVICE_ID" shell getprop ro.product.model | tr -d '\r')"
echo "Versão Android: $(adb -s "$DEVICE_ID" shell getprop ro.build.version.release | tr -d '\r') (API $(adb -s "$DEVICE_ID" shell getprop ro.build.version.sdk | tr -d '\r'))"

# Estratégia de Instalação Segura (Non-Streaming para evitar timeout de I/O no Wear OS)
echo "Enviando e instalando APK ($WEAR_APK)..."
INSTALL_SUCCESS=0

# Tentativa 1: Push direto para /data/local/tmp e instalação local pelo pm
if adb -s "$DEVICE_ID" push "$WEAR_APK" /data/local/tmp/mark-wear.apk; then
    INSTALL_OUTPUT=$(adb -s "$DEVICE_ID" shell pm install -r -d /data/local/tmp/mark-wear.apk 2>&1 || true)
    echo "$INSTALL_OUTPUT"
    if echo "$INSTALL_OUTPUT" | grep -q "Success"; then
        INSTALL_SUCCESS=1
    elif echo "$INSTALL_OUTPUT" | grep -qE "INSTALL_FAILED_UPDATE_INCOMPATIBLE|signatures"; then
        echo "Assinatura incompatível detectada. Desinstalando versão legada..."
        adb -s "$DEVICE_ID" uninstall "$PKG_NAME" || true
        REINSTALL_OUTPUT=$(adb -s "$DEVICE_ID" shell pm install -r /data/local/tmp/mark-wear.apk 2>&1 || true)
        echo "$REINSTALL_OUTPUT"
        if echo "$REINSTALL_OUTPUT" | grep -q "Success"; then
            INSTALL_SUCCESS=1
        fi
    fi
    adb -s "$DEVICE_ID" shell rm -f /data/local/tmp/mark-wear.apk || true
fi

# Fallback: adb install --no-streaming
if [ "$INSTALL_SUCCESS" -ne 1 ]; then
    echo "Tentando fallback com adb install --no-streaming..."
    INSTALL_OUTPUT=$(adb -s "$DEVICE_ID" install --no-streaming -r -d "$WEAR_APK" 2>&1 || true)
    echo "$INSTALL_OUTPUT"
    if echo "$INSTALL_OUTPUT" | grep -q "Success"; then
        INSTALL_SUCCESS=1
    elif echo "$INSTALL_OUTPUT" | grep -qE "INSTALL_FAILED_UPDATE_INCOMPATIBLE|signatures"; then
        echo "Desinstalando versão prévia para resolver incompatibilidade..."
        adb -s "$DEVICE_ID" uninstall "$PKG_NAME" || true
        adb -s "$DEVICE_ID" install --no-streaming -r "$WEAR_APK"
        INSTALL_SUCCESS=1
    fi
fi

if [ "$INSTALL_SUCCESS" -ne 1 ]; then
    echo "ERRO: Falha ao instalar o APK no TicWatch." >&2
    exit 1
fi

echo "✓ Instalação concluída com sucesso no TicWatch Pro!"

# Verificação do caminho do pacote instalado
echo "Localização do APK no sistema:"
adb -s "$DEVICE_ID" shell pm path "$PKG_NAME"

# Inicialização da aplicação
echo "Iniciando aplicação no Wear OS (Terminal I/O)..."
adb -s "$DEVICE_ID" shell am start -n "$PKG_NAME/$MAIN_ACTIVITY"

# Lançamento complementar com monkey se necessário
sleep 1
adb -s "$DEVICE_ID" shell monkey -p "$PKG_NAME" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1 || true

# Telemetria e Logs Iniciais (10 segundos)
echo "=== Telemetria Inicial do Logcat (MARK Wear) ==="
sleep 2
adb -s "$DEVICE_ID" logcat -d | grep -i -E "mark|WearVoice|PowerMode|bannaman" | tail -n 25 || true

echo "=== Deploy e Inicialização no TicWatch Concluídos com Êxito! ==="
