# MARK — Etapa IV: Conhecimento e Educação

Atualização: 13/10/2026. Estado desta implementação: **PARCIAL**. Alterações estão no checkout local de `main`; não houve commit, push, instalação no runtime operacional nem migração da biblioteca particular.

## Auditoria e dependências da Etapa 3

A Etapa 3 continua **em andamento**. Visualizer nativo, painel consolidado de estado e alguns testes E2E de missão permanecem pendentes, conforme [o relatório da Etapa 3](../core/ETAPA3_INTERFACE_VISAO_VISUALIZER.md).

Esses itens não são pré-requisitos técnicos para a Biblioteca nem para o Professor: ambos usam o desktop PyQt6 existente, o mesmo `brain.answer`, a mesma conversa, o mesmo roteamento local e o KnowledgeStore. A integração não inicia o Visualizer/MCP, não muda permissões de missão e não conclui nem altera os gates da Etapa 3.

## Fase 12 — Biblioteca avançada

### Já existia antes desta implementação

- Importação explícita de arquivos e pastas por fila persistente, processamento limitado, recuperação de itens interrompidos e relatório por item.
- Deduplicação por checksum, revisões, coleções, modo de referência ou cópia privada, cancelamento cooperativo, retry, reindexação e remoção dos dados derivados sem apagar o original.
- PDF com texto, Office, EPUB, formatos textuais, OCR e ASR local onde disponíveis; limites de entrada/processamento e isolamento estão detalhados em [Biblioteca](../library/README.md).
- SQLite/FTS5 com ranking lexical, metadados por documento/trecho e baseline de hashing lexical.

### Incrementos desta etapa

- Um título de fonte informado explicitamente no modo “somente minha biblioteca” é resolvido como documento exato, em vez de ser tratado apenas como palavras da consulta.
- Respostas de RAG recebem referências compactas no primeiro parágrafo, derivadas dos registros de recuperação: título e página quando conhecida. O MARK não acrescenta campos UNKNOWN, versões, URLs ou um rodapé bruto de telemetria.
- A instrução ao modelo pede citações inline copiadas dos metadados e proíbe inventar páginas.

### Limites mantidos

- A busca continua lexical FTS5/BM25 e vetores hash; **não é busca semântica por embeddings**. Não foi baixado outro modelo de embedding, pois isso aumentaria uso de RAM/VRAM e não há um embedding Heretic local já avaliado que demonstre ganho. Semântica e reranking permanecem pendentes de benchmark.
- Referências são selecionadas a partir dos trechos enviados ao modelo; a associação exata entre cada afirmação e cada fonte não é verificada automaticamente. A citação compacta significa “material consultado”, não certificação factual.
- Contradições estruturadas em `metadata.claims` são detectadas. Divergências expressas apenas em prosa ainda dependem de síntese do modelo e não têm verificador automático.
- Coleção “enciclopédia offline” e Honcho não foram adicionados: não existe benefício medido que justifique datasets grandes, licença/manutenção ou uma segunda arquitetura de memória.

## Fase 13 — MARK Professor

- O Professor agora oferece oito fluxos no desktop: Explicar, Ensinar por etapas, Quiz, Praticar, Avaliar resposta, Flashcards, Revisar e Plano de estudo.
- Um plano inicia na conversa compartilhada do MARK e usa o mesmo histórico/memória e a recuperação existente; não há segundo cérebro nem roteador de modelos.
- Campo opcional aceita o título exato de um documento. Nesse caso, o prompt solicita apenas a Biblioteca local e a recuperação seleciona esse documento; evidência ausente é declarada como ausente.
- Progresso é armazenado localmente em `~/.local/state/mark/cognition/study.sqlite3`, com permissão de arquivo `0600`. Guarda apenas assunto, objetivo, título da fonte, nível e estado ativo/concluído que o proprietário informou. “Apagar progresso” apaga somente essa tabela e nunca conversas, arquivos ou Biblioteca.
- Não há perfil comportamental nem inferência automática de domínio, nota, erro recorrente ou “fraqueza”. A pessoa pode concluir ou apagar seus planos. Métricas de quiz e domínio dos conceitos continuam pendentes.

## Modelos Heretic

- Os padrões do checkout e `config/config.example.json` agora usam `haervwe/qwen3-vl-4b-heretic:latest` para chat e chamada e `R4C3R/qwen2.5-coder-7b-instruct-heretic:q4_k_m` para Engineer.
- `load_config()` rejeita qualquer perfil local Ollama cujo nome não contenha `heretic`, inclusive o fallback. Isso evita que uma edição futura da configuração privada selecione silenciosamente um modelo base.
- A configuração privada existente foi apenas lida por nomes de modelo; ela já apontava Geral/Call/Engineer para variantes Heretic. Não foi editada. `ollama list` confirmou as duas variantes usadas já presentes localmente; nenhum download foi feito.
- As rotas externas permanecem desligadas no ambiente verificado. Se uma API de terceiro for habilitada depois, o serviço remoto não pode ser garantido Heretic por esta validação de perfis locais. O nome/tag identifica a variante solicitada ao Ollama, mas não é uma assinatura criptográfica dos pesos.
- Nenhuma trava de segurança foi removida ou alterada nesta etapa.

## Dados e privacidade

Os testes usam bancos temporários e textos sintéticos. Nenhum arquivo da Biblioteca particular, banco real de conversas, credencial, imagem ou captura de câmera/microfone foi lido ou enviado. A única nova persistência foi testada em diretório temporário. Não há migração da base de conhecimento nem da conversa.

## Testes e estado de conclusão

Antes da edição, a execução focalizada registrou 223 testes: dois casos de RAG falharam por não exibir as fontes, dois testes de UI falharam e o mesmo teste de EPUB falhou duas vezes porque `test_library_formats` importou `test_library` como módulo top-level. Esse runner também não conseguiu importar o módulo de formatos pela mesma razão. A suíte mediu 500 arquivos sintéticos indexados sem erro em aproximadamente 51,7 s; isso não é uma medição do corpus do proprietário.

Após as alterações, o primeiro conjunto focalizado de estudo, resolução de fonte/citação e configuração Heretic passou: **8 testes**. A regressão ampliada está registrada abaixo. Compilação de módulos e `git diff --check` também passaram.

### Regressão após a implementação

- Suíte focalizada integrada: **273 testes executados; 271 passaram e os mesmos 2 testes de interface já falhavam no baseline** (`assistantBubble` exige estilo inline e o teste exige uma paleta estritamente monocromática). Os 4 novos testes do Professor passaram, assim como RAG/citações, Biblioteca, API móvel, memória, missões e God’s Eye/MCP.
- Testes de extratores reais (OCR/ASR/Office/ZIP): **6 passaram**.
- Testes de missões, provedores e visão desktop: **17 passaram**.
- `compileall` e `git diff --check`: passaram.
- Não houve inferência real de modelo nem medição de watts, VRAM, latência ou consumo do aplicativo; não houve download nem treino.

**Conclusão:** Fase 12 continua parcial por busca semântica real e verificação por afirmação. Fase 13 tem fluxo integrado, sessão compartilhada e progresso local explícito, mas ainda não mede domínio nem avalia quizzes automaticamente. A Etapa IV não está completa.
