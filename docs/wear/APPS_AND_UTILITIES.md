# Aplicativos e utilidades do TicWatch — 09/10/2026

## Remoções solicitadas

Removidos apenas do usuário principal (`pm uninstall -k --user 0`), com retorno Success:

| Atalhos | Pacote |
| --- | --- |
| Fit, Fit Breathe, Treino Google Fit | `com.google.android.apps.fitness` |
| Google Pay | `com.google.android.apps.walletnfcrel` |
| Mobvoi Privacy | `com.mobvoi.wear.privacy.aw` |
| Tic Exercise | `com.mobvoi.wear.fitness.aw` |
| Tic Health | `com.mobvoi.wear.health.aw` |

Verificação posterior: ausentes de `pm list packages --user 0`. Dados mantidos com `-k`; APKs de fábrica permanecem na partição do sistema. Restauração, se solicitada: `adb -s SERIAL shell cmd package install-existing --user 0 PACOTE`. Nenhuma partição foi alterada. Home, MCU, FSTN, Bluetooth, serviços Google e mostradores OEM preservados.

### Correção de dependência — 10/10/2026

A remoção do Tic Health **não foi segura neste firmware**. Embora o pacote MCU tenha sido mantido, seu serviço persistente depende do provider `com.mobvoi.ticwear.steps`, fornecido pelo Tic Health. A verificação apenas de ausência do aplicativo não comprovou preservação funcional do MCU após iniciar o sistema.

Durante a investigação da tela Android Recovery, foram capturados crashes repetidos de `com.mobvoi.wear.mcuservice.aw` por provider ausente e escalada do RescueParty. Tic Health estava `installed=false` no usuário principal. O pacote original foi restaurado com `install-existing`, sem download, limpeza de dados ou alteração de partições. Depois, `installed=true`, StepProvider ativo e consumidor MCU conectado foram observados. A observação prolongada e o novo teste de inicialização devem constar do [relatório do incidente](../companion/testing/RECOVERY_INCIDENT_20261010.md).

Tic Health passa a ser **dependência de sistema a preservar** neste firmware; não remover ou desativar novamente para esconder seu atalho. Os outros pedidos de remoção anteriores não foram revertidos nesta correção. Não inferir que são seguros sem testes de dependência e inicialização.

## Instalações

- Spotify Wear 9.1.88.2204, pacote `com.spotify.music`, versionCode 146816072, minSDK 28, ABI armeabi-v7a incluída: instalação Success. APK original obtido do APKMirror porque a Play Store local solicitou conta Google. Assinatura verificada criptograficamente e comparada com o certificado publicado pelo próprio Spotify em [assetlinks.json](https://www.spotify.com/.well-known/assetlinks.json): SHA-256 `6505b181933344f93893d586e399b94616183f04349cb572a9e81a3335e28ffd`. Não é APK modificado nem versão para celular. Login/reprodução dependem do proprietário; instalação não comprova reprodução.
- Google Maps Wear 10.43.2, pacote `com.google.android.apps.maps`, versionCode 1043250000, minSDK 25, sem biblioteca nativa: instalação Success. Assinatura válida e igual à do Google Services Framework de fábrica do próprio relógio, SHA-256 `f0fd6c5b410f25cb25c3b53346c8972fae30f8ee7411df910480ad6b2d60db83`. Abertura e desenho do mapa observados no aparelho. É versão legada compatível com Android 9; não afirmar que possui os recursos das versões atuais. No Wear 2, as rotas passo a passo dependem do Google Maps no smartphone pareado, conforme [Google](https://support.google.com/wearos/answer/6056852?hl=pt-BR). Não foi feita campanha de navegação.
- Calculadora local no MARK Wear 0.3.5, versionCode 14: Activity independente e ícone próprio “Calculadora” no launcher. Sem acesso a celular, microfone, sensores ou rede no código da utilidade. Operações +, −, ×, ÷, decimais, parênteses, limpar e apagar. Parser de aritmética com BigDecimal/DECIMAL128; entrada limitada a 128 caracteres e saída limitada. Não executa scripts. Teclas visíveis simultaneamente na tela circular, estado preservado em recriação. Quatro testes unitários cobrem precedência, decimais, entradas inválidas, divisão por zero e limites.

Arquivos de terceiros, capturas com localização, inventários, logs e metadados de dispositivo ficam locais; não são publicados no Git. Certificado identifica o assinante, mas não equivale a auditoria completa de segurança de terceiros. [Suporte oficial Spotify](https://support.spotify.com/us/article/spotify-on-wear-os/).
