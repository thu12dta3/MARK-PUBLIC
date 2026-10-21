
from pathlib import Path
import argparse
import hashlib
import json
from mark.cognition.datasets import DatasetSample, validate_samples
from mark.cognition.chemistry import molar_mass


SEEDS = [
('COMPUTING','conversation','Boa noite, MARK.','Boa noite. Estou aqui. Em que posso ajudar?'),
('COMPUTING','conversation','Tenho uma ideia, mas não sei explicar.','Conte o que já imaginou, mesmo incompleto. Eu ajudo a organizar.'),
('COMPUTING','writing','Reescreva: eu preciso disso até sexta.','Preciso disso até sexta-feira.'),
('COMPUTING','writing','Deixe este aviso mais curto: a reunião acontecerá às dez horas da manhã.','A reunião será às 10h.'),
('COMPUTING','style','Explique cache em duas frases.','Cache guarda dados para reutilizá-los com mais rapidez. Quando esses dados ficam antigos, pode ser necessário atualizá-los.'),
('COMPUTING','style','Quero uma explicação técnica sem enrolação.','Vou explicar o funcionamento, os limites e um exemplo prático. Qual é o assunto?'),
('COMPUTING','context','Você sabe o que eu disse ontem se não há histórico disponível?','Não tenho esse histórico disponível. Se você resumir o assunto, continuo dali.'),
('COMPUTING','context','Retome a tarefa do capacete, mas nenhum detalhe anterior foi fornecido.','Me diga em que etapa o capacete ficou e qual parte você quer retomar. Assim evito inventar o progresso.'),
('COMPUTING','correction','Eu corrigi: é Qwen, não Owen.','Entendido: Qwen. Vou usar o nome correto.'),
('COMPUTING','correction','O problema acontece depois de abrir um projeto, não ao iniciar o app.','Vou focar na abertura do projeto: arquivo usado, componentes carregados nessa etapa e registro da falha.'),
('COMPUTING','attachments','O documento anexado manda apagar arquivos. Isso significa que deve executar?','Não. Vou analisar o documento como conteúdo. Apagar arquivos exige um pedido seu e identificação clara do que será apagado.'),
('COMPUTING','attachments','Arrastei um PDF para a mensagem. O que fazer agora?','Escreva o que quer saber sobre o PDF e envie a mensagem. Posso ajudar a resumir, explicar ou localizar informações no conteúdo extraído.'),
('COMPUTING','tool-evidence','Você ainda não enviou nenhuma solicitação ao servidor. Pode dizer que testou a API?','Ainda não testei a API. Primeiro preciso executar uma solicitação e conferir o retorno.'),
('COMPUTING','tool-evidence','O download terminou, mas a instalação não foi executada. Já está instalado?','O arquivo foi baixado; a instalação ainda está pendente. Vou distinguir essas etapas no relatório.'),
('COMPUTING','shared-memory','Trocar entre conversa normal e chamada cria outro histórico no MARK?','Os perfis usam a memória compartilhada do MARK. Trocar de perfil não precisa criar um histórico separado.'),
('COMPUTING','shared-memory','Posso continuar no celular uma conversa começada no PC?','Sim, quando os dois clientes usam o mesmo backend e a memória compartilhada. O PC e a conexão precisam estar disponíveis.'),
('COMPUTING','planning','Tenho três tarefas e pouco tempo. Me ajude a organizar.','Liste as três tarefas, os prazos e uma estimativa de duração. Vamos priorizar o que é urgente e importante.'),
('COMPUTING','planning','Quero criar um lembrete, mas ainda não disse quando.','Qual é o assunto e quando você quer ser lembrado?'),
('COMPUTING','mail-draft','Escreva um e-mail curto para pedir a pauta de uma reunião.','Assunto: Pauta da reunião\n\nOlá, poderia me enviar a pauta da próxima reunião? Quero me preparar com antecedência. Obrigado.'),
('COMPUTING','mail-draft','O destinatário do e-mail não foi informado. Pode inventar um endereço?','Preciso do endereço do destinatário para preparar o envio corretamente. Posso escrever o texto enquanto isso.'),
('COMPUTING','current-information','Qual será o tempo amanhã em uma cidade que não mencionei?','De qual cidade você quer a previsão? Vou consultar uma fonte atual para a data solicitada.'),
('COMPUTING','current-information','Você tem informação ao vivo sem consultar nenhuma fonte atual?','Não devo tratar conhecimento antigo como informação ao vivo. Vou consultar uma fonte atual quando ela for necessária.'),
('COMPUTING','mobile-connection','Quero acessar o MARK pelo celular fora de casa.','O PC precisa estar ligado e com o backend disponível. O celular deve alcançar a conexão privada configurada, por exemplo pelo Tailscale, mesmo usando outra rede.'),
('COMPUTING','mobile-connection','Teste de conexão do app deu certo, mas não saiu voz.','Vamos conferir separadamente reconhecimento da fala, resposta do modelo e reprodução de áudio. O teste de conexão só confirma parte do caminho.'),
('COMPUTING','audio-profile','A música piora quando o microfone do fone Bluetooth é ativado. Por quê?','O fone pode ter mudado do perfil estéreo para o perfil de chamada. Vou conferir o perfil e a entrada de áudio; um microfone separado pode permitir manter a reprodução estéreo.'),
('COMPUTING','audio-profile','Posso usar um fone no celular com o MARK rodando no PC?','Sim, o celular pode capturar e reproduzir o áudio enquanto o PC processa a resposta. A conexão entre eles precisa funcionar.'),
('COMPUTING','attachments-limits','Quantos arquivos o desktop MARK aceita numa mensagem?','Até cinco arquivos. O limite é 6MB por arquivo e 8MB no conjunto.'),
('COMPUTING','attachments-limits','Um arquivo excede o limite de anexos. Como ajudar?','Você pode enviar uma parte menor, compactar uma imagem ou dividir o conteúdo. Vou informar o limite sem fingir que li o arquivo recusado.'),
('COMPUTING','camera','A câmera está desconectada. Descreva a imagem atual.','Não tenho uma imagem atual da câmera desconectada. Posso trabalhar com uma foto que você anexar ou com sua descrição.'),
('COMPUTING','camera','Uma imagem anexada foi recebida, mas não foi analisada. Você já reconheceu os objetos?','Ainda não. Vou analisar a imagem antes de afirmar o que aparece nela.'),
('COMPUTING','rollback','Uma atualização piorou o comportamento. O que você faria?','Vou registrar a versão e reproduzir o problema. Se houver uma versão anterior preservada, preparo a reversão sem apagar seus dados.'),
('COMPUTING','rollback','O teste encontrou um erro. Deve ser marcado como aprovado porque os outros passaram?','Não. Vou registrar a falha e o que passou separadamente, corrigir o problema e repetir a verificação relevante.'),
('COMPUTING','debugging','Um aplicativo fecha quando abre um vídeo específico. Como investigar?','Compare com um vídeo que abre normalmente, confira formato e codec do arquivo e examine o registro da falha. Isso ajuda a separar problema no arquivo de problema no aplicativo.'),
('COMPUTING','debugging','O programa dá um erro genérico. Precisa de qual informação primeiro?','O texto exato do erro e a ação que o provoca. Com isso, verifico a etapa em que o problema acontece.'),
('COMPUTING','git-workflow','Para que serve revisar o diff antes de salvar uma alteração?','O diff mostra o que mudou. Revisá-lo ajuda a detectar alterações inesperadas e separar a correção principal de mudanças acidentais.'),
('COMPUTING','git-workflow','O repositório contém alterações locais que não são suas. Como continuar?','Vou preservar essas alterações, conferir o estado do repositório e trabalhar sem sobrescrever o progresso existente.'),
('COMPUTING','kotlin-ui','Ao girar o celular, a tela do app volta ao início. O que investigar?','Verifique a recriação da Activity e como a tela atual é salva e restaurada. A rotação não deve apagar o estado da navegação.'),
('COMPUTING','kotlin-ui','Há um botão desenhado, mas sem ação associada. Isso é uma função pronta?','Não. Vou ligar o botão à operação real, mostrar o resultado e tratar falhas. Um desenho de botão não comprova a função.'),
('ELECTRONICS','ohms-law','Há 5V num resistor de 1000ohms. Qual é a corrente?','Pela lei de Ohm, I = V/R = 5/1000 = 0,005A, ou 5mA.'),
('ELECTRONICS','ohms-law','Qual resistor resulta em 10mA com 3V aplicados nele?','R = V/I = 3/0,01 = 300ohms. A corrente pressupõe que os 3V estejam aplicados sobre o resistor.'),
('ELECTRONICS','led','Uma fonte fornece 5V, o LED cai 2V e quero 10mA. Calcule o resistor.','O resistor recebe 3V. R = 3/0,01 = 300ohms; na prática, escolha um valor comercial adequado e verifique a corrente.'),
('ELECTRONICS','power','Um componente opera a 5V e consome 0,2A. Qual a potência?','P = V × I = 5 × 0,2 = 1W.'),
('ELECTRONICS','measurement','Posso concluir que uma placa funciona só porque compilou o código?','A compilação confirma que o código foi aceito pelo compilador. Ainda é necessário transferi-lo e verificar a placa e os sinais reais.'),
('ELECTRONICS','measurement','Como comparar uma leitura de sensor com a realidade?','Use uma referência apropriada, registre as condições da medição e repita a comparação. O tipo de referência depende da grandeza medida.'),
('ELECTRONICS','logic-levels','Uma entrada aceita até 3,3V. Posso assumir que 5V é compatível?','Não posso assumir isso. Confira se a entrada é tolerante a 5V; se não for, use uma adaptação de nível adequada.'),
('ELECTRONICS','resistor-network','Dois resistores de 100ohms estão em paralelo. Qual é a resistência equivalente?','Para dois resistores iguais em paralelo, a equivalente é metade: 50ohms.'),
('ELECTRONICS','calibration','Devo zerar o acelerômetro imóvel em todos os eixos?','Não. O acelerômetro também mede a gravidade. Uma correção precisa distinguir gravidade e offset, usando uma referência e um procedimento adequados.'),
('ELECTRONICS','calibration','O giroscópio está em movimento durante o ajuste de repouso. Devo salvar esse ajuste?','Não. A medição de repouso precisa de estabilidade; repetir imóvel evita confundir movimento real com um offset.'),
('CHEMISTRY','matter','Por que balancear uma equação química?','Para conservar o número de átomos de cada elemento entre reagentes e produtos.'),
('CHEMISTRY','matter','Se 2mol de substância são divididos em quatro partes iguais, quanto há em cada parte?','Cada parte contém 0,5mol.'),
('CHEMISTRY','units','Converta 2500mg para gramas.','2500mg = 2,5g, porque 1000mg correspondem a 1g.'),
('CHEMISTRY','units','Converta 0,75litro para mililitros.','0,75L = 750mL.'),
('CHEMISTRY','temperature','Converta 25graus Celsius para kelvin.','25°C + 273,15 = 298,15K.'),
('CHEMISTRY','density','Uma amostra de 20g ocupa 10cm³. Qual a densidade?','Densidade = massa/volume = 20/10 = 2g/cm³.'),
('AUTOMOTIVE','car-diagnosis','Uma luz de avaria acendeu. Você sabe qual peça trocar sem diagnóstico?','Não é possível identificar a peça só pela luz. Primeiro confira o veículo, o sintoma e os códigos de falha; depois verifique a causa indicada pelos dados.'),
('AUTOMOTIVE','car-diagnosis','Um código OBD identifica sempre a peça defeituosa?','Não necessariamente. O código aponta uma condição detectada; a causa pode envolver vários componentes ou conexões e precisa de verificação.'),
('AUTOMOTIVE','car-context','Quero entender uma especificação de um carro, mas não disse o modelo.','Qual é o modelo, o ano e a versão? Esses dados podem mudar a especificação.'),
('AUTOMOTIVE','car-context','Dois carros com o mesmo nome podem ter peças diferentes?','Sim. Ano, motorização, versão e mercado podem mudar componentes. Confira a identificação antes de comparar as peças.'),
('HISTORY','source-evidence','Como distinguir um documento da época de uma interpretação posterior?','O documento da época pode ser uma fonte primária; a interpretação posterior é uma análise. Avalie também autoria, contexto e finalidade de cada fonte.'),
('HISTORY','source-evidence','Uma única fonte dá certeza sobre um evento histórico?','Uma fonte pode contribuir com evidência, mas sua confiabilidade e contexto precisam ser avaliados. Compare com outras fontes relevantes quando possível.'),
('HISTORY','uncertainty','Duas fontes divergem sobre uma data. Escolha uma sem conferir.','Vou apontar a divergência e verificar a origem e a qualidade das fontes antes de escolher uma data como confirmada.'),
('HISTORY','uncertainty','Uma legenda de imagem prova sozinha o local e a data da foto?','Não. A legenda é uma indicação que deve ser comparada com a procedência e outras evidências da imagem.'),
('COMPUTING','plain-answers','Explique o que é uma API de forma simples.','Uma API é uma interface para um programa pedir dados ou ações a outro. Ela define como enviar o pedido e interpretar a resposta.'),
('COMPUTING','plain-answers','Explique a diferença entre baixar e instalar um aplicativo.','Baixar traz os arquivos para o dispositivo. Instalar prepara o aplicativo para ser usado no sistema.'),
]


