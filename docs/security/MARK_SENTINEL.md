# MARK Sentinel — primeira integração local

Atualizado em 14/10/2026. O Sentinel é uma coleção de operações locais, explícitas e de leitura. Importar o módulo ou iniciar o MARK não inicia verificações.

## Auditoria inicial

- `clamscan` e `freshclam` estavam instalados; pacote observado: ClamAV 1.5.4.
- O ClamAV informou banco `27970`, datado de 13/04/2026. A data é antiga para a data deste registro; isso reduz a confiança de uma varredura. O Sentinel mostra a data local e não atualiza as assinaturas.
- `clamav-daemon.service` e `clamav-freshclam.service` estavam `inactive (dead)` e `disabled`. Não foram iniciados, habilitados ou alterados.
- Arquivos padrão de assinaturas existiam em `/var/lib/clamav`.
- Chat, voz e clientes do MARK convergem em `brain.answer`; o roteador Sentinel entra antes da chamada de modelo e só reconhece comandos explícitos. Assim, as frases de operação não dependem do modelo para inventar comandos.

## Operações disponíveis

No chat ou por voz, peça explicitamente:

- `Verifique se o PC tem vírus.` / `Meu computador está com malware?` (varre a pasta pessoal e informa o escopo; não equivale a uma varredura completa do sistema)
- `Faça uma verificação de vírus na minha pasta Downloads.`
- `Faça uma verificação de vírus em "/caminho/com espaços".`
- `Faça uma verificação completa do meu diretório pessoal.` (essa operação pode demorar; precisa ser pedida explicitamente)
- `Investigue esse arquivo "/caminho/arquivo".` (metadados, tipo indicativo, SHA-256 e ClamAV)
- `Consulte as bases de inteligência para descobrir se este arquivo é conhecido como malicioso: "/caminho/arquivo".`
- `Procure possíveis rogue agents.` / `Investigue os processos suspeitos.`
- `Investigue o processo PID 1234.`
- `Verifique se algum programa desconhecido está configurado para iniciar automaticamente.`
- `Investigue o serviço nome.service.`
- `Estado do ClamAV.`
- `Cancele a verificação do Sentinel.`
- `Mostre o relatório da última investigação.`

No app de PC, abra **Ferramentas → Antivírus e ameaças · Sentinel**. A janela permite escolher arquivo/pasta, verificar a pasta pessoal, consultar o estado do ClamAV, investigar processos e abrir o último relatório. Abrir a janela não inicia uma verificação; o botão **Verificar PC** inicia explicitamente a leitura da pasta pessoal. O diálogo mostra atividade, resultado e cancelamento da varredura.

As consultas a `/proc`, autostart e systemd mostram apenas os dados que o usuário atual consegue ler. Argumentos completos de processo não são coletados. Serviços não são iniciados, parados ou alterados. As conexões, quando `ss` existe, são listadas como observação e não classificadas por reputação de IP.

## ClamAV e interpretação

O scanner usa `clamscan --infected --no-summary` e argumentos separados, sem shell. Aceita arquivos regulares e diretórios existentes; rejeita links simbólicos e outros tipos de arquivo. Não remove nem repara amostras. A saída é limitada e vai para um arquivo temporário privado durante a execução, para evitar acumular linhas de arquivos limpos em memória.

Interpretação: código `0` = nenhuma detecção reportada; `1` = uma ou mais detecções; `2` ou outro código = erro, nunca “limpo”. Timeout e cancelamento são relatados como resultado parcial. O ClamAV pode não conseguir ler alguns arquivos sem privilégios; não há escalonamento automático. Uma varredura limpa não prova ausência de malware, especialmente com assinaturas desatualizadas.

O comando `Estado do ClamAV` consulta versão e arquivos de assinaturas. Não chama `freshclam`, não ativa serviços e não faz uma varredura.

Validação real em 14/10/2026: um arquivo de texto benigno terminou com código limpo; o arquivo EICAR oficial de 68 bytes foi detectado como `Eicar-Test-Signature`. Ambos ficaram em pasta temporária que foi removida ao final. Durante as duas leituras, o ClamAV emitiu aviso de que a base tinha mais de sete dias. O EICAR valida somente a detecção de teste e não mede a taxa de detecção contra malware real.

## Inteligência externa e privacidade

A primeira versão tem um adaptador opcional apenas para VirusTotal, por consulta do SHA-256. A consulta nunca envia o arquivo. A API requer chave. O padrão `sentinel.share_hash_with_virustotal` é `false`; mesmo com frase para consultar as bases, o MARK explica que não enviou dados até essa opção e a chave estarem configuradas. A variável de ambiente `MARK_VT_API_KEY` pode fornecer a chave sem gravá-la no repositório. A resposta limita-se às estatísticas efetivamente recebidas e sua data; falha ou ausência de relatório é inconclusiva.

Consultar o VirusTotal revela o hash ao provedor e pode incorporá-lo ao conjunto de dados acessível à comunidade, conforme a documentação da API. Configure isso somente após considerar a sensibilidade do arquivo. MalwareBazaar/ThreatFox não foram ativados: a primeira versão evita adicionar fontes sem configuração/autorização confirmada.

## Relatórios e proteção

O resumo mais recente é guardado em `~/.local/state/mark/sentinel/last-report.json`, com diretório `0700` e arquivo `0600`. É um único relatório substituível; contém caminho local, SHA-256 ou metadados que o usuário pediu para verificar. A interface oferece `Mostre o relatório da última investigação.` Entradas de persistência têm redação básica de campos comuns de credenciais. Não são coletados conteúdos completos dos arquivos investigados, histórico de shell ou argumentos de processo.

