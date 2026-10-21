# Arquitetura

## Reuso

O build existente em mobile-android coordena :app (M23), :wear (mark-wear/app) e :wear-protocol (mark-wear/protocol). Mesmo applicationId br.com.bannaman.markmobile; namespaces distintos. Release usa a chave local de proprietário já existente. Gradle e SDK são reaproveitados; novos downloads apenas de dependências necessárias, nenhum modelo de IA.

MessageClient transporta pequenos envelopes; CapabilityClient descobre mark_phone_v1/mark_wear_v1 e seleciona explicitamente o peer. NodeClient foi avaliado: a lista de nós conectados inclui aparelhos sem MARK; preferimos nós de CapabilityClient. DataClient não é usado para conversas/saúde porque persistiria/retransmitiria conteúdo além do pedido. ChannelClient não é necessário sem áudio bruto. Listener existe somente no foreground.

Envelope v1: v, id (32 hex), kind, at (milissegundos), payload. Máximo 8192 bytes, TTL cinco minutos, texto 2000 caracteres. Tipos status/ask/telemetry/reply/error; sem path arbitrário, admin, segredo ou transferência de arquivo. RequestGate rejeita replays e limita 64 ids por janela de cinco minutos. O celular tem executor único e no máximo duas tarefas pendentes; relógio aceita uma solicitação por vez.

Mobile reaproveita MarkApi, TLS 1.3, pin, DeviceIdentity e sessão curta do PC. ASK usa mark-call e a mesma memória; origem wear fica no corpo assinado e é registrada como wear-chat. Comandos críticos continuam passando pelo bloqueio do servidor; nenhuma prova biométrica é inventada a partir da voz ou da presença do relógio.

PC acrescenta apenas rotas /v1/wear/telemetry e /v1/wear/summary e armazenamento técnico dedicado. Bearer legado não autoriza essas rotas. Sem refazer modelos, agenda ou autenticação.

## Limites

Ponte apenas com tela Wear aberta no M23, autorização biométrica forte e cinco minutos de sessão. Rotação/segundo plano encerra essa ponte na v0.1. Não há notificação push, serviço contínuo, áudio remoto ou sincronização automática. Mensagens podem usar infraestrutura Google fora do Bluetooth; não presumir transporte local exclusivo.

Fontes oficiais: [Data Layer](https://developer.android.com/training/wearables/data/overview), [MessageClient](https://developers.google.com/android/reference/com/google/android/gms/wearable/MessageClient).
