# Modelo especializado do Engineer

## Estado inicial

O catálogo de especializações Geral/Engenheiro/Professor e o contexto assinado pelo servidor já existiam. A API já mantinha sessões de modelo para chat e chamada, mas não havia um perfil dedicado ao Engineer nem seleção de modelo com base nessa especialização.

O perfil Engineer usa `R4C3R/qwen2.5-coder-7b-instruct-heretic:q4_k_m` quando a conversa é textual e o cliente marca `specialization=engineer`. O chat Geral e as chamadas de voz mantêm seus perfis próprios. Mensagens com imagens permanecem no perfil multimodal Geral, pois este modelo é apenas texto.

O modelo é uma publicação comunitária no registro do Ollama, não um lançamento oficial do projeto Qwen. O registro lista 7,62B parâmetros, quantização Q4_K_M, 4,7 GB, janela de 32K e entrada textual. O tamanho do arquivo não garante que o modelo caiba junto de outros processos na VRAM; a RTX 2060 SUPER tem 8 GB e o carregamento ocorre sob demanda.

O cartão desta variante informa a licença `qwen-research`; revise os termos antes de redistribuir pesos. O download foi feito apenas para o armazenamento local do Ollama; nenhum peso foi copiado para o repositório.

## Disponibilidade e fallback

O nome fica em `models.engineer` na configuração padrão. O MARK não baixa modelos automaticamente. Se o perfil não iniciar ou a inferência falhar, a API faz uma única tentativa com o perfil `chat` e informa a troca; não alterna em ciclos. Antes de carregar outro perfil, ela libera apenas suas próprias sessões incompatíveis, e o bloqueio global do Ollama continua serializando requisições. A tag foi baixada para o armazenamento local do Ollama para a avaliação desta tarefa. Se removida no futuro, pode ser restaurada com:

```sh
ollama pull R4C3R/qwen2.5-coder-7b-instruct-heretic:q4_k_m
```

Depois, reinicie o serviço MARK. Imagens não são encaminhadas a este modelo; continuam usando o modelo multimodal já configurado.

## Limites de segurança

Heretic descreve uma variante de pesos comunitária e não concede acesso a ferramentas. O modelo só produz respostas. A integração não dá shell, rede, credenciais nem acesso adicional a arquivos; operações do Engineer continuam sujeitas aos mecanismos atuais de missão, autorização, escopo de workspace, revisão de diff e sandbox. Saídas de arquivos e repositórios continuam sendo dados não confiáveis.

As lacunas de segurança globais já documentadas não foram corrigidas nesta etapa: a rota HTTP legada do Tailscale, o Bearer legado e os demais itens permanecem em [SECURITY_DEBT.md](../SECURITY_DEBT.md). O encaminhamento usa a especialização validada pelo servidor e não aceita a tag do modelo enviada pelo cliente.

## Arquivos alterados

- `src/mark/config.py` e `config/config.example.json`: acrescentam a tag `models.engineer` sem substituir Geral nem chamada.
- `src/mark/mobile_api.py`: roteia conversas textuais de Engineer, libera perfis incompatíveis próprios antes de alternar, faz fallback único e mantém imagens no modelo multimodal.
- `src/mark/brain_model.py`: identifica falhas de inferência do perfil Engineer para permitir um fallback controlado.
- `tests/test_mobile_api.py`: verifica roteamento, imagens, indisponibilidade e fallback após erro.
- `tests/test_core.py`: torna o teste de pesquisa web independente da preferência pessoal salva do plugin.

## Evidência desta etapa

- A rota seleciona o perfil `engineer` a partir do contexto validado pelo servidor, sem aceitar o nome do modelo enviado pelo cliente.
- O fallback ao perfil Geral é exercitado por testes quando o modelo especializado não inicia e quando a inferência falha; os testes usam sessões falsas e não baixam nem carregam o modelo.
- `ollama show` confirmou qwen2, 7,6B parâmetros, contexto 32.768 e quantização Q4_K_M.
- Smoke test real no Ollama: gerou uma função iterativa de fatorial com validação negativa e exemplos; 119 tokens de saída em 7,31 s, dos quais 5,39 s foram carregamento reportado pelo Ollama. O verificador isolado do MARK aprovou a sintaxe (PASS); o código foi analisado, não executado. Pico de VRAM e RAM não foram amostrados, então permanecem não medidos.
- A variante e as tags foram conferidas no [registro Ollama](https://ollama.com/R4C3R/qwen2.5-coder-7b-instruct-heretic/tags). O upstream do modelo base é [Qwen2.5-Coder](https://ollama.com/library/qwen2.5-coder).
- A suíte ampla não foi executada antes das mudanças, portanto não há baseline de testes deste turno. Após a implementação, 156 testes focados passaram (`test_mobile_api`, `test_core`, `test_core_context`, `test_coding_verify`).
- A auditoria da dívida geral continua em [SECURITY_DEBT.md](../SECURITY_DEBT.md): Tailscale HTTP legado, Bearer legado e lacunas de autorização continuam abertos. Esta integração não os corrige nem os amplia; tampouco eleva o estado geral de segurança do produto.
- Nenhuma branch foi criada, nenhum commit ou push foi feito, e o serviço/app de produção não foi reiniciado.

## Próxima validação necessária

Depois de instalar esta revisão no app MARK ativo, validar o roteamento real do Engineer e medir pico de VRAM e RAM durante uso representativo. Só então comparar qualidade em tarefas adicionais do MARKBench. O smoke test direto do Ollama passou, mas não substitui o teste de ponta a ponta no app, nem prova especialização em todas as linguagens suportadas pelo Engineer.
