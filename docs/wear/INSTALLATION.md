# Build, instalação e recuperação

## Pré-requisitos

JDK 17, SDK Android utilizado pelo projeto, Gradle wrapper existente. ADB deve ser instalado/configurado antes do teste. No relógio, habilitar opções de desenvolvedor e depuração pelo procedimento oficial da versão instalada. Não usar root, flash ou desativar componentes OEM.

A partir de mobile-android:

```bash
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk
export ANDROID_HOME="$HOME/.cache/mark-android-sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
./gradlew --no-daemon :wear:assembleDebug :wear:lintDebug :wear:testDebugUnitTest :wear-protocol:testDebugUnitTest :app:assembleDebug :app:lintDebug
```

Debug usa o mesmo debug keystore nos dois módulos desse computador. Não instala sobre Mobile assinado pelo proprietário. Para release, usar arquivos locais privados já existentes; não publicar valores:

```bash
export MARK_ANDROID_KEYSTORE="$HOME/.config/mark/android-release.p12"
export MARK_ANDROID_KEY_PASSWORD_FILE="$HOME/.config/mark/android-release-password"
./gradlew --no-daemon :wear:assembleRelease :wear:lintRelease :app:assembleRelease :app:lintRelease
```

APK Wear: ../mark-wear/app/build/outputs/apk/release/wear-release.apk. APK Mobile: app/build/outputs/apk/release/app-release.apk. Confirmar assinatura correspondente com apksigner verify --print-certs, sem trocar/desinstalar a identidade do Mobile. Nunca copiar o keystore para o relógio.

## Diagnóstico e instalação

Substituir WATCH_SERIAL e PHONE_SERIAL pelos dispositivos confirmados em adb devices -l. Não presumir qual deles é o relógio.

```bash
adb devices -l
python3 ../scripts/wear_diagnose.py --adb "$ANDROID_HOME/platform-tools/adb" --serial WATCH_SERIAL --output "$HOME/.local/state/mark-wear-development/watch-diagnostics.json"
adb -s WATCH_SERIAL install -r ../mark-wear/app/build/outputs/apk/release/wear-release.apk
adb -s PHONE_SERIAL install -r app/build/outputs/apk/release/app-release.apk
adb -s WATCH_SERIAL shell pm path br.com.bannaman.markmobile
adb -s WATCH_SERIAL shell am start -n br.com.bannaman.markmobile/br.com.bannaman.markwear.MainActivity
```

No M23: MARK → Configurar servidor → MARK Wear → Encontrar e autorizar relógio → biometria forte; manter a tela aberta. No relógio: Configurações → Escolher celular → selecionar M23. Testar STATUS e uma pergunta com PC/API disponível pela configuração segura existente. Bluetooth/Wear OS pareado não substitui aprovação do MARK.

## Logs sanitizados

```bash
adb -s WATCH_SERIAL logcat -d -s AndroidRuntime:E '*:S'
```

O comando filtra erros, mas dumps podem conter identificadores/dados do sistema. Revisar e redigir antes de compartilhar. Diagnósticos brutos ficam locais 0700/0600; não versioná-los. Não logar voz, resposta, nó, token ou saúde.

## Desinstalação / recuperação

```bash
adb -s WATCH_SERIAL uninstall br.com.bannaman.markmobile
```

Só usar esse comando no serial do relógio; não desinstalar Mobile do M23 como método de atualização. Remover MARK Wear não remove Wear OS, watchface OEM ou apps. Se houver erro, encerrar pelo gerenciador do sistema, revogar a ponte no M23 e manter a watchface original. Rollback do Mobile deve preservar o app/dados e respeitar versionCode/assinatura; não executar downgrade/uninstall sem aprovação e plano de recuperação.
