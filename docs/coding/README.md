# Especialização em programação — v1

O MARK recebeu disciplina técnica para Python, C, C++, Assembly e Kotlin, com ferramentas reais de verificação nas respostas que contêm código. São usados os Qwen existentes, com a mesma memória compartilhada. Não foi baixado um modelo de chat adicional e não houve treino de pesos nesta etapa.

## Fluxo

1. Detectar o pedido de programação e selecionar instruções específicas de linguagem, padrão, arquitetura, ownership, limites e dependências.
2. Produzir a resposta usando o modelo selecionado e as fontes relevantes/GitHub quando solicitado.
3. Verificar até dois blocos reconhecidos em processo local isolado.
4. Em pedido de geração de código, se houver erro real de compilação, permitir **uma** tentativa de correção pelo mesmo Qwen. Conservar os nomes de funções/classes detectados, requisitos e plataforma; dependências ausentes não geram remoção automática de funções.
5. Aceitar o reparo apenas quando os blocos verificáveis passam; em outros casos conservar a resposta original e informar falha/inconclusão.
6. Mostrar a verificação de compilação/sintaxe, sem afirmar execução ou prova de correção funcional.

Conversas comuns não acionam o compilador. Bibliotecas/documentos não se tornam código executado, skills de ação ou dataset automaticamente. Os controles `coding.enabled`, `coding.verify` e `coding.repair` permitem opt-out. A ação trata a resposta gerada como código não confiável; não executa o programa produzido.

## Ferramentas verificadas nesta máquina

| Linguagem | Verificação | Limite da conclusão |
|---|---|---|
| Python | py_compile no Python instalado | Sintaxe; imports/algoritmo/runtime não testados |
| C | GCC, ISO C17, avisos, syntax-only | Sem execução/sanitizers |
| C++ | G++, C++20, avisos, syntax-only | Sem prova de ownership/UB/thread safety |
| Kotlin | Compiler embeddable 2.4 já em cache, JVM/JDK17 | Código JVM; projetos Android exigem SDK/Gradle/dependências |
| Assembly x86_64 | GNU as --64 | Sintaxe AT&T ou Intel com .intel_syntax; NASM não é fingido como instalado |
| Assembly AArch64 | Clang --target=aarch64-linux-gnu, objeto | Cross-assembly; não executado no PC x86_64 |

O launcher kotlinc instalado era incompatível com o Java 27 padrão (`-noverify`). A solução usa o compiler embeddable já presente nos caches do projeto com JDK17 e arquivos específicos somente leitura; não muda o Java global e não baixa outro compilador. Os hashes SHA-1 dos JARs são conferidos contra seus diretórios do cache. Não é auditoria integral de cadeia de suprimentos.

## Isolamento

Bubblewrap sem rede, HOME/credenciais do proprietário, câmera/microfone/GPU, com /usr somente leitura e workspace temporário privado. Kotlin recebe só JARs selecionados e o arquivo público de configuração de segurança do JDK; nunca todo o .gradle/HOME. Orçamentos: código até 64 KB, até dois blocos, compilação até 30 s/25 s CPU, memória virtual 2 GiB, saída de arquivo 2 MiB e diagnóstico limitado. Falta de sandbox/compilador retorna UNAVAILABLE, sem executar código no host como fallback.

**Compilar não prova correção funcional ou segurança.** O campo executed=false se refere ao programa enviado, não à ferramenta de compilação. Não são prometidas especialização absoluta, ausência de bugs nem validade para qualquer SDK/arquitetura. A capacidade do modelo permanece limitada; o ganho implementado é disciplina técnica, feedback real e correção limitada.

## Evidências

Fixtures autorais de Python, C17, C++20, Kotlin/JVM, Assembly x86_64 e AArch64: **6/6 verificações reais passaram**. Kotlin ~2,57 s, C++ ~0,21 s, demais ~0,01–0,07 s no ensaio isolado. Testes de sintaxe inválida, ausência de execução do módulo, inacessibilidade de arquivo privado, falta de sandbox, limites, aplicação ao cérebro e reparo limitado também passaram. Não confundir esses escores com pontuação do Qwen: os casos são compilados de fixtures autorais, não uma avaliação cega da inteligência do modelo.

Nenhum código/conversa/documento privado foi publicado. Exemplos e fontes do módulo são locais e versionados; datasets de fine-tuning continuam sujeitos à revisão humana separada. Auditoria adversarial completa não foi demonstrada: SECURITY INCOMPLETE.

Regressão integrada antes da instalação desta revisão: **446 testes passaram em 76,695 s**. A avaliação de qualidade do Qwen em tarefas novas permanece separada das verificações dos compiladores.

## Instalação e teste real

O proprietário aprovou a revisão 809c2c1; ela foi instalada no PC com backup verificado de 28 arquivos/ativos. O fluxo da API usando o Qwen existente gerou uma função Python `soma(a, b)` e retornou o rodapé de sintaxe verificada em 5,45 s. O teste usou memória e banco temporários, sem documentos privados e sem alterar a conversa compartilhada. Isso comprova a integração em um caso simples; não comprova domínio absoluto das cinco linguagens.

## Como solicitar

Exemplos: “Escreva uma função Python com validação de entrada”, “Implemente uma fila em C17”, “Analise o lifetime deste código C++20”, “Escreva Assembly AArch64 para esta função” e “Implemente um Flow em Kotlin”. Informe plataforma, versões, bibliotecas e requisitos quando forem relevantes. Blocos de código reconhecidos acionam a verificação quando o pedido é de programação; explicações conceituais não precisam de compilador.

Além de Python, o mesmo fluxo real gerou e verificou exemplos simples de C17 (~5,09 s), C++20 (~0,43 s), Kotlin/JVM (~3,23 s) e Assembly x86_64 (~6,91 s). As cinco respostas continham código reconhecido e rodapé de compilação/sintaxe aprovada. O estado da memória era temporário; as funções geradas não foram executadas. Os exemplos simples demonstram integração, não um benchmark abrangente de programação. Assembly AArch64 foi verificado com fixture autoral pelo cross-assembler, sem afirmar teste do Qwen nessa arquitetura.

Regressão final após a correção complementar da biblioteca: **450 testes passaram em 81,160 s**, com PYTHONPATH=src e Qt offscreen. A verificação dos 28 caminhos instalados combinando os dois manifests também passou.
