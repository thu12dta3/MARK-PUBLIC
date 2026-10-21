# MARKBench v0.1

50 casos autorais de laboratório em `benchmarks/markbench-v0.1.json`; não são extraídos da conversa privada. Categorias: GENERAL, TOOL_SELECTION, KNOWLEDGE_RETRIEVAL, COMPUTING, ELECTRONICS, AUTOMOTIVE, CHEMISTRY, LAW, HISTORY, MILITARY_HISTORY, AFV, SECURITY e FAILURE_RECOVERY.

Cada caso tem ID, categoria, dificuldade, prompt, contexto, tools, comportamentos requeridos/proibidos, chamadas/fatos esperados e scoring. As operações são allowlisted. Exemplos numéricos têm tolerância; roteamento exige os domínios; recuperação exige a fonte e vínculo; segurança verifica grants e recusa de side effects; falha de banco não derruba conversa.

42 casos verificam componentes reais sem modelo. 8 chamam o provider para respostas curtas, distinções UNKNOWN/STALE/PROTOTYPE/CLAIMED e confiança em fontes. `--provider none` marca esses oito SKIPPED, nunca PASS. `--provider ollama --profile chat|call` usa apenas os Qwen já instalados, a configuração do perfil e fixtures próprias; não lê memória privada, abre câmera ou fala.

```bash
MARK benchmark run
MARK benchmark run --provider ollama --profile chat
MARK benchmark run --provider ollama --profile call
MARK benchmark report /pasta/privada/relatorio.json
MARK benchmark performance
```

O runner registra sucesso, fatos verificáveis, tools, qualidade dos vínculos de citação, gate de segurança, latência, tokens informados pelo provider e orçamento por caso. O score combina verificações aplicáveis; falha de segurança zera o caso. Um benchmark pequeno não certifica segurança total, compreensão geral ou qualidade universitária por domínio. A suíte mistura componentes e síntese: 50/50 não significa 50 respostas livres do Qwen.

Baseline real registrado em 07/10/2026: chat 48/50 (duas respostas finais vazias após retry); call 50/50. Os dois são GGUF Q4_K_M, 4.4B reportados pelo Ollama apesar da tag 4b. No primeiro relatório chat, métricas dos casos falhos não foram capturadas; a versão posterior do runner captura last_metrics também em falha. Não estimar seus tokens ausentes. Baselines e limitações estão em `docs/cognition/results` e no relatório final.

Experimentos locais têm IDs EXP-XXXX. A sessão de benchmark usa ModelSession para proteger sessões existentes e liberar o modelo conforme o protocolo ao terminar. Não há treino ou download no runner. As medidas de desempenho usam corpus sintético pequeno, cache local e carga da máquina não controlada.
