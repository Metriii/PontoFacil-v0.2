# Processo - Modelo 5 (REGISTRO DE PONTO com duas linhas)

> Documento de apoio do **Modelo 5** do PontoFácil, criado em 2026-10-29 a
> partir do cartão "REGISTRO DE PONTO" que antes era aceito no Modelo 4.

- **Versão:** 1.0
- **Data:** 2026-10-29
- **Status:** implementado e validado com OCR real simulado + imagem de referência
- **Referência visual:**
  `C:\Users\usr-ti02\Pontos\WhatsApp Image 2026-10-01 at 13.57.37 (2).jpeg`
- **Rota:** `/processamento/modelo-5`
- **Modelo no backend:** `modelo-5`

---

## 1. Objetivo

Processar o cartão **REGISTRO DE PONTO**, em que **cada data ocupa duas linhas
físicas**, gerar o Excel consolidado de seis colunas e permitir revisão dos
horários na interface.

O Modelo 5 é o antigo layout A do Modelo 4. A separação deixou cada modelo com
um único layout:

| Modelo | Layout | Formato |
| --- | --- | --- |
| `modelo-4` | `linha-unica` (B) | uma linha por dia, quatro batidas |
| `modelo-5` | `duas-linhas` (A) | duas linhas físicas por data |

O pacote `backend/modelo5/` **reaproveita** o núcleo de `backend/modelo4/`
(parser/mapper) em vez de duplicar o código. A diferença fica no layout forçado,
no perfil de imagem e na validação de layout.

---

## 2. Decisões

- O layout é **forçado** no mapper (`layout="duas-linhas"`), não na detecção.
  Assim, mesmo que uma linha isolada apareça, o Modelo 5 não lista a quinzena
  inteira: lista apenas os dias preenchidos.
- A coluna de data no SQLite/Excel usa **somente o dia** (ex.: `"2"`), como nos
  Modelos 1, 3 e 4.
- **Feriado** aparece no Excel com o rótulo `(Feriado)` e uma observação no
  registro. Como não há horários, o banco não persiste a linha.
- O Excel mantém **seis colunas**:
  `Dia da semana | Entrar | Saída para intervalo | Volta intervalo | Saída | Hora total`.
- O Modelo 5 **recusa** uma imagem cujo cabeçalho seja claramente do layout
  quinzenal (Modelo 4). Cartão ambíguo **não é recusado**: entra com o layout
  forçado do Modelo 5 e marcado para revisão.
- Não houve mudança de schema nem no sincronizador Excel -> SQLite.
- O preparo de imagem é próprio (`backend/modelo5/imagem.py`), em escala de
  cinza, porque a referência é tinta preta sobre papel branco.

---

## 3. Layout - `DIA | MÊS | Nº`

### 3.1 Cabeçalho

- Título: **REGISTRO DE PONTO**.
- Mês manuscrito no topo (ex.: **Setembro**) e número de página (`80`).
- Nome do funcionário manuscrito no topo (na imagem de referência,
  **Cristiano**).
- Colunas: `DIA | MÊS | Nº`.
- `Nº` é o ano com dois dígitos (`26` -> `2026`).
- `ENTRADA` e `SAÍDA` possuem duas subcolunas cada: `ASSINATURA` e `HORAS`.
- Não existe coluna de total; o total é calculado pelo sistema.

### 3.2 Linhas por data

O cartão usa normalmente **duas linhas físicas por data**:

1. primeira linha: `entrada` e `saída para o intervalo`;
2. segunda linha: `volta do intervalo` e `saída final`.

Exemplo da imagem real:

```text
02  09  26  Crusting  08/06  Crusting  12/07
02  09  26  Crusting  13/07  Crusting  17/05
```

O parser também aceita dia curto com apenas uma linha e duas batidas (ex.:
`05/09` com `08:00` e `13:08`), marcando o registro para revisão.

---

## 4. OCR e preparo de imagem

### 4.1 Perfil do Modelo 5 (`backend/modelo5/imagem.py`)

