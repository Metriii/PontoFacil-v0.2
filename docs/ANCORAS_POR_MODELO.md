# Âncoras de recorte por modelo

Resumo das regras que decidem **onde o recorte da anonimização começa** (tudo
acima do limite é descartado antes do OCR externo). Código em
`backend/anonimizacao/ancoras.py` e `detector._resolver_inicio`.

Princípio: **fail closed**. Sem âncora confiável, a imagem é bloqueada; nunca se
corta de forma arbitrária. Nenhuma regra usa coordenada fixa, nome de pessoa ou
texto manuscrito. Os diagnósticos de log guardam só rótulos fixos e números.

## Visão geral

| Modelo | Âncora principal | Alternativas (nessa ordem) |
|---|---|---|
| 1 (listra azul) | faixa `1ª/2ª Quinzena` (`QUINZENA_MODELO1`) | faixa do mês 年/月份 (`ANCHOR_MONTH_BAND`) → rótulos de coluna (`ANCHOR_FALLBACK`) → geometria da grade (`ANCHOR_GEOMETRIC`) |
| 2 (padrão) | linha com `Saída` (`CABECALHO_SAIDA`) | geometria da grade (`ANCHOR_GEOMETRIC`) → rótulos de coluna (`ANCHOR_FALLBACK`, igual ao Modelo 1); só se a falha for de leitura de texto |
| 3 (listra verde) | faixa `1ª/2ª Quinzena` | rótulos de coluna (`ANCHOR_FALLBACK`). Sem geometria e sem faixa do mês |
| 4 e 5 | caminho genérico do detector | — |

## Modelo 1: nova ordem de fallback

```text
quinzena -> faixa do mês (NOVA) -> rótulos (ANCHOR_FALLBACK) -> geometria (ANCHOR_GEOMETRIC)
```

A faixa do mês só é consultada quando a **quinzena não existe**
(`ANCHOR_NOT_FOUND`). Cartões com "Quinzena" seguem pela âncora de quinzena,
sem mudança. Modelos 2, 3, 4 e 5 nunca usam a faixa do mês.

### Faixa do mês (variação chinesa do Modelo 1)

Cartão azul sem "Quinzena", título 考勤表. A faixa impressa com os rótulos
**年** e **月份** fica entre o título e o cabeçalho das colunas
(日期 / 上午 下午 加班 / 上班 下班). Ela serve de âncora **mesmo em branco**: o
mês escrito à mão não é usado.

- Aviso: `ANCHOR_MONTH_BAND` (`AVISO_ANCORA_FAIXA_MES`); origem
  `FAIXA_MES_MODELO1`.
- Função: `ancoras.avaliar_faixa_mes`.
- Limite: base da caixa 年/月份 + margem pequena (5% da altura da caixa, mínimo
  2 px), ou a linha horizontal logo abaixo da base quando existir. Tudo acima
  (nome, 单位, salário, título, a própria faixa e o que foi escrito nela) fica
  fora do recorte.
- Rótulos aceitos: caixas `年月份`, `年`, `月`, `月份` (o OCR costuma devolver a
  faixa como uma caixa só). Se o OCR errar os rótulos, aceita caixas curtas com
  ideograma entre o título e o cabeçalho das colunas.
- Contexto exigido: título 考勤表 ou rótulos 上班/下班 em algum lugar da imagem.
- Estrutura exigida abaixo do limite: ao menos 2 rótulos de cabeçalho
  (日期, 上午, 下午, 加班, 上班, 下班) e dados (dia/horário). Sem isso a âncora
  não vale e o fluxo segue para o próximo fallback.
- Foto torta: o limite precisa ficar **abaixo da base da faixa e acima do
  centro** dos textos 上午/下午/加班. Se não couber, bloqueia.

| `faixa_mes=` | Significado | Efeito |
|---|---|---|
| `ok` | âncora válida | usa `ANCHOR_MONTH_BAND` |
| `nao_aplicavel` | modelo diferente do 1 | ignora |
| `sem_contexto` | sem 考勤表 nem 上班/下班 | segue para rótulos |
| `nao_encontrada` | sem rótulos 年/月份 (nem caixa curta na região) | segue para rótulos |
| `sem_cabecalho` | faixa achada, mas sem cabeçalho/dados abaixo | segue para rótulos |
| `ambigua` | mais de uma faixa, ou rótulo fora da região da tabela | **bloqueia** (`ANCHOR_NOT_CONFIDENT`) |
| `limite_incerto` | o limite cortaria o cabeçalho das colunas | **bloqueia** (`ANCHOR_NOT_CONFIDENT`) |

Quando a cadeia inteira falha, o diagnóstico do `ErroDeteccao` traz, por
exemplo: `rotulos=ausentes faixa_mes=sem_cabecalho marcos=1 faixas=1
dist_cabecalho_alturas=1.4 rotulos_cabecalho=0 geometria=...`.
`dist_cabecalho_alturas` é a distância, em alturas de célula, entre o limite e o
centro do cabeçalho das colunas. Imagens aprovadas mostram `aviso=ANCHOR_MONTH_BAND`
no log.

