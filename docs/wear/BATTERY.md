# Energia

Estado de consumo real: UNVERIFIED / PHYSICAL TEST REQUIRED. Não há números inventados de autonomia.

SMART é padrão: listeners no foreground, descoberta por evento/ação, sensor apenas sob demanda e liberação ao completar leitura, sair da tela ou no máximo 15s. ULTRA SAVE suspende sincronização opcional e limita sensor a 8s; bateria abaixo de 15% também suspende sync opcional. ACTIVE permite uso explícito, mantendo o teto de sensores. Backoff entre tentativas manuais vai até 60s; não há retry loop automático.

Manifesto Wear não pede WAKE_LOCK, localização, foreground service ou gravação contínua. Voz usa serviço externo solicitado pelo usuário. Sem render loop, AOD, Wi-Fi forçado, controle da tela secundária ou timer a cada segundo. OnStop remove listeners/callbacks e sensores. O sistema conserva seu modo econômico.

## Baseline físico

A: Wear OS original sem MARK ativo. B: MARK instalado sem interação. C: uso normal controlado. Aproximadamente 24h por condição, repetir quando viável; não executar automaticamente. Registrar saúde/carga da bateria, brilho, Bluetooth/Wi-Fi, GPS, voz, notificações, atividade e versão do sistema. Comparar descarga/tempo com incerteza; total do relógio não equivale a consumo do MARK.

Reprovar sensor/microfone esquecido, wake lock persistente, rede contínua, fila crescente, callbacks com app oculto ou reconexão agressiva. Verificar retornos ao display econômico, suspensões e dumpsys power/battery localmente no aparelho. Estatísticas privilegiadas indisponíveis ficam UNAVAILABLE.