A referência é uma foto de folha branca com caligrafia preta. O preparo:

1. `ImageOps.exif_transpose`;
2. margem branca de 5% do maior lado (mínimo 24 px);
3. ampliação até **1200 px** de largura (máximo 3x);
4. JPEG qualidade 92, mantendo a cor.

Esse é o mesmo perfil que o OCR real da imagem de referência aprovou. A versão
inicial do módulo usava escala de cinza, 2000 px e `UnsharpMask`; nesse perfil o
PaddleOCR devolveu texto solto, sem tabela e sem horários. O perfil atual
devolve a tabela de sete células.

O serviço só injeta esse preparo no fluxo do Modelo 5; os demais modelos não
são afetados. PDFs permanecem intactos.

### 4.2 Validação de layout

`backend/modelo5/parser.py` delega para o núcleo:

- `layout_evidente(grid, markdown=None)`: devolve `linha-unica`,
  `duas-linhas` ou `None`. Examina o grid escolhido **e** as demais tabelas do
  markdown, porque o OCR costuma separar o cabeçalho da tabela de horários.
- `validar_layout(grid, markdown=None)`: recusa o cartão quinzenal
  (`"Imagem com uma linha por dia (Modelo 4); processe-a no Modelo 4."`).

Sem evidência de layout, o cartão **não é recusado**: segue com o layout
forçado do Modelo 5 e revisão.

---

## 5. Parser (`backend/modelo5/parser.py`)

O parser do Modelo 5 delega ao núcleo compartilhado, fixando
`LAYOUT = LAYOUT_DUAS_LINHAS` em `referencia`. Funções expostas:

- `selecionar_grid(markdown)`: escolhe a tabela com mais linhas de data.
- `nome_colaborador(grid, texto)`: nome manuscrito no topo; pode vir com erros
  do OCR.
- `referencia(grid, texto)`: período do cartão (menor/maior data reconhecida ou
  período explícito), forçando o layout de duas linhas.
- `linhas_dia(grid)`: mantém linhas com data, horários ou anotação.
- `diagnostico(grid, texto)`: resumo para o log de falha.
- `avisos_estrutura(grid)`: problemas de alinhamento que exigem conferência.
- `layout_evidente` / `validar_layout`: guardas de layout.
- Reexporta `preencher_datas`, `corrigir_datas_apos_feriado` e `separar_linha`
  para uso direto e em testes.

Regras herdadas do núcleo (ver `docs/PROCESSO_MODELO_4.md` §5):

- data em células numéricas ou data compacta;
- ano com dois dígitos e descarte de anos fora de `20..40`;
- horários com `:`, `/`, `.` ou `h`;
- `ÀS`/`A5`/`AS` como separador e remoção de duplicata exata;
- reparação de hora inválida (`92:00` -> `22:00`) somente se a sequência fechar;
- `preencher_datas` para linhas de continuação;
- `corrigir_datas_apos_feriado` para o dia repetido após feriado;
- anotações (`Feriado`, `Falta`, `Férias`, `Folga`, `Atestado`, `Licença`).

---

## 6. Mapper (`backend/modelo5/mapper.py`)

- `mapear_folha(...)` delega a `backend.modelo4.mapper.mapear_folha` passando
  `layout="duas-linhas"`.
- Continua exportando `map_registro` e `registro_tem_conteudo` do núcleo.
- Agrupa as duas linhas físicas pela data completa e distribui as batidas em
  `entrada | saída_almoco | volta_almoco | saída`.
- Dia curto com uma linha e duas batidas usa a heurística de `_duas_batidas` e
  fica com `revisar=True`.
- O Excel lista **somente os dias preenchidos** (diferente do Modelo 4, que
  lista toda a quinzena).
- `data` do registro recebe somente o dia (`"2"`).
- `dia_semana` é calculado pelo calendário a partir da data completa.
- `hora_total` desconta o intervalo e soma 24h na virada de meia-noite (bloco
  de até 8h), marcando revisão.
