# Pesquisa inicial e proveniência

Consulta10/10/2026. Nenhum código proprietário/AGPL importado ao MARK.

## APK original, análise local autorizada

AAPT extraiu manifestos dos APKs efetivamente instalados no telefone e relógio. JADX1.5.6 obtido da release oficial, digest SHA256 comparado ao metadado GitHub, usado somente fora do Git. Tentativa de instalar pelo pacman exigiu sudo; adotada ferramenta portátil sem modificar o sistema.

Manifesto do telefone identifica NotificationCollectorService/NotificationBridgerService, IncomingCallService, PackageDeliveryService, PackageUpdateService, DispatchingWearableListenerService, PairingActivity e serviços de conexão/relacionamento. Permissões incluem Bluetooth/scan, estado de telefonia e atendimento; listener exige BIND_NOTIFICATION_LISTENER_SERVICE. Manifesto não demonstra que todas as permissões foram concedidas ou que funcionem sem GMS.

Decompilação seletiva mostrou NotificationCollectorService derivando de ListenableNotificationListenerService, que por sua vez usa a API pública NotificationListenerService e callbacks reais de publicação/remoção. PackageDeliveryService referencia ChannelImpl do GMS/WearableHost e metadados de companion; não é instalador genérico copiável. Algumas regiões apresentam avisos de decompilação. Protocolo original completo, privilégios e pareamento pós-reset NÃO foram reproduzidos. Fontes decompiladas, APKs e dumps privados permanecem fora do repositório.

## Referências abertas

- [DieselBridge](https://github.com/A-Australian/dieselbridge): README atual descreve peripheral BLE com NUS/protocolo Bangle.js, telefone Gadgetbridge, notificações/chamadas/mídia. Apache2.0 declarada no repositório. O projeto não comprova pareamento de sistema pós-reset nem substituição de privilégios OEM. Não adotar canal UART sem conferir autenticação/composição das versões reais. Nesta revisão, comparação documental; ensaio do projeto no TicWatch ainda pendente.
- [Gadgetbridge](https://gadgetbridge.org/), [repositório](https://codeberg.org/Freeyourgadget/Gadgetbridge): arquitetura de integração por dispositivo e notificações; não importar código AGPL para projeto proprietário. DieselBridge usa compatibilidade Bangle.js como adaptador, não implementação nativa completa de Wear OS no Gadgetbridge. Verificar versão concreta antes de integrar.
- [CompanionDeviceManager](https://developer.android.com/develop/connectivity/bluetooth/companion-device-pairing): associação e descoberta mediadas pelo Android; associação não é estabelecimento automático de socket nem grant do assistente. Disponível desdeAPI26, mas confirmar serviços presentes na ROM Wear antiga.
- [Permissões Bluetooth](https://developer.android.com/develop/connectivity/bluetooth/bt-permissions): Android12+ exige consentimento runtime CONNECT/SCAN/ADVERTISE; escaneamento BLE legado tem requisitos de localização. O caminho transitório atual usa apenas dispositivos já bonded e CONNECT.
- [GATT](https://developer.android.com/develop/connectivity/bluetooth/ble/connect-gatt-server): conexão/operações assíncronas; MTU, fragmentação, confirmações e autenticação devem ser implementados/testados antes da migração.
- [PackageInstaller.Session](https://developer.android.com/reference/android/content/pm/PackageInstaller.Session): API de staging/commit, resultado assíncrono e possível confirmação do usuário. No API28 não presumir APIs recentes de pré-aprovação.
- [AOSP PackageInstaller Wear](https://android.googlesource.com/platform/packages/apps/PackageInstaller/+/9c78316/src/com/android/packageinstaller/wear/WearPackageInstallerService.java): implementação privilegiada Wear não concede INSTALL_PACKAGES ao MARK. Versão fonte não foi equiparada automaticamente ao binário OEM.
- [F-Droid APIs](https://f-droid.org/docs/All_our_APIs/): índices assinados e hashes de pacotes. Catálogo externo só ativar após verificar signer confiável do índice e cadeia de hashes, não baixar APK a partir de JSON arbitrário. Catálogo próprio inicial contém apenas fixture assinada do próprio MARK.

## Decisão de transporte

RFCOMM já comprovado no hardware é mantido como implementação transitória com UUID de administração distinto. Isso não atende ainda à prioridade BLE, nem demonstra vantagem de consumo sobre GATT. A mudança definitiva requer ensaio BLE bidirecional e medição em repouso, sem enfraquecer autenticação/aprovação.

## Falha reproduzida no Android9 ao ler assinatura

A primeira campanha da loja no TicWatch falhou: SigningInfo nulo ao usar apenas GET_SIGNING_CERTIFICATES. O [PackageManager do AOSP Android9](https://android.googlesource.com/platform/frameworks/base/+/android-9.0.0_r1/core/java/android/content/pm/PackageManager.java) chama collectCertificates somente quando GET_SIGNATURES está presente no getPackageArchiveInfo. collectCertificates usa skipVerify=false. A correção acrescenta a flag legada somente no API28, mantendo a leitura de SigningInfo e a verificação, sem aceitar APK sem certificado. Adicionado teste físico que modifica classes.dex de um APK assinado e exige rejeição. Resultado final de regressão deve ser registrado na campanha, não inferido do código-fonte.
