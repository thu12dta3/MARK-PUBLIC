# Estado do MARK

## Navegação web, GitHub e instalações locais — 13/10/2026

Implementado e aplicado ao MARK do PC: leitura Python de links HTTP/HTTPS públicos com resolução de DNS validada, bloqueio de endereços privados, TLS verificado, redirecionamentos e tamanho limitados, e extração textual sem JavaScript. Links GitHub abrem repositórios públicos sem login; repositórios privados dependem da autorização existente em `gh`. Leitura de links de arquivo `blob` e listagem da raiz/README. Conteúdo externo permanece não confiável e não é executado.

Pedidos claros de instalação pelo chat do PC fazem prévia e pedem uma confirmação nova por pacote. Pacman fica restrito a `core`/`extra`/`multilib`, com prévia de dependências e Polkit; Flatpak usa apenas Flathub no escopo do usuário; ferramentas Python podem ser instaladas por PyPI em ambiente isolado do `pipx`, com aviso de execução das rotinas de instalação do pacote. Antes de instalar, MARK salva inventário de pacman, Flatpak e pipx em `~/.local/state/mark/install-backups/`; falha do backup bloqueia a instalação. Não é imagem do disco nem backup de dados pessoais. Mobile/API não instala software no computador.

Backup dos arquivos do runtime anteriores à atualização preservado defensivamente. API reiniciada e respondeu `200` autenticado; janela desktop reiniciada. Ver [docs/SETUP.md](docs/SETUP.md) e [docs/README.md](docs/README.md).

## Etapa 3 — Integrações de Visão e Ferramentas

Implementados o controlador local, cliente MCP com permissões restritas e intents explícitas no fluxo compartilhado de chat/Call. Validação real: ferramentas anunciadas no MCP nativo, consultas estruturadas, processos efêmeros e isolamento em loopback `127.0.0.1`. Relatório e consolidação documentados em [updates.md](updates.md).

## Atualização de Assistência e Cognição

Assistência atualizada: contexto, cálculos determinísticos, fatos estruturados e áudio. A fundação v0.1 abaixo é um snapshot histórico consolidado. Visão atualizada com câmera USB, captura de frames e seleção de foco.

O executor QLoRA foi implementado e adaptadores foram avaliados; ver [docs/cognition/TRAINING.md](docs/cognition/TRAINING.md). Os modelos base locais permanecem ativos como linha de base.

## Snapshot histórico — Cognitive Foundation v0.1

Atualização: 07/10/2026 (America/Sao_Paulo). Fundação implementada; não é um modelo novo treinado. Estados: IMPLEMENTED = código integrado; VERIFIED = verificações executadas; PARTIAL = escopo/validação incompletos; PLANNED = ainda não existe; UNVERIFIED = não foi conferido.

| Componente | Estado | Evidência / limite |
|---|---|---|
| ModelProvider e lifecycle Ollama | VERIFIED | Contratos históricos e testes existentes preservados; modelos atuais usados no baseline |
| Source/Chunk schemas v1 + SQLite | VERIFIED | Validação, vínculo, permissões, dedupe e revisões testados |
| Registro e roteamento multi-domínio | VERIFIED | Heurísticas locais; confiança não calibrada |
| TXT/MD/PDF textual, OCR_REQUIRED | VERIFIED | Ingestão explícita; nenhuma importação de scans inventada |
| FTS5 + vetores hash e reindex | VERIFIED | Corpus sintético medido; hash não é busca semântica |
| Embeddings semânticos/reranker neural | PLANNED | Interface pronta; sem download de modelo novo |
| Citações, temporalidade e conflitos | PARTIAL | Vínculos/datas e conflitos estruturados verificados; fact checking em prosa é incompleto |
| ToolRegistry e autorização adicional | VERIFIED | Read-only, grants, schemas e timeout; nenhum dispatcher arbitrário |
| Chemistry toolbox inicial | VERIFIED | Massa molar integrada; seis calculadoras/lookup iniciais; tabela parcial |
| AFVRecord e enums/claims | VERIFIED | Testes estruturais; não há enciclopédia de blindados importada |
| Dataset loader/validator/dedupe/split | VERIFIED | Esqueleto DRAFT; revisão requerida para split de treino |
| Sanitizer e learning cases | PARTIAL | Heurístico, opt-in; dois candidatos sintéticos PENDING, sem treino automático |
| MARKBench 50 casos | VERIFIED | 42 componentes + 8 casos de síntese pelo modelo |
| Baseline perfil chat | VERIFIED / FAIL | 48/50; duas respostas vazias após retry, issue #1 |
| Baseline perfil call | VERIFIED / PASS | 50/50 na mesma suíte pequena |
| Experimentos EXP-XXXX / telemetria | IMPLEMENTED | Relatórios privados, telemetria opt-in; watts e VRAM peak não medidos |
| LoRA/QLoRA/distillation executores | PLANNED | Config e gates preparados; execução não implementada |
| PC/Android/memória/autorização existente | PARTIAL | Regressão de código executada; dívidas e campanha física anteriores permanecem |

Não declarar SECURITY COMPLETE: a análise contínua de superfícies de ataque permanece documentada em [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md). Nenhum novo APK ou teste físico de câmera/AR foi necessário à fundação do servidor.

## MARK Wear v0.1 — 08/10/2026

Estado PARTIAL. Projeto Kotlin Wear/ponte Mobile, protocolo limitado, UI nativa, sensores sob demanda e armazenamento técnico implementados. Builds e testes automatizados executados; compatibilidade real, instalação, UI, sensores e bateria em refinamento. Detalhes em [docs/wear/README.md](docs/wear/README.md).

## MARK Wear Launcher v0.3 — 08/10/2026

PARTIAL: launcher nível B, apps/sistema, watchface legada, dispatcher de botão auxiliar e correções de lifecycle. Android 9/API 28 comprovado no TicWatch; Home alternativa UNVERIFIED. Sete bugs registrados e tratados. Resultados documentados em [docs/wear/README.md](docs/wear/README.md).

Correção Wear 0.3.1: launcher real agora configurado e verificado para MAIN/HOME; revisão anterior não cumpria a ativação. Botão superior OEM e FSTN aguardam validação humana.

Wear 0.3.3: substituição Home revertida por solicitação do proprietário após regressão do mostrador e lentidão. Home OEM comprovada; MARK auxiliar permanece. UI de chat/home reutiliza Views, Apps recicla linhas. Confirmação humana de botão superior/fluidez solicitada.
