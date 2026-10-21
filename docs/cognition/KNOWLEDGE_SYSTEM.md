# Biblioteca de conhecimento

`SourceRecord` e `ChunkRecord` têm `schema_version=1`, validação em tempo de execução e IDs estáveis. Fonte inclui título, domínio, autor, publicador, tipo, URL/caminho, idioma, datas, licença, confiança, checksum SHA-256, metadata e versão. Chunk inclui vínculo à fonte, domínio, texto, seção, página e identidade/versionamento do embedding. SQLite impõe vínculo por foreign key; o banco também usa user_version=1 e rejeita versões desconhecidas.

Ingestão é uma operação explícita do proprietário no CLI. TXT/MD UTF-8 e PDF com texto são aceitos; um scan vazio recebe OCR_REQUIRED. Não há OCR, crawler ou conversão automática de documentos pessoais. Limites: 8 MiB de arquivo, 2 milhões de caracteres, chunks de até 1400 caracteres com sobreposição de 120 e até 5000 chunks por documento. Poppler opera com timeout e, quando disponível, prlimit. A extração trabalha sobre snapshot do arquivo; symlink, pipe e documento inválido são rejeitados.

É obrigatória a declaração de licença/proveniência. Valores reconhecidos incluem USER_OWNED, PUBLIC_DOMAIN, CC0-1.0, CC-BY-4.0, CC-BY-SA-4.0, MIT, Apache-2.0, EXPLICIT_PERMISSION e REFERENCE_ONLY_PERMISSION. O CLI não determina juridicamente se o operador possui os direitos: a declaração deve ser verdadeira. Material de referência não se transforma em treino.

Dedupe combina checksum e metadata. Título/domínio/URL ou metadata.document_key agrupam revisões: alterar conteúdo gera nova versão; buscas normais selecionam a versão ativa; --include-history permite consultar anteriores. Não use o mesmo document_key para entidades distintas. Dados atuais não sobrescrevem o histórico.

Metadata legal exige country, jurisdiction, authority, document, article, publication_date, effective_date, version_date, source_url e source_type. Datas desconhecidas são null; demais campos desconhecidos devem ser explícitos. A atualidade usa data de versão/verificação, não a data em que um arquivo antigo foi recém-importado. CURRENT significa recente sob a janela heurística, não prova de vigência legal. Conferir effective_date e fontes oficiais continua obrigatório.

Para automóveis, metadata pode descrever fabricante, modelo, ano, motor, transmissão, mercado e versão. Para história, registrar período, região, evento, pessoa, autor, tipo de fonte e confiabilidade. Registros AFV possuem campos completos de identificação, componentes, variantes, linhagens, fontes e claims. UNKNOWN/null é preferível a inventar informação.

Exemplo local:

```bash
MARK knowledge ingest /caminho/manual.md --domain computing --title 'Manual autorizado' --license USER_OWNED
MARK knowledge search 'diagnóstico Linux' --domain computing
MARK knowledge stats
MARK knowledge reindex
```

`--metadata arquivo.json`, `--source-type`, `--url` e `--publication-date` completam a proveniência. `--db` vem antes do subcomando e permite um laboratório isolado. O banco é 0600, em pasta privada, e não é publicado no Git.
