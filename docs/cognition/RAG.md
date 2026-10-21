# Recuperação e fontes

A recuperação usa consulta → domínios locais → FTS5/BM25 → candidatos → similaridade vetorial → filtros de metadata → ranking → contexto limitado → modelo. O baseline `mark-hash-lexical`, versão 1, tem 128 dimensões e não usa VRAM. Não é embedding semântico. `EmbeddingProvider` e `KnowledgeBackend` permitem substituir o algoritmo/backend; semantic search com modelo especializado e reranker neural estão PLANNED.

Palavras funcionais comuns de português/inglês são removidas dos termos de busca para não criar citações irrelevantes em conversas casuais. O baseline usa no máximo 500 candidatos FTS. O ranking combina cobertura de tokens, posição BM25 e similaridade de hash; fontes UNVERIFIED recebem menor peso. Filtros incluem domínio, tipo, confiança e histórico. Não se indexa material em toda pergunta; o chat só abre a biblioteca existente em modo read-only. A mudança de modelo/versionamento dos vetores exige reindex, que é transacional.

`Citation` propaga source_id, título, seção/página, data, tipo de fonte, confiança, atualidade, versão, URL, claim_status e source_tier. A resposta final ganha uma lista das fontes efetivamente colocadas no contexto. Isso demonstra quais registros foram consultados, não verifica automaticamente cada frase gerada pelo modelo. A verificação de consistência entre toda afirmação e a fonte é PARTIAL.

O contexto é limitado e fontes são explicitamente conteúdo não confiável, sem autoridade para mudar instruções ou pedir execução. Não há dispatcher de tools em texto recuperado. Citações correspondem somente aos trechos que couberam no contexto. Se a web falhar, a biblioteca local ainda pode ser usada com aviso de falta de informação atual. Banco ausente, incompatível ou corrompido preserva o fluxo anterior.

Freshness: CURRENT, STALE, HISTORICAL ou UNKNOWN. As janelas iniciais são 90 dias para lei e 180 para outros registros datados; sem data confiável, UNKNOWN. Fontes históricas não viram notícia atual. Esta é uma heurística documentada, não validação da vigência de uma lei ou disponibilidade de um produto.

Conflitos estruturados são sinalizados quando metadata.claims possui o mesmo sujeito/propriedade e valores divergentes; use chaves qualificadas como `vehicle_x.combat_weight`. Conflitos em prosa dependem da síntese/revisão; não há extrator de claims automático. TIER_D e claim de fabricante não viram confirmação independente pelo ranking.
