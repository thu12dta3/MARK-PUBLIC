#!/usr/bin/env bash
# ==============================================================================
# MARK Ecosystem - Build e Assinatura de APKs Multiplataforma (Fase 4)
# Compila e assina os APKs do MARK Mobile (Android) e MARK Wear (Wear OS)
# Destino dos artefatos: build/
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== MARK: Iniciando compilação de APKs Multiplataforma ==="

# 1. Configuração do Ambiente JDK e Android SDK
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk}"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/.cache/mark-android-sdk}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"

if [ ! -d "$JAVA_HOME" ]; then
    echo "ERRO: JAVA_HOME não encontrado em: $JAVA_HOME" >&2
    exit 1
fi

if [ ! -d "$ANDROID_HOME" ]; then
    echo "ERRO: ANDROID_HOME não encontrado em: $ANDROID_HOME" >&2
    exit 1
fi

# 2. Credenciais locais de assinatura
KEYSTORE_PATH="${MARK_ANDROID_KEYSTORE:-$HOME/.config/mark/android-release.p12}"
KEY_PASSWORD_PATH="${MARK_ANDROID_KEY_PASSWORD_FILE:-$HOME/.config/mark/android-release-password}"

if [ -f "$KEYSTORE_PATH" ] && [ -f "$KEY_PASSWORD_PATH" ]; then
    export MARK_ANDROID_KEYSTORE="$KEYSTORE_PATH"
    export MARK_ANDROID_KEY_PASSWORD_FILE="$KEY_PASSWORD_PATH"
    echo "Assinatura: Chave de release configurada com sucesso ($KEYSTORE_PATH)."
else
    echo "AVISO: Chave de release privada não encontrada. Usando modo padrão de compilação."
fi

# 3. Diretório de Saída
OUTPUT_DIR="$REPO_ROOT/build"
mkdir -p "$OUTPUT_DIR"

# 4. Execução do Gradle Wrapper
echo "Compilando :app:assembleRelease e :wear:assembleRelease..."
cd "$REPO_ROOT/mobile-android"
./gradlew --no-daemon :app:assembleRelease :wear:assembleRelease

# 5. Cópia dos Artefatos para build/
MOBILE_APK_SRC="$REPO_ROOT/mobile-android/app/build/outputs/apk/release/app-release.apk"
WEAR_APK_SRC="$REPO_ROOT/mark-wear/app/build/outputs/apk/release/wear-release.apk"

MOBILE_APK_DEST="$OUTPUT_DIR/mark-mobile-release.apk"
WEAR_APK_DEST="$OUTPUT_DIR/mark-wear-release.apk"

if [ ! -f "$MOBILE_APK_SRC" ]; then
    echo "ERRO: Artefato Mobile não encontrado em $MOBILE_APK_SRC" >&2
    exit 1
fi

if [ ! -f "$WEAR_APK_SRC" ]; then
    echo "ERRO: Artefato Wear OS não encontrado em $WEAR_APK_SRC" >&2
    exit 1
fi

cp -f "$MOBILE_APK_SRC" "$MOBILE_APK_DEST"
cp -f "$WEAR_APK_SRC" "$WEAR_APK_DEST"

echo ""
echo "=== Artefatos gerados com sucesso em $OUTPUT_DIR ==="
ls -lh "$MOBILE_APK_DEST" "$WEAR_APK_DEST"

echo ""
echo "=== Hashes SHA-256 ==="
sha256sum "$MOBILE_APK_DEST" "$WEAR_APK_DEST"

# 6. Verificação de Assinatura com apksigner (se disponível)
APKSIGNER="$(find "$ANDROID_HOME/build-tools" -name apksigner 2>/dev/null | sort -V | tail -n 1 || true)"
if [ -n "$APKSIGNER" ] && [ -x "$APKSIGNER" ]; then
    echo ""
    echo "=== Verificação Criptográfica com apksigner ==="
    "$APKSIGNER" verify "$MOBILE_APK_DEST" && echo "✓ mark-mobile-release.apk: Assinatura VÁLIDA"
    "$APKSIGNER" verify "$WEAR_APK_DEST" && echo "✓ mark-wear-release.apk: Assinatura VÁLIDA"
fi

echo ""
echo "=== Build Concluído com Sucesso! ==="
