# Roadmap do MARK

## Cognitive Foundation v0.1 — entrega atual

Schemas, ingestão, busca local, citações, roteamento, providers, tools iniciais, dataset validator/split, MARKBench, experimentos, testes e documentação. Resultados e limitações reais em STATUS.md e MARK_COGNITIVE_FOUNDATION_REPORT.md.

## Cognitive Foundation v0.2 — execução parcial autorizada

Etapas de calculadoras/assistência, recuperação de respostas vazias e QLoRA experimental começaram por solicitação explícita; não significam conclusão de toda v0.2. [Continuidade atual](docs/cognition/CONTINUITY_20261011.md), [experimentos de treino](docs/cognition/TRAINING_20261011.md). Bibliotecas maiores, embedding semântico e validação ampla permanecem pendentes.

1. Incorporar primeiras bibliotecas realmente licenciadas de Computing, Electronics e Chemistry; revisar metadata e datas.
2. Avaliar embedding semântico pequeno, qualidade híbrida e reranking contra o baseline lexical, com orçamento local medido.
3. Expandir calculadoras, casos de erro e comparação entre dado observado, claim e fato.
4. Criar poucos registros AFV de fontes permitidas, com status e história temporal revisados.
5. Expandir MARKBench além de respostas curtas: síntese com citações, ambiguidade, falhas reais e revisão humana.
6. Investigar a issue #1 das respostas vazias do perfil chat antes de declarar evolução de qualidade.
7. Revisar datasets, escolher modelo candidato licenciado, medir novo baseline e só então experimentar QLoRA pequeno.

## Evolução maior — PLANNED

Família Nano/Core/Deep/Vision/Embeddings, distillation, fallback por confiança, verifier mais completo e personalização controlada pelo proprietário. Não substituir recursos atuais para reduzir custo.

A dívida de segurança permanece em docs/SECURITY_DEBT.md. Novas etapas e treinamento não começam automaticamente; respeitar aprovação de dados, decisões do proprietário e validações necessárias.

## Evoluções de produto

- Motor dedicado de wake word e VAD neural para reduzir falsos positivos.
- Reduzir a latência da chamada web com transmissão incremental e permitir uso confiável com a tela bloqueada.
- Integração com Arduino e sensores do capacete.
- HUD e interface de estado visual.
- Execução parcial dos módulos em Raspberry Pi.
- Cancelamento de resposta por voz e interrupção (barge-in).
- Afinar latência e privacidade dos pedidos ao roteador.

## MARK Wear

- v0.1: app convencional, status/perguntas, sensores sob demanda, ponte assinada via M23, telemetria técnica opt-in; PARTIAL até campanha física no TicWatch.
- v0.2: watchface/Tile compatíveis, notificações/haptics, roteamento para fone, leitura cardíaca encaminhada e comparação energética; PLANNED após validar v0.1.
- v0.3: launcher dentro das possibilidades oficiais, atalhos/adaptação e perfil opcional; PLANNED.

Não iniciar fases posteriores nem integração privilegiada antes da validação/revisão específica.

## Wear v0.3 iniciada por solicitação explícita

Launcher/apps/sistema/watchface e mapping local implementados; completar campanha física e resolver #9 antes de declarar fase concluída. Nível C privilegiado não foi iniciado.