- Registros com menos de quatro batidas ficam com `revisar=True`.
- Feriados ficam com `confianca=0.0`, `revisar=True` e observação `Feriado`.
- Se o mês/ano não for lido, o registro não é descartado: entra no Excel sem
  dia da semana, com `revisar=True` e observação de conferência.

---

## 7. Serviço, rota e Excel

- `backend/config.py`: `modelo-5` em `MODELOS_VALIDOS`.
- `backend/servico.py`:
  - `_Fluxo.validar_layout` (novo hook) é chamado com `(grid, markdown)` logo
    após `selecionar_grid`;
  - `_FLUXO_MODELO4` usa `mapear_folha_linha_unica` + guard;
  - `_FLUXO_MODELO5` usa `modelo5_parser`, `modelo5_mapper` e
    `modelo5_imagem`;
  - `processar_folhas_modelo5(imagens, lote)` executa o pipeline.
- `backend/rotas/processar.py`: despacha `modelo-5` no `POST /processar`.
- `backend/excel.py`: mesmo cabeçalho de seis colunas dos Modelos 1, 3 e 4.
- Importação manual aceita `--modelo modelo-5`.
- O sincronizador Excel -> SQLite não precisou de alteração.

---

## 8. Frontend

- Página: `PontoFacil/src/routes/processamento/modelo-5.tsx`.
- Rota: `/processamento/modelo-5`.
- Chamada: `processarCartoes(..., "modelo-5")`.
- Card: `PontoFacil/src/components/ponto/ModelCards.tsx`, com a imagem
  `PontoFacil/src/assets/modelo5.jpg` (a antiga foto do Modelo 4).
- Menu lateral: link `Modelo 5`.
- Tipos `ModeloId` e `ModelId` incluem `modelo-5`.
- `routeTree.gen.ts` é regenerado pelo Vite.

---

## 9. Validação

### 9.1 Testes automatizados

```bash
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -t . -q
```

Resultado: **267 testes OK** (Modelos 4 e 5 juntos).

```bash
cd PontoFacil
npm test
```

Resultado: **15 testes OK**.

```bash
npm run build
```

Resultado: build de produção concluído, incluindo `modelo-5-*.js` e
`modelo5-*.jpg`.

`backend/tests/test_modelo5.py` cobre:

- parser (grid, nome, referência, feriado, datas repetidas, herança de data);
- `layout_evidente` e `validar_layout` (próprio layout, layout do outro modelo
  e cabeçalho ambíguo);
- mapper (duas linhas por data, dia curto, feriado, correção após feriado,
  seis colunas, apenas dias preenchidos, layout forçado);
- preparo de imagem (cinza + ampliação, PDF intacto);
- pipeline (preparo injetado, recusa do cartão quinzenal);
- endpoint `/processar` persistindo com `modelo = "modelo-5"`.

### 9.2 Saída real validada (layout de duas linhas)

Validação de 2026-10-29 com a imagem de referência, chamando o MCP do
PaddleOCR com o preparo do Modelo 5 (sem persistir no banco):

- layout detectado: `duas-linhas`;
- nome lido: `Crist�rio` (caligrafia; o nome da aba continua vindo do arquivo);
- 21 registros e 21 linhas de Excel;
- dia `02`: `08:06 | 12:07 | 13:07 | 17:05` (o `12:07` é leitura do OCR para
  o `12:02` da referência);
- feriados nos dias `07` e `16`;
- dias `08` e `17` corrigidos após o feriado, com revisão;
- dias curtos `05`, `12` e `19` com duas batidas e revisão.

O OCR errou o mês/ano de duas linhas nessa execução (`14/01/2026` em vez de
`14/09/2026` e `23/09/2028` em vez de `23/09/2026`), o que separou os dias `14`
e `23` em dois registros. Os dígitos lidos são preservados e o caso depende de
conferência humana, como nos demais modelos.

Com a imagem inteira e o preparo próprio, o OCR entrega linhas de sete células:

