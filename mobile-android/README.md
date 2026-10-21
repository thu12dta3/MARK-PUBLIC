# MARK Mobile 0.3.11 (candidato de uso pessoal)

Aplicativo Android nativo em Kotlin e Jetpack Compose. Requer Android 12 ou superior. A paleta é escura com azul/ciano. Não coleta IMEI, serial, SIM, MAC ou Android ID.

## Recursos implementados

- Pareamento QR com aprovação no PC, chave por instalação no Android Keystore, TLS validado e certificado do PC fixado.
- Chat compartilhado com perfis Qwen normal e rápido, histórico, anexos de texto/imagem/PDF, agenda, seleção como assistente padrão por gesto e chamada de voz por turnos no app.
- Diagnóstico local de sensores, câmeras, USB Host, biometria, codecs, tela, RAM e bateria em tempo de execução.
- Foto feita dentro do app com CameraX: prévia da câmera traseira, captura e anexo sob demanda; a imagem fica na memória do app para envio no chat.
- Modo AR com ARCore: detecta planos e coloca o marcador 3D do MARK onde o usuário tocar. No primeiro uso, o app solicita ao Android a instalação/atualização oficial dos Serviços do Google Play para RA, confirma o resultado ao voltar e pede permissão de câmera.
- Mira quadrada central com ponto de foco no Modo AR para facilitar alinhar e posicionar o marcador.
- Modo AR usa explicitamente a câmera traseira de maior resolução disponível e foco automático; a tela informa o estado inicial do rastreamento e inclui detalhes de falha da sessão.
- Diálogos de permissão de câmera não disparam o bloqueio do MARK enquanto o sistema os apresenta.
- Diagnóstico AR explica falhas de início e rastreamento; erro de carregamento do marcador não falha silenciosamente.
- HUD AR futurista e discreto, com mira fina, indicadores de rastreamento e painéis compactos translúcidos.
- Rotação mantém AR ou chamada abertos; AR consulta hit test mesmo quando a camada 3D já relata um nó sob o toque e indica quando reconhece uma mesa.
- HUD inclui nível eletrônico ao vivo com bolha/graus e leituras de câmera em estilo manual; o monograma M grande saiu da tela inicial sem conversa.
- HUD AR refinado com visor mais livre, régua de nível central e faixa inferior compacta para estado/controles.
- AR procura e aceita planos horizontais e verticais; HUD paisagem tem layout próprio e mais baixo.
- Ícones vetoriais consistentes para chamada, câmera, anexos, AR e controles principais; sem emojis dependentes de fontes do telefone.
- Mantém a sessão desbloqueada durante uma rotação da tela, mas volta a exigir biometria quando o app vai para segundo plano ou é iniciado novamente.
- Chat identifica mensagens enviadas pelo PC e pelo celular no histórico compartilhado.
- App PC inclui chamada de voz local na própria janela, sem abrir navegador, e sincroniza o histórico compartilhado.

## Compilar

Use Android SDK API 37 e JDK 17: `./gradlew assembleDebug lintDebug`. Para uma compilação assinada, configure `MARK_ANDROID_KEYSTORE` e `MARK_ANDROID_KEY_PASSWORD_FILE` com caminhos locais privados e rode `./gradlew assembleRelease lintRelease`. O APK fica em `app/build/outputs/apk/release/app-release.apk`. Não versione as credenciais de assinatura.

Para conectar, siga [docs/MOBILE.md](../docs/MOBILE.md). As pendências de segurança e funcionalidades estão em [docs/MOBILE_SECURITY.md](../docs/MOBILE_SECURITY.md) e [docs/MOBILE_REQUIREMENTS.md](../docs/MOBILE_REQUIREMENTS.md). O ARCore é uma instalação separada da Play Store; no APK o pedido de instalação aparece ao tocar em “Modo AR”. A sessão AR ainda precisa ser reaberta no Galaxy M23 para capturar a mensagem de diagnóstico e confirmar o rastreamento. A seleção de maior resolução já era padrão da biblioteca e não melhorou a qualidade observada. O gate global de segurança continua incompleto.