As heurísticas usam categorias qualitativas e sinais explicados, sem pontuação inventada. A detecção do ClamAV fica como “Alto”; sem detecção, um arquivo é informativo somente se a verificação terminou sem erro. Caminho incomum de processo é um indício, não prova. Esta é triagem inicial, não uma ferramenta forense completa.

## Testes

`python3 -m unittest tests.test_sentinel -v` cobre roteamento pelo ponto comum de chat, validação de caminhos, espaços e caracteres especiais, códigos do ClamAV, status sem atualizar assinaturas, ausência de varredura ao importar, cancelamento, chamada opcional por hash, falhas/ausência da fonte e auditorias somente leitura. Resultado: 25 testes do Sentinel passaram; a suíte geral está resumida no relatório desta alteração.

Foi separado teste unitário simulado de qualquer teste real do ClamAV. Antes de um teste local com EICAR, confirme que o diretório temporário será removido após a leitura; EICAR é o arquivo de teste inofensivo publicado pelo próprio EICAR, não malware real. O relatório de implementação registra se a assinatura local o detectou. Nenhuma amostra maliciosa real é usada.

## Ausência de atividade em segundo plano

Não foram criados daemons, serviços systemd, timers, cron jobs, verificações no startup ou consultas externas recorrentes. O único subprocesso do Sentinel é iniciado por uma solicitação explícita e termina ao concluir, expirar ou ser cancelado. A rotina existente do MARK pode chamar seu mecanismo normal de lembretes, independente deste módulo; o Sentinel não se conecta a esse mecanismo.

## Limites que permanecem

- Uma primeira implementação de triagem, não cobertura integral de investigação de ameaças.
- Sem correlacionar cada PID com conexões, sem inventário de pacotes conhecidos e sem baseline de persistência aprovado pelo proprietário.
- A auditoria systemd mostra unidades instaladas/estado básico e caminhos acessíveis, não uma revisão semântica completa de todos os arquivos de unidade.
- Sem upload de arquivo, quarentena, correção ou encerramento de processo.
- O painel do app de PC mostra progresso indeterminado enquanto o ClamAV trabalha; a verificação pode ser cancelada. A integração por chat/voz retorna o relatório quando termina.
- A assinatura do ClamAV encontrada na auditoria inicial estava desatualizada; atualizar manualmente por mecanismo normal do sistema é necessário para melhorar a cobertura.
- O Virustotal opcional ainda depende de o proprietário configurar chave e `share_hash_with_virustotal: true` localmente. Não foi consultado durante a implementação.

## Registro de validação da implementação

- O módulo foi validado em ambiente local controlado; os serviços do MARK foram confirmados ativos.
- O comando real `Estado do ClamAV`, enviado por `brain.answer`, retornou a versão e a data das assinaturas. Isso confirma o roteamento interno; o aplicativo móvel usa o mesmo ponto de entrada.
- `python3 -m py_compile src/mark/sentinel.py src/mark/brain.py src/mark/config.py`: passou. `python3 -m unittest tests.test_sentinel -q`: 25 passaram.
- A suíte geral executada por `python3 -m unittest discover -s tests -p 'test_*.py' -q` encontrou 599 testes: 6 falhas e 2 erros. As falhas foram nos testes existentes de interface Desktop (`test_desktop_library`, `test_desktop_security`) e integração GitHub (`test_github_assistant`); os erros foram importação do `mark` ausente em `test_assistance` nesta forma de execução e `TypeError` no mock do teste GitHub `test_unknown_repo_denied_no_request`. Nenhum desses arquivos integra o Sentinel. O resultado geral não é totalmente verde.
- `git diff --check` passou. Não foram encontrados arquivos de unidade/timer do Sentinel; os dois serviços preexistentes do ClamAV continuam `disabled` e `inactive` após a validação.

## Integração no app de PC e linguagem natural

Em 14/10/2026, a janela do Sentinel foi adicionada a **Ferramentas → Antivírus e ameaças · Sentinel**. Ela inicia ociosa, oferece botões separados para verificar a pasta pessoal, escolher uma pasta/arquivo, consultar o estado do ClamAV, investigar processos e consultar o relatório mais recente. A barra de atividade, resultado e cancelamento ficam no próprio diálogo.

O roteador comum de chat e voz reconhece pedidos como `Verifique se o PC tem vírus`, `Meu computador está com malware?` e `O PC está infectado?`. Eles iniciam uma verificação do ClamAV na pasta pessoal do usuário, mostram esse escopo na resposta e não afirmam que todo o sistema foi coberto. O teste local do reconhecedor usou `scan_path` simulado e confirmou o caminho selecionado; nenhum resultado simulado é apresentado como diagnóstico real.

Durante uma checagem inicial, o exemplo foi executado uma vez antes de substituir o scanner por um mock e iniciou uma leitura real da pasta pessoal. O operador encerrou-a após cerca de 19 segundos. O relatório privado foi marcado como `recognizer-smoke-check` e inconclusivo; não representa PC limpo ou infectado. Uma segunda checagem, com scanner simulado, confirmou o reconhecimento sem iniciar outra varredura real. A compilação dos módulos e `git diff --check` passaram; a suíte completa não foi repetida nesta integração visual.
- Fonte externa consultada na validação foi só documentação oficial. Não houve consulta VirusTotal nem transmissão de hash.
