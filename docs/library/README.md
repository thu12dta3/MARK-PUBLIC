# Biblioteca de Memória v1.0

![Biblioteca Qt com documentos sintéticos de demonstração](preview.png)

## Uso no PC

Abra **Biblioteca de Memória** na barra lateral. A tela principal mostra busca, documentos e Adicionar. O menu **⋯** reúne coleções, opções de importação e espaço usado; **Importações** expande a fila e seus controles. Clique com o botão direito num documento para gerenciá-lo; duplo clique mostra metadados. Arraste arquivos ou uma pasta para a área destacada; também é possível selecionar centenas de documentos pelo botão Adicionar arquivos. Escolha a coleção no filtro e o modo em **⋯ → Opções de importação** antes de adicionar.

- **Referenciar original** é o padrão: armazena índice e metadados, preserva a origem, sem copiar os livros.
- **Importar cópia privada** mantém uma cópia deduplicada por SHA-256. O espaço adicional aparece no painel. Remover do índice preserva essa cópia; sua exclusão física exige uma ação explícita separada no backend.
- Coleções podem ser criadas, habilitadas ou desabilitadas. O botão Ativar/desativar documento controla a consulta pelo modelo.
- Pausar termina o item atual e impede novos. Cancelar interrompe cooperativamente a extração selecionada; sem seleção, pede confirmação para cancelar toda a fila. Reprocessar erros recoloca erros/cancelados na fila.
- Fechar o PC/app interrompe a extração e preserva pendências. Ao reabrir a biblioteca, o lock exclusivo recupera os itens interrompidos. A biblioteca continua processando ao voltar para a conversa, enquanto o app permanecer aberto.
- A lista de documentos tem paginação em grupos de 500; a fila exibe os 500 registros mais recentes. Coleções e busca por título/conteúdo percorrem a biblioteca inteira.
- Um worker é o padrão medido no PC. Pode selecionar dois: no máximo duas extrações isoladas, nunca centenas de conversores.

Busque título ou conteúdo. Abra o original, confira metadados/páginas, reindexe um documento alterado ou use **Localizar origem** para apontar um arquivo movido: o checksum deve ser igual. Apagar do índice remove fontes e chunks de todas as revisões desse documento, sem apagar o original. Outras coleções permanecem independentes.

## Formatos e limites

Qualquer arquivo regular selecionado é aceito como texto indexado, texto parcial ou referência sem texto. A seção **Ampliação solicitada** abaixo descreve os extratores reais e a cobertura; aceitar um arquivo não significa decodificar completamente seu conteúdo.

PDF textual, DOCX, TXT, Markdown, HTML, CSV, JSON, YAML, XML e EPUB são lidos localmente. Office, OCR de imagens/PDF e transcrição offline foram acrescentados. YAML/código são dados: construtores, scripts e macros não são executados. Página desconhecida continua “—”; slides não são páginas impressas.

Limites: entrada 64 MiB, texto até 2 milhões de caracteres e 5000 chunks. Extração normal: 512 MiB de memória virtual, 30 s CPU e 36 s externos; OCR/mídia: 2 GiB, 150 s CPU e 180 s externos. ZIP: até 2000 entradas e 32 MiB expandidos, sem traversal/criptografia/taxas excessivas. XML com entidades/DOCTYPE é bloqueado. Conteúdo ilegível/protegido/corrompido pode virar referência com aviso; falhas de IO, tamanho ou isolamento continuam erros reais.

## Consulta nas conversas

O cérebro compartilhado do PC usa o mesmo KnowledgeStore para PC/celular/API já autenticada. As permissões remotas existentes continuam valendo; nenhum endpoint público de upload de biblioteca foi criado.

Exemplos:

- “Pesquise nos meus documentos sobre capacitores.”
- “Use somente minha biblioteca: capacitores.”
- “Procure na coleção de Química.” (use o nome exato da coleção)
- “Resuma o capítulo 4 do livro \"Manual de circuitos\".”
- “Compare \"Manual de circuitos\" e \"Guia de energia\".”

A busca é lexical FTS5 com baseline de hashing local: **não** é embedding semântico. O domínio heurístico não exclui documentos da biblioteca; coleções e documentos desabilitados são filtrados **antes** do limite de candidatos FTS. Somente trechos relevantes entram no contexto (até 10 mil caracteres serializados); referências incluem título, página quando existente, seção e versão. Ausência de resultados é explicitada. “Somente minha biblioteca” evita pesquisa web e ferramentas e não inventa citações quando não encontra material.

Títulos entre aspas permitem selecionar dois livros ou um capítulo numerado. Capítulos precisam ter cabeçalho textual reconhecível; não se inventa a seção. Comparação ampla de livros ou resumo de um capítulo inteiro ainda depende da seleção dos trechos recuperados: o sistema não promete leitura integral nem cobertura de todas as páginas. As referências do rodapé são fontes recuperadas; não provam que cada frase gerada pelo Qwen está correta. Essa limitação também existia no núcleo anterior.

## Separação e privacidade

- Conversas: banco compartilhado existente, sem alteração de schema.
- Conhecimento: `knowledge.sqlite3`, fontes/chunks/FTS existentes + tabelas library_*.
- Fila: `library/imports.sqlite3`, separada, persistente.
- Cópias gerenciadas: `library/files/`, privadas, por checksum.
- Treinamento: separado. Importar **não altera pesos**, não cria datasets e não inicia treino.