```text
['02', '09', '26', 'Crusting', '08/06', 'Crusting', '12/07']
['02', '09', '26', 'Crusting', '13/07', 'Crusting', '17/05']
['07', '09', '26', 'Fructo', '', 'fructo', '']
```

As assinaturas (`Crusting`, `Estrutivo`, `Fernado`) são ignoradas. O nome
manuscrito no topo pode sair como `Gritua`, `Pristine` ou `Crist�rio`; nesse
caso o sistema usa o texto lido e o nome da aba continua vindo do arquivo
enviado.

### 9.3 Separação dos Modelos 4 e 5 (29/10/2026)

- O Modelo 4 passou a aceitar somente o cartão quinzenal (`alexandre.jpg`).
- O Modelo 5 recebeu o cartão de duas linhas
  (`WhatsApp Image 2026-10-01 at 13.57.37 (2).jpeg`).
- O cartão do Modelo 5 é recusado no Modelo 4 e vice-versa, sempre com aviso
  explícito e markdown bruto em `logs/ocr/`.
- Cartões com cabeçalho ambíguo não são recusados: entram com revisão.
- As imagens dos cards na home foram trocadas:
  `modelo4.jpg` = `alexandre.jpg`; `modelo5.jpg` = antiga `modelo4.jpg`.

---

## 10. Limitações conhecidas

- O nome manuscrito pode não ser lido; o nome da aba do Excel e o
  `nome_funcionario` no banco continuam vindo do nome do arquivo enviado.
- Se o OCR perder uma data no meio do cartão, a data é herdada da linha
  anterior e o registro é marcado para revisão.
- A correção de data após feriado é heurística e sempre marca revisão.
- A reparação de hora inválida depende de a sequência fechar; quando não fecha,
  a batida é descartada e o dia fica com revisão.
- A virada de meia-noite só é aceita para o último bloco, com até 8 horas.
- Se o mês/ano manuscrito não for legível, o cartão entra com revisão e sem dia
  da semana.
- Dígitos manuscritos podem ser trocados pelo OCR; o parser mantém o valor lido
  e marca revisão quando a sequência fica incoerente.
- O banco continua guardando apenas o dia; dias iguais em meses diferentes
  colidem, como nos demais modelos.

---

## Correções de leitura e pareamento (2026-11-13)

Com a imagem anonimizada (assinaturas mascaradas, título cortado), o OCR do
caderno passou a errar de formas previsíveis. O parser do Modelo 5
(`backend/modelo5/parser.py`) corrige, antes do núcleo compartilhado:

| Leitura do OCR | Correção |
|---|---|
| `D2`, `O3`, `D10` nas colunas de data | `02`, `03`, `10` |
| `DIA`/`MÊS` na mesma linha de horários (cabeçalho fundido) | vira linha de dados sem data |
| `08%` + `00` (traço da coluna HORAS entre hora e minuto) | `08:00` |
| `08800` (dois-pontos manuscrito lido como `8`) | `08:00`, só se for horário válido; dia marcado para revisão |
| anos diferentes na coluna Nº (`26`, `10`, `20`, `16`) | ano mais frequente |

**Pareamento manhã/tarde** (`parear_turnos`, usado por `mapper.mapear_folha`):
cada data tem a 1ª linha = entrada e saída da manhã e a 2ª = volta e saída da
tarde. As linhas são agrupadas pela data lida quando ela bate; senão, pela
ordem e pelo turno (última batida a partir de 14:30 = tarde). A data de cada
dia é a menor data lida maior que a do dia anterior, o que corrige a coluna de
datas deslocada pelo OCR. Data ajustada recebe a observação "Data ajustada pela
ordem das linhas do caderno" e fica para revisão. Linhas sem horário (feriado)
mantêm a data lida.

Limite: quando o OCR lê o próprio horário errado (`12:02` como `08:02`), o
parser não adivinha; o dia sai com alerta ("Jornada acima de 10 horas") para
correção na revisão.
