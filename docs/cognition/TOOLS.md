# Ferramentas e autorização

`ToolRegistry` registra nome, descrição, schemas de entrada/saída, risco, timeout, permissões e efeitos. A fase aceita READ_ONLY e nenhum efeito externo. Um `ActionRequest` contém só o nome allowlisted e argumentos validados; `AuthorizationLayer` confere grants recebidos da aplicação. O modelo não cria grants nem acessa funções internas por nome arbitrário. Esta camada adicional não substitui a autorização da API/Android existente.

Ferramentas implementadas: chemistry.molar_mass, stoichiometry (razão de coeficientes já balanceados), unit_conversion, ideal_gas, ph_calculation (apenas ácido forte monoprotônico ideal, nas concentrações declaradas), periodic_table_lookup (conjunto inicial de elementos), knowledge.search e knowledge.stats. A chamada local de massa molar está integrada em PC/celular/voz e não precisa de Qwen. As demais tools são acessíveis pelo CLI e registry; seleção livre de tools pelo modelo é PLANNED.

```bash
MARK tool list
MARK tool run chemistry.molar_mass --arguments '{"formula":"H2O"}'
MARK tool run chemistry.unit_conversion --arguments '{"value":1000,"from_unit":"g","to_unit":"kg"}'
```

Entradas desconhecidas, campos extras, coeficiente zero, NaN/inf e unidades incompatíveis falham. Fórmulas neutras com parênteses são aceitas; cargas, hidratos e isótopos ainda não. Os pesos atômicos abreviados de referência foram conferidos na [CIAAW, tabela 2024](https://ciaaw.org/abridged-atomic-weights.htm). A precisão é aproximada para materiais usuais, não análise isotópica. Balanceamento automático, equilíbrio, redox e compound lookup estão PLANNED.

O timeout usa uma future: uma função já iniciada pode continuar no worker após o timeout. Portanto só são registradas implementações internas de leitura/cálculo com trabalho limitado. A função não é um sandbox; plugins externos e tools com efeitos precisam de isolamento e autorização adicionais antes de existir. Não há shell, email, exclusão, escrita GitHub ou controle de hardware nessa registry.
