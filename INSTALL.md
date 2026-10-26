# MARK — Guia de Instalação Universal Linux

Este guia detalha como instalar o **MARK** como um aplicativo nativo completo em qualquer distribuição Linux moderna (Ubuntu, Debian, Fedora, Arch Linux, Manjaro, openSUSE, Linux Mint, Pop!_OS, etc.).

O instalador configura automaticamente o ambiente isolado, ícones em alta resolução, atalho no menu de aplicativos, atalho na Área de Trabalho e integração total com a Barra de Tarefas/Dock.

---

## ⚡ Instalação Rápida (Recomendada)

Não requer privilégios de administrador (`root` ou `sudo`).

```bash
# 1. Baixe e extraia o pacote tarball
tar -xzf mark-linux-x86_64.tar.gz

# 2. Acesse a pasta extraída
cd mark-linux-x86_64

# 3. Execute o instalador universal
./install.sh
```

O instalador realizará todo o processo em segundos:
- [x] Verificação do interpretador Python (3.9+)
- [x] Configuração de ambiente virtual isolado (`venv`) para conformidade com distros modernas (PEP 668)
- [x] Instalação de ícones em 8 resoluções (16px a 512px) e SVG no tema do sistema
- [x] Registro do lançador `.desktop` no menu de aplicativos
- [x] Criação de atalho funcional na Área de Trabalho (`~/Desktop` ou `~/Área de trabalho`)
- [x] Atualização de caches do sistema (`update-desktop-database` e `gtk-update-icon-cache`)

---

## 🖥️ Integração com Barra de Tarefas e Dock

O MARK foi construído com suporte total à especificação FreeDesktop e **`StartupWMClass=MARK`**, garantindo que a janela em execução se vincule exatamente ao ícone fixado, sem gerar ícones cinzas ou duplicados.

### Como Fixar:
* **GNOME Shell (Ubuntu, Fedora, Debian, Pop!_OS):**
  1. Pressione a tecla `Super` (Windows) para abrir a visão de atividades.
  2. Digite **MARK**.
  3. Clique com o botão direito sobre o ícone do MARK e selecione **Adicionar aos Favoritos**.
* **KDE Plasma (Kubuntu, Fedora KDE, openSUSE, Arch):**
  1. Abra o menu de aplicativos (Kickoff).
  2. Localize **MARK** na categoria *Utilitários* ou *Desenvolvimento*.
  3. Clique com o botão direito e selecione **Fixar no Gerenciador de Tarefas**.
* **XFCE / Cinnamon / MATE:**
  1. Abra o menu Iniciar.
  2. Clique com o botão direito no MARK e escolha **Adicionar ao Painel**.

---

## 📂 Atalho na Área de Trabalho

Se você usa Área de Trabalho ativa, o instalador já deposita o atalho `MARK` pronto para uso.

> **Nota para usuários do GNOME Shell / Nautilus:**  
> O instalador marca automaticamente o arquivo como confiável (`gio set ... metadata::trusted true`). Caso sua versão do gerenciador de arquivos solicite, clique com o botão direito no ícone da Área de Trabalho e escolha **"Permitir Execução"** (*Allow Launching*).

---

## 💻 Uso via Linha de Comando (Terminal)

O instalador registra atalhos nos binários locais:

```bash
# Iniciar a Interface Gráfica Executiva
mark-desktop

# Iniciar o Assistente em modo Terminal / CLI
mark
```

> **Dica de PATH:**  
> Se o comando `mark-desktop` não for encontrado no terminal, certifique-se de que `~/.local/bin` está no seu `$PATH`. Para adicioná-lo:
> ```bash
> echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
> source ~/.bashrc
> ```

---

## 🌐 Instalação para Todo o Sistema (Multi-usuário)

Caso deseje disponibilizar o MARK para todos os usuários de um computador ou servidor compartilhado:

```bash
sudo ./install.sh --system
```

* Os arquivos serão instalados em `/opt/mark`
* Os comandos ficarão disponíveis globalmente em `/usr/local/bin`
* O lançador de menu será registrado em `/usr/share/applications`

---

## ⚙️ Opções do Instalador

| Opção | Descrição |
| :--- | :--- |
| `./install.sh` | Instalação padrão no perfil do usuário (`~/.local/share/mark-app`) |
| `./install.sh --system` | Instalação em nível de sistema (`/opt/mark`, requer `sudo`) |
| `./install.sh --no-desktop` | Instala no menu e terminal, sem criar atalho na Área de Trabalho |
| `./install.sh --no-venv` | Pula a criação do venv (usa os pacotes do Python global) |
| `./install.sh --help` | Exibe resumo de todas as opções de linha de comando |

---

## 🗑️ Desinstalação Limpa

Para remover o MARK completamente do sistema:

```bash
# Se instalado no modo usuário (padrão):
~/.local/share/mark-app/uninstall.sh

# Se instalado no modo sistema (global):
sudo /opt/mark/uninstall.sh
```

Para remover também todos os arquivos de configuração, cofre de chaves e histórico local:
```bash
~/.local/share/mark-app/uninstall.sh --purge
```

---

## 🧩 Pré-requisitos do Sistema

* **Distribuição:** Qualquer distribuição Linux x86_64 recente (Kernel 5.4+).
* **Python:** 3.9, 3.10, 3.11, 3.12, 3.13 ou 3.14.
* **Bibliotecas Gráficas:** Suporte nativo a **X11** e **Wayland** (fornecido pelas bibliotecas padrão do sistema e PyQt6).
* Em sistemas Ubuntu/Debian muito enxutos (*minimal install*), pode ser necessário garantir os utilitários básicos do Python:
  ```bash
  sudo apt install python3-pip python3-venv
  ```
