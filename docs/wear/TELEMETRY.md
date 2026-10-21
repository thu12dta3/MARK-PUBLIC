# Telemetria

Opt-in, inicialmente desligada. Wear registra bateria, carregamento e tempo mensurável de foreground em eventos locais limitados; fila máxima 32 entradas, sem áudio, conversa, saúde ou posição. Envio é manual, com ponte autorizada. Pausar não publica dados; limpar fila remove apenas seus eventos técnicos locais mediante ação explícita do usuário.

PC: SQLite privado ~/.local/state/mark/wear/telemetry.sqlite3, diretório 0700 e arquivo 0600, sem Git. Cada evento tem id/horário e allowlist de métricas numéricas. Janela de ingestão sete dias, máximo 32 eventos/8192 bytes, limite 10000 eventos e 4096 páginas SQLite; retenção técnica 30 dias aplicada ao receber lote. Idempotência por identidade assinada do M23 + id. Saúde/GPS/segredos e valores inválidos são recusados, sem schema flexível arbitrário.

GET /v1/wear/summary retorna amostras 24h/7d, foreground observado e queda percentual entre amostras não carregando; coleta esparsa pode perder consumo. Atribuição ao MARK e estimativa de autonomia: UNAVAILABLE. Settings → Consultar dados no PC mostra o resumo. Nenhuma média/trend clínica ou gráfico inventado.

Identidade de armazenamento é a ponte M23 autorizada, não prova do hardware do relógio. MVP usa um relógio por sessão; distinguir múltiplos relógios com proveniência explícita é PLANNED. Contadores de mic/GPS/tela/reinicializações/bateria interna podem ser indisponíveis; só coletar métricas realmente expostas em fase futura. Relatórios/gráficos e comparações A/B/C completos são PLANNED para v0.2, após campanha física.
