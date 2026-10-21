# Sensores e saúde

Sensor Lab lista somente SensorManager.getSensorList(TYPE_ALL): nome, fabricante, tipo, resolução, faixa e corrente declarada. Corrente nominal do sensor não é consumo medido do MARK. Toque inicia uma leitura SENSOR_DELAY_NORMAL, com timeout/liberação; falha e SecurityException são exibidas. Nenhum GPS foi solicitado.

HEALTH verifica se TYPE_HEART_RATE existe. Ativação explícita libera coleta local; BODY_SENSORS em APIs antigas e READ_HEART_RATE em API 36+ quando aplicável, sempre com permissão em runtime. Leitura não finita/fora de 25–240 bpm é recusada; essa faixa é validação técnica, não diagnóstico. Sem leitura válida, marcar indisponível. Saúde pausada também bloqueia leitura cardíaca via Sensor Lab.

HealthReading distingue ATUAL (até 30s), RECENTE (até 5min), ANTIGA, QUALIDADE INCERTA (accuracy não confiável) e INDISPONÍVEL. Idade é baseada no timestamp do evento do sensor (elapsedRealtime), não apenas no recebimento. Timestamp ausente/incoerente implica qualidade incerta. A idade exibida atualiza por eventos/navegação, sem polling. Atividade física: UNAVAILABLE, sem inferência automática.

Leitura fica apenas em memória da Activity e é apagada ao reiniciar/processo; sem histórico, gravação ou envio ao PC nesta v0.1. Settings permite pausar/limpar. Perfil de saúde e leitura encaminhada por comando são PLANNED para fase posterior com consentimento específico. Sensores reais, qualidade e permissões: PHYSICAL TEST REQUIRED.
