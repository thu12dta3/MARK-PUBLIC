# MARK Cognitive Foundation v0.1

A fundação organiza conhecimento, cálculo, avaliação e proveniência ao redor dos modelos já existentes. Não é um novo modelo treinado. A memória compartilhada, API, autorização, PC, Android, agenda e voz permanecem na arquitetura existente.

Fluxo integrado: pedido autenticado → comandos existentes → calculadora local, se aplicável → roteamento de câmera/web → recuperação local → ModelProvider → resposta com fontes consultadas. O registro de domínios fornece orientação adicional ao prompt. A API atende massa molar antes de carregar o modelo. Nenhum conteúdo de fonte, plano ou resposta do modelo despacha ações arbitrárias.

O código fica em `src/mark/cognition`, dividido por responsabilidade: schemas, domains, embeddings, knowledge, tools/chemistry, providers, integration, datasets/privacy, benchmark/performance, experiments/telemetry e cli. `brain.answer`, `_ollama` e `route` mantêm seus contratos.

A biblioteca privada fica em `~/.local/state/mark/cognition/knowledge.sqlite3`; datasets e modelos não são capturados automaticamente. Não há downloads novos nem treino. Desativar `cognition.enabled` permite manter apenas o fluxo anterior. `model_provider=ollama` preserva os Qwen existentes; o nome do modelo não é conhecido pelo banco ou pelo registry de tools.

O primeiro índice usa FTS5 e vetores de hashing lexical em CPU. Isso não é entendimento semântico de sinônimos. A interface permite outro embedding/provider e reindexação explícita. O código não precisa de framework de treinamento ou banco externo.

Estado e limitações: consulte `STATUS.md`, os documentos desta pasta e `MARK_COGNITIVE_FOUNDATION_REPORT.md`. O Android utiliza a API existente; não foi necessário modificar ou gerar outro APK nesta etapa.
