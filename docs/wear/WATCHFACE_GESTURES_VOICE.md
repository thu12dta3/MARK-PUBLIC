# Watchface, gestos e canal de voz — Wear 0.3.5

## Configuração atual

O proprietário selecionou a watchface MARK no relógio em 08/10/2026; a seleção foi confirmada por inspeção do sistema. Home OEM continua principal, com HomeEntry do MARK desabilitada. Essa integração preserva os painéis nativos e o botão superior. Não reativar a substituição de Home que causou regressão em 0.3.1.

| Gesto no mostrador MARK | Destino |
| --- | --- |
| Esquerda → direita | Canal de voz do MARK |
| Direita → esquerda | Menu MARK |
| Baixo → cima | Notificações nativas do Wear OS |
| Cima → baixo | Controles rápidos nativos |
| Toque inferior em VOZ / MENU / TEMPO | Voz / menu / Google Weather |

A watchface usa WallpaperService/CanvasWatchFaceService legada compatível com API 28. O reconhecimento de gesto horizontal exige confirmação de toque recebida pelo callback nativo do mostrador, duração limitada e tolerância temporal de 500 ms. Toques espelhados sob painéis OEM não devem abrir o MARK. Os testes no dispositivo reproduziram esse problema inicial e comprovaram a correção: gesto sobre notificação manteve HomeActivity2 OEM. Classificador puro tem cinco testes (horizontal, vertical, diagonal, cancelamento e bloqueio fora do mostrador). Os gestos verticais não são consumidos pelo MARK.

Google Weather abre a Activity exportada existente no firmware; previsão foi visualizada. Não foi criada uma cópia de notificações nem concedido acesso a NotificationListener. Esse dispositivo é low-RAM/API 28, no qual Android limita o acesso a notificações. Não apresentar notificações nativas como implementação de um leitor MARK.

## Canal de voz

Página nativa dentro do app, com Entrar, Encerrar e Conectar celular. Requer ponte autorizada com M23 e permissão de microfone. Reconhecimento e síntese usam os serviços Android instalados, pt-BR, com preferência por reconhecimento offline (preferência não garante execução local). O reconhecimento pode usar Google/internet; a interface informa isso. Perguntas usam a ponte existente, perfil rápido/call e origem wear. A resposta é falada; o reconhecedor volta a ouvir após a síntese.

Não é WebRTC nem áudio duplex: implementação por turnos de fala, preservando o protocolo de perguntas já existente. Microfone só inicia pela ação visível Entrar. Sair da página/app, erro, timeout e Encerrar destroem o canal/recursos. Não há wake word ou microfone sempre ativo no mostrador.

Compilação e lint PASS; abertura da página VOICE pelo gesto comprovada. Reconhecimento → M23 → PC → resposta falada ainda NOT TESTED nesta campanha: exige vínculo e autorização humana no M23. HIGH do celular bloqueado continua aberto. Biometria/SAS não são automatizados.

Boot real, botão físico superior, FSTN físico e campanha de bateria ainda pendentes. Ciclo de vida do mostrador fica com Wear OS, sem receiver que abra Activity no boot.

Referências: [WallpaperService.Engine](https://developer.android.com/reference/android/service/wallpaper/WallpaperService.Engine), [NotificationListenerService](https://developer.android.com/reference/android/service/notification/NotificationListenerService).
