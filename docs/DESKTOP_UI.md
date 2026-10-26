# Interface Desktop do MARK (Widescreen 16:9 — Soft Minimal Tech)

A interface para computador do MARK é desenvolvida em **PyQt6**, projetada sob a filosofia *Soft Minimal Tech* (Minimalismo Executivo e Funcional), combinando máxima ergonomia visual, tipografia refinada e simetria horizontal em monitores widescreen 16:9 (`1920x1080`).

---

## 1. Princípios de Design e Tokens Visuais

O sistema visual é padronizado pelo gerenciador central `mark.ui.theme_manager.ThemeManager` através da classe `ThemeTokens`, utilizando uma paleta de carvão fosco anti-fadiga com acentos ciano luminosos:

| Token | Valor Hex | Finalidade e Aplicação |
| :--- | :--- | :--- |
| `BG_CANVAS` | `#121417` | Fundo principal da aplicação e área de leitura do assistente. |
| `BG_SIDEBAR` | `#0D0F11` | Barra lateral fixa (tom escuro de ancoragem). |
| `BG_PANEL` | `#16191D` | Elementos elevados, campos de busca, menus contextuais e modais. |
| `BG_SURFACE` | `#1A1D22` | Superfície de elevação intermediária. |
| `BG_USER_BUBBLE` | `#20252C` | Bolha de mensagem do usuário, sem bordas duras (`border-radius: 8px`). |
| `BG_FOCUS` | `#262B33` | Estados de hover e foco em botões e seletores. |
| `TEXT_PRIMARY` | `#EDEDED` | Tipografia principal (branco suave, alto contraste e sem reflexo). |
| `TEXT_SECONDARY` | `#8E95A0` | Metadados, subtítulos, carimbos de tempo e dicas. |
| `TEXT_MUTED` | `#5F6672` | Separadores e rótulos de seções desabilitadas. |
| `ACCENT_CYAN` | `#00D8F6` | Acento principal de destaque, botões primários e links ativos. |
| `ACCENT_GREEN` | `#3FB950` | Indicador de integridade (BWRAP ativo, voz ativa, serviço online). |
| `BORDER_SUBTLE` | `#1E2228` | Bordas ultrafinas de separação estrutural. |

### Tipografia Híbrida Estrita
- **Fonte Primária:** `Inter`, `-apple-system`, `BlinkMacSystemFont`, `'Segoe UI'`, `Roboto`, sans-serif.
- **Pesos Permitidos:** Rigorosamente limitados a `400` (Regular) e `500` (Medium) em todos os componentes para evitar pesos excessivos ou fadiga visual.
- **Fonte Monoespaçada:** `'JetBrains Mono'`, `'Fira Code'`, `'Cascadia Code'`, monospace para blocos de código, identificadores técnicos e telemetria.

---

## 2. Layout Widescreen 16:9 e Eixo Central Simétrico

Em monitores convencionais ou ultrawide, a leitura em largura total de tela causa cansaço visual. O MARK resolve isso através de um contêiner central com restrição simétrica:

```
+----------------+---------------------------------------------------------------+
|  BARRA LATERAL |                        ÁREA DE CONTEÚDO                       |
|   FIXA (248px) |  [Stretch]       COLUNA CENTRAL (920px)           [Stretch]   |
|                |             +---------------------------------+               |
|  - Logo MARK   |             | Top Header (Título & Ações)     |               |
|  - Nova Conv.  |             |---------------------------------|               |
|  - Filtro      |             | Chat Scroll (Histórico Mensagens|               |
|  - Modos/Agente|             |  - User Bubble (#20252C, 8px)   |               |
|  - Ferramentas |             |  - Assistant (Direto no fundo)  |               |
|  - Histórico   |             |---------------------------------|               |
|  - Configs     |             | Composer em Pílula (Raio 22px)  |               |
|  - Status      |             | Telemetria (BWRAP, mTLS, CTX)   |               |
|                |             +---------------------------------+               |
+----------------+---------------------------------------------------------------+
```

1. **Eixo Central de 920px (`#chatColumnContainer`):**
   - Largura mínima de 680px e máxima estrita de 920px.
   - Centralizado via expansores flexíveis (`addStretch(1)`) nas extremidades esquerda e direita da área de conteúdo.
   - Alinhamento perfeitamente simétrico verificado em testes de regressão visual.
2. **Barra Lateral Fixa (248px):**
   - Não colapsa involuntariamente, fornecendo acesso constante às sessões de trabalho.
   - Seletor rápido de modos (`Geral`, `Engineer`, `Professor`).
   - Gestão de sessões independentes no modo Engenheiro (criação via `+ Nova conversa`, renomeação e exclusão).
   - Busca/filtro instantâneo de conversas recentes.

---

## 3. Experiência de Chat e Renderização de Conteúdo

### Formatação de Mensagens e Código
- **Mensagens do Usuário:** Bolha compacta à direita em `#20252C` com cantos arredondados (8px) e tipografia Inter 13px (400).
- **Respostas do Assistente:** Repousam diretamente sobre o canvas `#121417` sem bordas ou caixas intrusivas, priorizando foco na leitura.
- **Blocos de Código Markdown:**
  - Cabeçalho superior com identificação da linguagem em `Inter` 11px / 500.
  - Botão de ação rápida **Copiar** vinculado ao protocolo de URI interna `mark-copy://`.
  - Área de código com fundo escuro `#0B0D0F`, bordas sutis `#1E2228` e tipografia `JetBrains Mono` 12px.
- **Citações da Biblioteca (RAG):** Badges clicáveis no formato `[DocID:Trecho]` com fundo `#00D8F6` translúcido, acionando o visualizador de fontes verificadas.

### Caixa de Entrada (Pill Composer)
- **Design em Cápsula:** Bordas suaves arredondadas (`border-radius: 20px / 22px`), fundo `#16191D` e borda sutil `#1E2228`.
- **Botão Anexar:** Suporte a arrastar e soltar (drag-and-drop) de múltiplos arquivos (imagens, documentos e código).
- **Seletores de Modelo e Raciocínio:** Alternância dinâmica entre modelos locais (`mark-local`, `ollama`) e remotos autorizados, com indicador de profundidade de raciocínio.
- **Botão de Envio Cápsula:** Botão circular/pílula em ciano `#00D8F6` com ícone vetorial de envio e contraste ótimo `#121417`.
- **Dica de Envio:** Instrução `"Enter envia · Shift+Enter nova linha"` posicionada em linha inferior sem truncamento de texto.

---

## 4. Telemetria Técnica e Diálogos de Segurança

A barra inferior exibe o status de segurança e contexto do host:
- **● BWRAP:** Indica o status do confinamento em sandbox de namespaces do kernel Linux (PID, IPC, UTS, Mount). Clicável para auditoria técnica.
- **● mTLS:** Status da malha de transporte criptografada via MARK Link / Tailscale.
- **CTX:** Janela de contexto ativa do modelo (ex: 8K / 32K tokens).

Ao clicar sobre qualquer badge, o método `show_technical_details()` exibe detalhes arquiteturais sobre isolamento de processos, criptografia de tráfego e limites de memória sem expor dados confidenciais do usuário.
