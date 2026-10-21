# Treinamento local experimental

A fundação v0.1 continha somente gates e `training/qlora-planned.json`. Em 11/10/2026, após a retomada e a aprovação explícita de 72 exemplos pelo proprietário, foi acrescentado um executor local em `src/mark/cognition/training.py`. O esqueleto DRAFT continua não aprovado.

## Dados e isolamento

`tools/training/build_curriculum.py` prepara exemplos autorais PENDING, revisão legível e relatório de validação. Não aprova dados. O proprietário deve revisar o conteúdo; a aprovação fica vinculada ao SHA-256 do JSONL. O executor verifica novamente o arquivo aprovado e o conteúdo de cada split. Não coleta chats, anexos, senhas, saúde ou raciocínio oculto.

O currículo v0.2 tem 72 exemplos e 36 famílias: 56 treino, 11 validação, 5 teste. Famílias de tarefas ficam disjuntas. Essa separação não prova ausência de similaridade semântica ou contaminação prévia do modelo. O conjunto é pequeno; não sustenta alegações de competência abrangente.

## Ambiente e base

Ambiente Python 3.12 isolado em `~/.local/share/mark/training-env`, separado do Python/PyQt do desktop. PyTorch 2.9.1+cu126, Transformers 4.57.1, PEFT 0.17.1, Accelerate 1.11.0, bitsandbytes 0.48.1. O ambiente foi usado porque uma instalação privilegiada por pacman não estava disponível nessa sessão.

É a versão treinável do mesmo Qwen rápido: `Qwen/Qwen3-VL-4B-Instruct`, revisão `ebb281ec70b05090aa6165b016eac8ec08e71b17`, licença Apache-2.0. `tools/training/fetch_base.py` registra a revisão, baixa apenas pesos/configuração/tokenizer/licença públicos e confere tamanho e SHA-256 LFS. A base e os dois Qwen originais são preservados. Não se aplica o adaptador Instruct ao Qwen Thinking.

Receita: `training/qlora-mark-v02.json`, QLoRA NF4 com quantização dupla, rank8/alpha16 em q_proj/v_proj de linguagem, batch1/acumulação4, uma época, contexto256. Embeddings e visão ficam congelados em FP16. O gate considera até4,5B parâmetros reais, incluindo visão, até8GiB de VRAM, dados revisados e baseline real PASS. Não há treinamento de todos os parâmetros nem ativação automática.

## Execução e recuperação

```bash
PYTHONPATH=src ~/.local/share/mark/training-env/bin/python -m mark.cognition.training split \
  --dataset /pasta/privada/approved.jsonl --approval /pasta/privada/approval.json --output /pasta/privada/splits

PYTHONPATH=src ~/.local/share/mark/training-env/bin/python -m mark.cognition.training preflight \
  --config training/qlora-mark-v02.json --approval /pasta/privada/approval.json \
  --baseline /pasta/privada/baseline-call.json --base /pasta/base/revisao \
  --splits /pasta/privada/splits --output /pasta/privada/preflight.json
```

`run` usa os mesmos argumentos, com `--output` apontando para uma pasta nova. `--resume` exige configuração, dataset e base correspondentes, checkpoint-adapter e optimizer salvos. Não sobrescrever rodada anterior. Arquivos parciais, pesos-base com checksum divergente e links simbólicos nos inputs críticos são recusados.

O executor mede loss antes/depois em validação e teste e guarda respostas desses cinco casos. Resposta esperada completa: exemplos que excedem o contexto são recusados, não truncados. Checkpoint a cada quatro atualizações e ao interromper. Gradientes não finitos nunca são aplicados: AMP reduz a escala e repete o mesmo lote, com limite de tentativas. Falha persistente interrompe e preserva progresso. A validação de loss admite até2% de variação; PASS não substitui revisão das respostas, avaliação de segurança ou compatibilidade do runtime.

Estado e pesos ficam em diretórios privados fora do Git. `TRAINED_CANDIDATE` significa candidato treinado, não modelo ativo. Publicar código/receita/resultado agregado, não credenciais, conversas, optimizer ou pesos completos. A licença proprietária do MARK não substitui as licenças dos componentes Qwen/llama.cpp.

Resultados reais desta rodada: [relatório de 11/10](TRAINING_20261011.md). Ferramentas/RAG continuam preferíveis para cálculos, fontes e fatos atuais; fine-tuning não concede permissões de execução.