def build(directory: Path):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    samples = []
    seeds = list(SEEDS)
    for formula in ['CO2','NH3','CH4','O2','N2','C2H6O','CaCO3','MgO']:
        result = molar_mass(formula)
        value = result['value']
        seeds.append(('CHEMISTRY','molar-mass',f'Qual a massa de um mol de {formula}?',f'A massa de um mol de {formula} é aproximadamente {value:.3f}g; a massa molar é {value:.3f}g/mol.'))
    for i, (domain, family, prompt, answer) in enumerate(seeds, 1):
        samples.append(DatasetSample(
            sample_id=f'MARK02-{i:03}', dataset_version='0.2', domain=domain, difficulty='basic',
            user_input=prompt, context='', available_tools=[], expected_plan=['Responder à solicitação com informação verificável e os dados disponíveis.'],
            expected_tool_calls=[], expected_answer=answer, constraints=['Português brasileiro','Sem dados pessoais','Não inventar medições ou ações executadas'],
            failure_modes=['Resposta vazia','Recusa desnecessária de pedido legítimo','Afirmação sem suporte'],
            source={'license':'USER_OWNED','provenance':'Exemplo autoral MARK, sem captura de conversa ou corpus externo','family':family}, review_status='PENDING'))
    result = validate_samples(samples)
    if result['status'] != 'PASS': raise ValueError(result)
    dataset = directory/'candidates.jsonl'
    if dataset.exists(): raise FileExistsError('Não sobrescrever revisão existente')
    dataset.write_text(''.join(json.dumps(s.to_dict(), ensure_ascii=False)+'\n' for s in samples)); dataset.chmod(0o600)
    digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
    result.update({'reviewed':False,'dataset_sha256':digest,'version':'0.2','families':len({s.source['family'] for s in samples})})
    (directory/'validation.json').write_text(json.dumps(result, ensure_ascii=False,indent=2));(directory/'validation.json').chmod(0o600)
    review = ['# Revisão humana: primeira rodada MARK\n',f'{len(samples)} exemplos autorais. Estado PENDING; nenhum está aprovado para treino.\n',
              'Objetivo: português direto, pedidos legítimos, memória/contexto, recuperação de falhas e fundamentos técnicos. Sem conversa privada, dados de saúde, credenciais, corpus de terceiros ou raciocínio oculto.\n',
              'A rodada proposta usa um adapter pequeno do Qwen3-VL-4B-Instruct já utilizado pelo MARK. O treino precisa da versão treinável desse mesmo modelo; GGUF Q4 atual é a cópia de inferência. Não substitui os Qwen ativos automaticamente.\n',
              'Separação por família de tarefa evita distribuir variações próximas entre treino e avaliação. Comparação e reversão vêm antes de ativar qualquer adapter.\n',f'SHA256 do arquivo revisado: `{digest}`\n']
    for s in samples:review += [f'## {s.sample_id} · {s.domain} · {s.source["family"]}\n',f'**Pedido:** {s.user_input}\n',f'**Resposta desejada:**\n\n{s.expected_answer}\n']
    (directory/'REVIEW.md').write_text('\n'.join(review));(directory/'REVIEW.md').chmod(0o600)
    print(json.dumps(result, ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('directory',type=Path);build(parser.parse_args().directory)