### Limite de deriva da geometria (só Modelo 1)

A âncora geométrica rejeita a grade quando o topo das colunas diverge mais que
um limite (em alturas de célula). O limite padrão é **1,5**; no **Modelo 1** é
**2,5** (`_DERIVA_MAX_GRADE_MODELO1`): cabeçalhos mesclados do cartão chinês
fazem as colunas começarem em alturas diferentes mesmo com a foto reta
(observado 1,87 e 1,96 com ângulo 0). Modelo 2 continua em 1,5.

## Âncora resolvida dispensa a segunda validação (Modelos 1, 2 e 3)

Quando o recorte foi decidido por uma âncora do modelo (quinzena, faixa do mês,
`Saída`, rótulos ou geometria), `DeteccaoEstrutural.ancorada` fica `True`. Nos
**Modelos 1, 2 e 3** (`validacao.MODELOS_ANCORA_DISPENSA_CONFLITO`) o cabeçalho
pessoal já foi descartado e tudo abaixo do corte é a tabela de ponto, então
`analisar_sensiveis` **não faz mais a validação de campo sensível dentro da
tabela**: o texto é preservado, sem bloquear (`SENSITIVE_FIELD_CONFLICT`) e sem
redigir.

Motivo: carimbos de horário lidos pelo OCR como números colados (ex.: 11
dígitos) eram tomados por CPF/nome e bloqueavam folhas boas (caso da Alisson,
2° Quinzena). Redigir esses campos apagaria horários.

O que **continua valendo**:

- a sobra de cabeçalho que atravessa a linha de corte é redigida (ou bloqueia se
  encostar em horário/dia);
- `validar_recorte`, `redacao_efetiva`, `validar_saida` e remoção de metadados;
- a prévia humana (aprovar/rejeitar) antes de qualquer envio ao MCP.

Risco aceito: um campo sensível *preenchido à mão* abaixo do corte (por exemplo,
assinatura no rodapé de recibo) passa a seguir para a prévia sem bloqueio
automático. Os Modelos 4 e 5 não têm âncora e seguem a regra anterior; sem
âncora resolvida, vale a regra anterior em todos os modelos.

## Modelo 2: colunas a direita da Saida final

Com a ancora resolvida, `validacao.mascara_colunas_direita_modelo2` cobre de
branco tudo a direita da `Saida` apos o intervalo (Hora Extra e Assinatura).
Nada a esquerda dessa coluna e alterado: todos os horarios ficam. A coluna e
localizada pelos rotulos impressos (`Saida`, `Entrada`, `Hora Extra`) e a
inclinacao e compensada pelas celulas, em faixas de 40 px. Sem rotulos
suficientes, nao mascara (a previa humana segue obrigatoria).

## Modelo 5: corte pelos rotulos e mascara das assinaturas

- **Corte:** logo abaixo da faixa `REGISTRO DE PONTO` (que traz o nome
  manuscrito). Vale quando o layout nao acha tabela e tambem quando acha, mas o
  caminho generico nao acha o inicio. Aviso no log: `LAYOUT_LABELS`.
- **Rotulos tortos:** `detector.rotulo_modelo5` aceita leituras comuns de foto
  escura (`BAIDA`, `ARBINATURA`/`ABBINATURA`, `HORAB`); exige ENTRADA + SAIDA +
  ao menos 3 de ASSINATURA/HORAS.
- **Bordas:** a direita para na ultima coluna HORAS (a pagina vizinha do
  caderno fica fora); a esquerda inclui DIA/MES/N. Sem rodape (o caderno nao tem).
- **Assinaturas:** `validacao.mascara_assinaturas_modelo5` cobre de branco as
  colunas ASSINATURA de ENTRADA e de SAIDA, em faixas de 40 px que seguem a
  coluna HORAS. Caixas que o OCR funde numa linha inteira sao ignoradas no
  rastreio. Com a mascara aplicada, a redacao de nome dentro da tabela e
  dispensada (ela apagava datas e horas fundidas na mesma caixa).

## Detecção de tabela: segunda tentativa de layout (só Modelo 1)

Se a primeira passada do PP-StructureV3 não achar nenhuma tabela, o `modelo-1`
repete a inferência com `layout_threshold=0.3`
(`detector._LAYOUT_THRESHOLD_RETRY`). Só ocorre quando a primeira passada
falhou; imagens que já funcionam não mudam e os demais modelos não são afetados.
Não baixar além de 0,3: com 0,1 o modelo devolveu menos blocos e nenhuma
tabela.

## Testes

`backend/tests/test_correcoes_modelos_ocr.py` (`TestModelo1FaixaDoMes`,
`TestDerivaModelo1`) e `backend/tests/test_anonimizacao_detector.py`
(`TestSegundaTentativaLayout`). Dados sintéticos; fotos reais não são versionadas.
