# MARK no celular

O MARK roda no PC; o MARK Mobile usa Tailscale Serve por HTTPS. O app Android é nativo (Kotlin/Compose), com chat, memória compartilhada, dois perfis Qwen, agenda, anexos e chamada de voz por turnos no próprio app. O LocalMind continua disponível pela API OpenAI com a chave legada; ele não usa o pareamento por aparelho do MARK Mobile.

## Conectar o Android

1. Conecte PC e celular à mesma rede Tailscale. No PC, confirme que `mark-mobile.service` está ativo e que o Tailscale Serve expõe `127.0.0.1:8765` por HTTPS.
2. No PC, execute `MARK --mobile-pair https://SEU-PC.SEU-TAILNET.ts.net`. O comando valida TLS 1.3 e mostra um QR temporário com endereço, código de uso único e impressão SHA-256 do certificado HTTPS. O código expira em cinco minutos.
3. No celular, abra MARK Mobile, toque **Ler QR do computador** e **Solicitar pareamento**. O aplicativo gera uma chave EC P-256 não exportável no Android Keystore e prova a posse dela ao PC.
4. No PC, use `MARK --mobile-devices` para ver o aparelho `pending`. Compare o início do ID mostrado no celular e no PC. Aprove com `MARK --mobile-approve ID_COMPLETO`.
5. No celular, use o botão **Atualizar conversa**. O status deve passar a conectado, e a conversa compartilhada aparece.

## Câmera e modo AR

- A câmera de foto usa uma tela de prévia dentro do app; a imagem só é anexada se você confirmar a captura.
- O modo AR é opcional e usa ARCore. Na primeira abertura, o botão **Modo AR** chama a instalação/atualização oficial dos **Serviços do Google Play para RA** pela Play Store; permita essa instalação e toque em **Modo AR** outra vez quando concluir. Depois, autorize a câmera para iniciar a detecção de superfícies. Se preferir, abra [Serviços do Google Play para RA na Play Store](https://play.google.com/store/apps/details?id=com.google.ar.core).
- Na tela AR, a câmera traseira de maior resolução disponível e o foco automático são selecionados explicitamente. Uma mira quadrada central ajuda a enquadrar a região de colocação. Mova o telefone devagar até detectar uma superfície e toque na mira para colocar ou mover o marcador. O estado de rastreamento e erros da sessão aparecem na própria tela.
- Ao pedir a permissão de câmera, o app mantém o MARK desbloqueado enquanto o diálogo de sistema está aberto; isso evita que a autenticação interrompa a abertura do modo AR ou da câmera.
- A versão 0.3.6 mostra causas de falha do rastreamento AR. Abra **Modo AR** e leia a mensagem na faixa inferior para diagnosticar o caso. A qualidade continua sem correção confirmada: a seleção de maior resolução já era o padrão da biblioteca e não alterou a imagem observada.
- A versão 0.3.7 redesenha a camada visual do AR como um HUD fino em ciano, com indicadores de estado e controles compactos translúcidos para deixar a câmera visível.
- Na versão 0.3.8, girar o aparelho preserva a tela AR e a chamada. Para posicionar o marcador, mantenha uma mesa no centro e mova o celular devagar; o HUD mostra quando reconheceu o plano. Toques agora fazem o teste de plano mesmo quando há um nó 3D sob o dedo.
- Na versão 0.3.9, o HUD ganha nível eletrônico em tempo real com bolha e graus, além de pequenas leituras ISO/WB/EV/AF inspiradas no modo manual Zenfone 3. O “M” grande do estado inicial foi removido.
- A versão 0.3.10 simplifica a sobreposição: visor mais limpo, régua do nível central e uma única faixa inferior para dados e estado.
- A versão 0.3.12 ativa Depth API quando suportada e aceita DepthPoints, além dos planos horizontais e verticais. O HUD mostra o número de planos rastreados. O mapa espacial dura só enquanto a sessão AR estiver aberta, não é gravado no armazenamento e é descartado ao sair do AR. A câmera usa autofocus e a maior resolução ARCore disponível a 30 fps; confirme a nitidez no M23.
- A versão 0.3.11 adapta o HUD para paisagem com uma barra inferior única.
- O Galaxy M23 5G está na lista oficial de aparelhos compatíveis com ARCore. A disponibilidade também depende de uma versão compatível do Android e dos Serviços do Google Play para RA instalados.
- O pacote Android `com.google.ar.core` é instalado pela Play Store e atualizado separadamente do MARK; não instale APKs obtidos de sites de terceiros.
- Fontes: [instalação do ARCore em apps Android](https://developers.google.com/ar/develop/java/enable-arcore) e [lista oficial de aparelhos compatíveis](https://developers.google.com/ar/devices).

O QR só funciona para uma tentativa. Se o certificado HTTPS do PC mudar, o app bloqueia a conexão; confirme a mudança no PC e faça um novo pareamento. Não cole o código ou QR em chats públicos.

## Controles do PC

- `MARK --mobile-devices`: lista IDs, nomes e estados.
- `MARK --mobile-revoke ID_COMPLETO`: revoga um aparelho imediatamente.
- `MARK --mobile-lockdown on`: fecha novos pareamentos e nega pedidos dos aparelhos. `off` restaura o acesso aos aparelhos já autorizados; pareamentos pendentes são revogados.

O segredo antigo de `MARK --mobile-token` continua apenas para clientes OpenAI compatíveis, como LocalMind. O MARK Mobile não aceita nem guarda esse segredo. Para segurança máxima, retire clientes legados e desative o acesso por Bearer token em uma futura migração coordenada.

## Uso atual e limitações

O chat aceita texto, imagens JPEG/PNG/WebP, arquivos de texto e a primeira página de PDFs como imagem. O servidor limita tamanho e tipo. A agenda do PC é visível no celular e pode ser alterada conversando com o MARK. O botão do assistente pede ao Android para selecionar o MARK como assistente padrão por gesto; a chamada abre quando o sistema invoca o app. Isso não habilita palavra de ativação em segundo plano. A chamada usa microfone e áudio sintetizado no app, alternando gravação e reprodução; ela ainda **não** usa WebRTC nem fala simultânea. Ao encerrar, o microfone é liberado.

O APK de depuração sai de `mobile-android` com `./gradlew assembleDebug` (SDK API 37, JDK 17). Há também uma compilação de release assinada e otimizada, gerada com `assembleRelease`; a chave de assinatura fica somente no PC. O app mantém o desbloqueio e as telas AR/chamada durante mudanças de configuração, como rotação, mas volta a exigir biometria ao ir para segundo plano ou ao iniciar após encerramento. A versão 0.3.9 inclui nível eletrônico e controles compactos no HUD AR. Esta compilação ainda não é estável: a detecção de plano precisa de confirmação física e a qualidade de imagem reportada continua sem solução. Ainda faltam `VoiceInteractionService` e palavra de ativação, WebSocket/WebRTC, localização, integração UVC, widget e campanha física completa no Galaxy M23. Veja [requisitos de segurança](SECURITY_REQUIREMENTS.md), [modelo de ameaças](THREAT_MODEL.md) e o [histórico de versões Android](../mobile-android/CHANGELOG.md).
