#!/usr/bin/env bash
# ==============================================================================
# MARK Ecosystem - Deploy Físico no Smartphone Android (Galaxy M23) via USB
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MOBILE_APK="$REPO_ROOT/build/mark-mobile-release.apk"
PKG_NAME="br.com.bannaman.markmobile"
MAIN_ACTIVITY="br.com.bannaman.markmobile.MainActivity"

if [ ! -f "$MOBILE_APK" ]; then
    echo "Artefato mobile não encontrado. Compilando com scripts/build_apks.sh..."
    "$SCRIPT_DIR/build_apks.sh"
fi

echo "=== MARK Mobile: Preparando Deploy Físico no Smartphone Android ==="

find_phone_device() {
    echo "Identificando smartphone conectado via ADB..." >&2
    for dev in $(adb devices | grep -v "List of" | grep -E "\bdevice\b" | awk '{print $1}'); do
        # Descarta o relógio (TicWatch) caso ambos estejam conectados simultaneamente
        MODEL=$(adb -s "$dev" shell getprop ro.product.model 2>/dev/null | tr -d '\r' || true)
        if [ "$MODEL" != "TicWatch_Pro" ] && [ -n "$MODEL" ]; then
            if adb -s "$dev" shell echo "ping" >/dev/null 2>&1; then
                echo "$dev"
                return 0
            fi
        fi
    done
    return 1
}

DEVICE_ID=$(find_phone_device || true)

if [ -z "$DEVICE_ID" ]; then
    echo "Reiniciando servidor ADB para reestabelecer conexão com o smartphone..."
    adb kill-server
    adb start-server
    sleep 2
    DEVICE_ID=$(find_phone_device || true)
fi

if [ -z "$DEVICE_ID" ]; then
    echo "ERRO: Smartphone Android não detectado via ADB." >&2
    echo "Certifique-se de que a Depuração USB está ativada no celular." >&2
    exit 1
fi

PHONE_MODEL=$(adb -s "$DEVICE_ID" shell getprop ro.product.model | tr -d '\r')
ANDROID_VER=$(adb -s "$DEVICE_ID" shell getprop ro.build.version.release | tr -d '\r')
ANDROID_SDK=$(adb -s "$DEVICE_ID" shell getprop ro.build.version.sdk | tr -d '\r')

echo "✓ Smartphone conectado: $DEVICE_ID"
echo "  Modelo: $PHONE_MODEL"
echo "  Android: $ANDROID_VER (API $ANDROID_SDK)"

# Injeção e Instalação Limpa
START_TIME=$(date +%s)
echo "Enviando artefato ($MOBILE_APK) para o smartphone..."
INSTALL_SUCCESS=0

# Método Primário: Transferência para /data/local/tmp e instalação com pm
if adb -s "$DEVICE_ID" push "$MOBILE_APK" /data/local/tmp/mark-mobile.apk; then
    echo "Executando instalação do pacote..."
    INSTALL_OUTPUT=$(adb -s "$DEVICE_ID" shell pm install -r -d /data/local/tmp/mark-mobile.apk 2>&1 || true)
    echo "$INSTALL_OUTPUT"
    if echo "$INSTALL_OUTPUT" | grep -q "Success"; then
        INSTALL_SUCCESS=1
    elif echo "$INSTALL_OUTPUT" | grep -qE "INSTALL_FAILED_UPDATE_INCOMPATIBLE|signatures"; then
        echo "Assinatura incompatível detectada com build anterior. Desinstalando versão antiga..."
        adb -s "$DEVICE_ID" uninstall "$PKG_NAME" || true
        REINSTALL_OUTPUT=$(adb -s "$DEVICE_ID" shell pm install -r /data/local/tmp/mark-mobile.apk 2>&1 || true)
        echo "$REINSTALL_OUTPUT"
        if echo "$REINSTALL_OUTPUT" | grep -q "Success"; then
            INSTALL_SUCCESS=1
        fi
    fi
    adb -s "$DEVICE_ID" shell rm -f /data/local/tmp/mark-mobile.apk || true
fi

# Fallback: adb install --no-streaming
if [ "$INSTALL_SUCCESS" -ne 1 ]; then
    echo "Tentando fallback com adb install --no-streaming..."
    INSTALL_OUTPUT=$(adb -s "$DEVICE_ID" install --no-streaming -r -d "$MOBILE_APK" 2>&1 || true)
    echo "$INSTALL_OUTPUT"
    if echo "$INSTALL_OUTPUT" | grep -q "Success"; then
        INSTALL_SUCCESS=1
    elif echo "$INSTALL_OUTPUT" | grep -qE "INSTALL_FAILED_UPDATE_INCOMPATIBLE|signatures"; then
        echo "Desinstalando versão prévia para resolver incompatibilidade..."
        adb -s "$DEVICE_ID" uninstall "$PKG_NAME" || true
        adb -s "$DEVICE_ID" install --no-streaming -r "$MOBILE_APK"
        INSTALL_SUCCESS=1
    fi
fi

if [ "$INSTALL_SUCCESS" -ne 1 ]; then
    echo "ERRO: Falha ao instalar o APK no smartphone." >&2
    exit 1
fi

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
echo "✓ Instalação concluída com sucesso em ${ELAPSED}s!"

# Confirmação do Caminho do Pacote
echo "Localização do APK no dispositivo:"
adb -s "$DEVICE_ID" shell pm path "$PKG_NAME"

# Limpa buffers antigos do logcat
adb -s "$DEVICE_ID" logcat -c

# Inicialização Remota
echo "Iniciando MARK Mobile via Launcher/Monkey..."
adb -s "$DEVICE_ID" shell monkey -p "$PKG_NAME" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1 || true
adb -s "$DEVICE_ID" shell am start -n "$PKG_NAME/$MAIN_ACTIVITY" >/dev/null 2>&1 || true

# Telemetria e Verificação de Logs
echo "Aguardando inicialização da UI (3 segundos)..."
sleep 3

echo ""
echo "=== Telemetria Inicial do Logcat (MARK Mobile) ==="
adb -s "$DEVICE_ID" logcat -d | grep -i -E "mark|bannaman|MainActivity|ScanContract|DeviceIdentity" | tail -n 30 || true

# Checagem de Erros Fatais (Crash/ANR)
CRASH_COUNT=$(adb -s "$DEVICE_ID" logcat -d | grep -i -E "FATAL EXCEPTION|ANR in $PKG_NAME" | wc -l || true)
if [ "$CRASH_COUNT" -eq 0 ]; then
    echo ""
    echo "✓ Zero exceções fatais ou ANRs detectados!"
    echo "=== Deploy e Inicialização no Smartphone Concluídos com Sucesso Absoluto! ==="
else
    echo "AVISO: $CRASH_COUNT exceções registradas no logcat. Analise os detalhes acima."
fi
