# Integração opcional com Honcho

O MARK mantém seu histórico local em todos os casos. Honcho acrescenta uma camada de memória de longo prazo entre sessões e dispositivos: os turnos novos podem ser sincronizados e uma representação curta pode ser recuperada para contextualizar uma resposta.

## Privacidade e ativação

- A integração vem desligada por padrão.
- A ativação fica em **Memória** no app MARK para PC. A caixa de seleção autoriza enviar mensagens e respostas novas ao endereço e workspace escolhidos.
- O histórico local anterior não é importado nem enviado automaticamente.
- A chave do Honcho fica no chaveiro do sistema por `secret-tool`, ou pode vir da variável `HONCHO_API_KEY`. Ela nunca é gravada no JSON do MARK.
- `http://` é permitido somente para um servidor local. Endereços remotos precisam usar HTTPS.
- O SDK usa tempo limite curto, sem repetição automática. Se Honcho falhar, a resposta e o histórico local continuam funcionando; o contexto remoto é simplesmente omitido.
- A sessão remota padrão `shared` permite compartilhar a memória entre as interfaces PC, celular e relógio que gravam no histórico compartilhado do MARK.

## Dependência

`honcho-ai` é carregado sob demanda. Instale as dependências do MARK pelo ambiente Python usado para executá-lo. Sem a dependência, a chave ou a configuração, o app permanece operacional e a integração fica inativa.

## Honcho hospedado ou próprio

O padrão é `https://api.honcho.dev`, que requer uma chave de API. Um servidor próprio pode ser configurado no mesmo painel; o MARK aceita HTTP somente em `localhost` e HTTPS para hosts remotos. O servidor Honcho local pode ter seus próprios requisitos de implantação e provedor de raciocínio.

## Escopo enviado

Somente texto de turnos novos concluídos pelo armazenamento compartilhado. O MARK limita cada lado da conversa a 6.000 caracteres e o contexto retornado a 1.800 caracteres. Anexos e imagens não são enviados por esta integração. Se uma credencial for digitada como parte do texto da conversa, ela também poderá ser enviada; não cole senhas nem tokens quando a sincronização estiver ativa.

## Configuração e suporte

A configuração não secreta usa a seção `honcho` em `~/.config/mark/config.json`; a configuração exemplo documenta os nomes. Para parar novos envios, desmarque a opção e salve. As falhas registram apenas o tipo da exceção, nunca o texto da conversa nem a chave.

Honcho fornece SDK Python, sessões, peers, ingestão de mensagens e consulta de representações; consulte o [quickstart oficial](https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/introduction/quickstart.mdx) e o [repositório oficial](https://github.com/plastic-labs/honcho).
