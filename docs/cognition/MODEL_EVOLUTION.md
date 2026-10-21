# Evolução de modelo

`ModelProvider.generate(ModelRequest) -> ModelResponse` desacopla a orquestração do nome Qwen. O provider Ollama mantém multimodalidade, parâmetros, retry e extração da resposta final existentes. ModelMetrics expõe apenas dados técnicos. O lifecycle de carregar/liberar também passa pelo provider, conservando ModelSession. Um futuro MARK/external provider implementará o mesmo contrato; os modelos Nano/Core/Deep/Vision e adapters não existem ainda.

`EmbeddingProvider` declara modelo, versão e dimensões. Trocar embedding exige reindexação transacional. O banco não depende de Qwen e a memória compartilhada permanece separada da base de referência e dos datasets.

Distillation futura: escolher teacher com direitos de uso, gerar planos explícitos/chamadas/verificação/respostas finais em fixtures licenciadas, revisar, deduplicar, dividir e avaliar student pequeno. Não capturar chain-of-thought privada. Nenhum pipeline de geração em massa ou executor de distillation foi iniciado.

Experimentos usam EXP-XXXX e relatam base/model_size, dataset/versão, método, quantização, hiperparâmetros, hardware, duração, RAM/VRAM quando medidas, tokens/s, MARKBench, segurança, notas e resultado. Valores não medidos são null/UNKNOWN. IDs são imutáveis; não sobrescrever um resultado para fingir melhoria.

Telemetria de aplicação é opt-in (`cognition.telemetry`), limitada a 1 MB em estado privado. O writer só aceita campos técnicos allowlisted e não grava prompts, respostas ou raciocínio. Sem opt-in, somente os relatórios explícitos de benchmark registram métricas. Não há upload automático.

A próxima fase proposta é Cognitive Foundation v0.2: bibliotecas realmente licenciadas, embeddings semânticos avaliados, chemistry toolbox ampliada, bases Computing/AFV revisadas, benchmark mais exigente e só então QLoRA experimental. Esta etapa não inicia v0.2.

A criação de um EXP-XXXX usa arquivo exclusivo: tentativas concorrentes não sobrescrevem o mesmo ID. Diagnóstico selfcheck e nomes de configuração Ollama permanecem compatibilidade legada; novos providers precisam implementar geração/lifecycle e adaptar diagnósticos.

## Retomada de 11/10/2026

QLoRA experimental passou a ter executor isolado; veja [treinamento](TRAINING.md). O fluxo de aplicação continua separado. A memória compartilhada e a biblioteca não são datasets automáticos.

O provider agora usa o Qwen rápido existente quando o perfil chat retorna duas respostas finais vazias. Mantém pedido, contexto e imagens, desativa raciocínio apenas nessa tentativa de recuperação, impede recursão e conserva o lifecycle por modelo. ModelMetrics registra fallback_from/fallback_reason, tentativas e tokens combinados, inclusive em falha. Não é filtro de conteúdo, mudança de autorização nem substituição permanente do modelo lento.
