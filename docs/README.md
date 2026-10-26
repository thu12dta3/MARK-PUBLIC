# MARK — Índice da Documentação Técnica

Bem-vindo à documentação técnica oficial do **MARK (Modular Augmented Reasoning & Knowledge Assistant)**. Este índice organiza todos os manuais, especificações e referências arquiteturais disponíveis no repositório.

---

## 🏛️ 1. Arquitetura e Engenharia do Sistema

| Documento | Descrição |
| :--- | :--- |
| [**ARCHITECTURE.md**](ARCHITECTURE.md) | Visão aprofundada da arquitetura do MARK, fluxo de inferência e subsistemas. |
| [**SETUP.md**](SETUP.md) | Guia de preparação de ambiente, dependências e compilação do núcleo Rust. |
| [**DESKTOP_UI.md**](DESKTOP_UI.md) | Especificação visual do redesign 16:9, tokens de tema carvão e simetria de canvas. |
| [**coding/ENGINEER_MODEL.md**](coding/ENGINEER_MODEL.md) | Diretrizes e perfil cognitivo do modo Engenheiro para tarefas de código e refatoração. |
| [**FREE_AI.md**](FREE_AI.md) | Guia de configuração de modelos locais (Ollama / MarkLocalProvider) com custo zero e privacidade total. |
| [**PLUGINS.md**](PLUGINS.md) | Arquitetura de ferramentas externas, MCP e extensões cognitivas. |

---

## 🛡️ 2. Segurança, Governança e Resiliência

| Documento | Descrição |
| :--- | :--- |
| [**THREAT_MODEL.md**](THREAT_MODEL.md) | Modelo formal de ameaças, superfícies de ataque (rede, IPC, mobile) e defesas ativas. |
| [**PERMISSIONS_MATRIX.md**](PERMISSIONS_MATRIX.md) | Matriz rigorosa de capacidades e controle de acesso a ferramentas (Zero-Trust). |
| [**SECURITY_REQUIREMENTS.md**](SECURITY_REQUIREMENTS.md) | Requisitos de isolamento via Bubblewrap (`bwrap`), sanitização de tokens e mTLS. |
| [**SEGURANCA_APP.md**](SEGURANCA_APP.md) | Práticas de segurança aplicadas ao aplicativo desktop e clientes móveis. |
| [**INCIDENT_RESPONSE.md**](INCIDENT_RESPONSE.md) | Plano de resposta rápida, isolamento de falhas e contenção de vazamentos. |
| [**RECOVERY_PROCEDURES.md**](RECOVERY_PROCEDURES.md) | Procedimentos estruturados de recuperação após interrupções ou falhas operacionais. |
| [**UPDATE_POLICY.md**](UPDATE_POLICY.md) | Política de atualização com validação de hashes SHA-256 e proteção anti-downgrade. |

---

## 📱 3. Clientes Móveis e Ecossistema

| Documento | Descrição |
| :--- | :--- |
| [**MOBILE.md**](MOBILE.md) | Arquitetura do aplicativo Android, vinculação de chaves ao Keystore e pareamento QR. |
| [**wear/README.md**](wear/README.md) | Índice da documentação para smartwatches e dispositivos vestíveis (Wear OS). |
| [**companion/README.md**](companion/README.md) | Arquitetura do aplicativo complementar e comunicação via malha MARK Link. |

---

## 📦 4. Instalação e Distribuição

* [**INSTALL.md**](../INSTALL.md) — Guia passo a passo de instalação do pacote Linux universal (`mark-linux-x86_64.tar.gz`), atalhos de barra de tarefas e desinstalação.
* [**updates.md**](../updates.md) — Histórico detalhado de versões, notas de lançamento e auditoria de qualidade.