Nenhum documento pessoal entra no GitHub ou em API externa. Não existe varredura automática do HOME. A seleção de uma pasta autoriza apenas aquela árvore; symlinks, pipes e caminhos com `..` são rejeitados. Conteúdo é dado não confiável, não instrução operacional. Erros persistem códigos, sem trechos do documento. Caminhos/títulos são metadados privados locais necessários à gestão, não publicados.

Sandbox Bubblewrap sem rede, HOME, credenciais ou dispositivos do proprietário; /usr é somente leitura e apenas o snapshot selecionado é montado. Prlimit impõe recursos. Se o isolamento falhar, não há conversão sem sandbox. Isso reduz a exposição, não prova ausência de vulnerabilidades no kernel/parser: **SECURITY INCOMPLETE**.

## Linha de comando / recuperação

Comandos explícitos, sem microfone nem modelo:

```bash
MARK knowledge library add /pasta/autorizada --collection Eletrônica --mode reference
MARK knowledge library status
MARK knowledge library work
MARK knowledge library pause
MARK knowledge library resume
MARK knowledge library retry
```

`MARK knowledge --db /caminho/knowledge.sqlite3 library ...` permite laboratório separado. `work` é finito e sequencial; o app oferece 1/2 workers. O lock impede dois gerenciadores de recuperar a mesma fila.

Antes da primeira migração aditiva, SQLite online backup cria `library/knowledge-before-library.sqlite3` privado. Schema dos registros SourceRecord/ChunkRecord permanece 1; versão da extensão library_schema é independente. Banco desconhecido/corrompido não é zerado automaticamente.

Para recuperação, feche a biblioteca/app e qualquer `library work`, faça backup privado com a API SQLite backup (não copie só o .sqlite3 quando houver WAL ativo), e execute `PRAGMA integrity_check`. Preserve banco, fila e cópias antes de qualquer restauração. Restaure o backup para um **novo** caminho e confira fontes/chunks com o CLI `--db`; somente depois substitua o banco com autorização. Nunca substitua o banco de conversas. Se a fila permaneceu íntegra, reabra a biblioteca para recuperar RUNNING; use Retry para erros. Reindexar embeddings (`MARK knowledge reindex`) não recupera chunks apagados: reimporte as origens.

Remoções liberam páginas internas reutilizáveis; o arquivo SQLite pode não diminuir imediatamente. VACUUM é manutenção offline explícita, após backup e conferência de espaço; não é executado automaticamente. Backups e cópias gerenciadas não são purgados automaticamente.

## Ampliação solicitada: todos os formatos do lote

Qualquer arquivo regular selecionado pode ser adicionado. O painel diferencia:

- **Texto indexado**: documento com conteúdo extraído.
- **Texto parcial**: strings estáticas de binários/legados, entradas textuais de ZIP, RTF, OCR/cobertura limitada ou transcrição parcial.
- **Referência · sem texto**: metadados e original disponíveis, sem inferir conteúdo. Arquivo desconhecido/corrompido ou protegido pode receber essa modalidade, com o motivo nos detalhes da fila.

Isso substitui a classificação inicial de formato não suportado quando o usuário pediu aceitação universal; não significa decodificação completa de todo formato proprietário. Programas EXE/DLL/OBJ/arquivos de máquina nunca são executados. Objetos/arquivos técnicos recebem strings estáticas quando há texto legível; senão, referência. Código-fonte é lido como texto, sem scripts/macros.

Implementados e testados: PPTX (páginas são slides), XLSX (células e valores registrados, fórmulas não recalculadas), ODT/ODS/ODP textual, RTF parcial, fontes de C/C++/Python/Kotlin/Assembly e outros textos. ZIP tem leitura de entradas textuais limitada, sem extrair caminhos no host; traversal/ZIP excessivo gera referência com aviso.

Imagens usam OCR local. PDF sem camada textual pode receber OCR de até 5 páginas por execução, com coverage/total_pages; livros maiores ficam explícitos como parciais. Áudio/vídeo usa FFprobe/FFmpeg e Whisper base **existente**, offline e sem GPU, com até 60 s por arquivo; a cobertura parcial é preservada. Vídeo sem faixa de áudio e imagens sem texto recebem referência. Não há descrição visual geral prometida para toda foto.

Os dados de OCR português/inglês foram obtidos dos pacotes oficiais extra do Arch, com SHA-256 conferido contra a base local do pacman. A instalação global solicitou sudo; os recursos ficaram privados no usuário, sem mudar pacotes do sistema. Whisper/Qwen existentes foram reutilizados. Nenhum documento foi enviado para OCR/transcrição externos. Formatos com OCR/mídia têm limite isolado maior: 2 GiB de memória virtual, 150 s CPU e 180 s de tempo; texto normal mantém 512 MiB/30 s CPU/36 s. O teto de entrada continua 64 MiB; limite de tamanho/IO não é confundido com formato não suportado.

Antes de reclassificar a fila antiga, salva-se `imports-before-classifier-v3.sqlite3`. Os antigos formatos ignorados/PDFs que exigiam OCR são recolocados para tentativa usando as novas capacidades; os originais são preservados. A referência não é fingida como texto completo e não autoriza treinamento.
