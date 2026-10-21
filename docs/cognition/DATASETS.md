# Dados de treinamento e revisão

`DatasetSample`, versão 1, contém sample_id, dataset_version, domain, difficulty, user_input, context, available_tools, expected_plan, expected_tool_calls, expected_answer, constraints, failure_modes, source e review_status. O loader aceita JSONL limitado; campos de chain-of-thought/raciocínio oculto são recusados. Planos explícitos, chamadas de tools e resposta final são materiais auditáveis.

O esqueleto em `training/skeleton.jsonl` contém poucos exemplos autorais DRAFT. Não está aprovado para treinamento. A validação geral verifica schema, licença/proveniência, privacidade heurística, IDs e conteúdo duplicado. O splitter exige todos os exemplos APPROVED e no mínimo três entradas, então cria train/validation/test determinísticos e verifica overlap de IDs e entradas normalizadas. O split não mede similaridade semântica nem prova ausência de contaminação de um modelo-base.

```bash
MARK dataset validate /caminho/dados.jsonl
MARK dataset validate /caminho/dados.jsonl --require-approved --output /pasta/privada/review.json
MARK dataset dedupe /caminho/dados.jsonl --output /pasta/privada/dedup.jsonl
MARK dataset split /caminho/dados-revisados.jsonl --output-dir /pasta/privada/splits
```

Dedupe é explícito e reporta entradas descartadas. Modificar review_status exige revisão humana do conteúdo; não há aprovação automática pelo modelo. Material REFERENCE_ONLY_PERMISSION pode estar no RAG, mas é negado para treino. A licença declarada deve refletir os direitos reais do operador.

`MARK learning sanitize entrada.json --output /pasta/privada/candidato.json` cria candidato PENDING e training_eligible=false. Não lê SQLite de conversa, anexos ou credenciais automaticamente. Sanitizer cobre marcadores de senha/token, Bearer, PEM, emails, padrões de identificadores, coordenadas e chaves conhecidas em objetos. É heurístico: revisão obrigatória e nenhum compromisso de anonimização completa. Dados de saúde e documentos pessoais não são uma fonte automática de treino.

## Currículo autoral v0.2 · 11/10/2026

O proprietário aprovou explicitamente os 72 exemplos após receber o REVIEW.md local. Candidato SHA-256 `c5e2c9bfd65e82854482e7b3ef3c0c2570301a6d337dd1b52c2ff3a7f5cf3df7`; arquivo aprovado SHA-256 `917151a27ca5a05b1b03b3a1ac44b39ff123d94b7e7da0c6b2eb7846a02096fb`. A mudança de status preservou os conteúdos revisados.

O splitter do executor v0.2 separa 36 famílias, além de IDs e entradas: 56 treino /11 validação /5 teste. Validação: rollback, molar-mass, temperature. Teste: attachments-limits, attachments, density. JSONL e aprovação ficam fora do Git; o gerador reproduz candidatos PENDING, exigindo nova aprovação antes de outra rodada. Novos exemplos ou alteração de conteúdo exigem revisão novamente.
