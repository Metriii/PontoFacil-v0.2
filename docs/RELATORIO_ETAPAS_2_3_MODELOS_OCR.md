# Relatório — Etapas 2 e 3 (correções dos modelos de cartão de ponto)

**Plano:** `docs/PLANO_CORRECOES_MODELOS_OCR.md`
**Diagnóstico base:** `docs/DIAGNOSTICO_ETAPA1_MODELOS_OCR.md`
**Escopo:** Modelo azul (1), padrão (2) e simples (4). Modelo verde (3) não foi alterado.

> **Estado honesto:** a lógica está implementada e coberta por testes
> **sintéticos**. Nenhuma foto real da variação B do Modelo 1, das fotos que
> falharam do Modelo 2 ou das imagens bloqueadas do Modelo 4 estava disponível,
> e o PP-StructureV3 não foi executado nesta sessão. Portanto nada aqui prova o
> comportamento em fotografias reais. Ver §8.

---

## 1. Diagnóstico e causa por modelo

| Modelo | Causa identificada | Onde |
|---|---|---|
| 1 — Azul (variação B) | Sem texto de quinzena, `_ancora_faixa` levanta `ANCHOR_NOT_FOUND`; o único fallback existente depende de rótulos de coluna em português, que a área com caracteres chineses não fornece | `ancoras._ancora_faixa`, `detector._resolver_inicio` |
| 2 — Padrão | `ancora_modelo2` exige `{dia, entrada, saída}` e pelo menos 5 rótulos distintos; foto menos nítida perde rótulos e a âncora levanta `ANCHOR_NOT_CONFIDENT` sem nenhuma alternativa | `ancoras.ancora_modelo2` |
| 4 — Simples | `_mesma_coluna` não tem limite vertical: qualquer campo sensível que se sobreponha horizontalmente a **qualquer** horário da tabela bloqueava a folha, mesmo a centenas de pixels de distância | `validacao.analisar_sensiveis` |

## 2. Arquivos modificados

| Arquivo | Alteração |
|---|---|
| `backend/anonimizacao/ancoras.py` | `ErroAncora.recuperavel_por_geometria`; função `ancora_geometrica` (grade de células) |
| `backend/anonimizacao/detector.py` | `_resolver_inicio` consulta a geometria só para `modelo-1` e `modelo-2` |
| `backend/anonimizacao/validacao.py` | conflito geométrico local para `modelo-4`; `_mascara_atinge_dados` |
| `backend/anonimizacao/service.py` | repassa `modelo` a `analisar_sensiveis` |
| `backend/tests/test_correcoes_modelos_ocr.py` | 29 testes novos (matriz AZ/PD/VD/SI) |

## 3. Âncoras e alternativas implementadas

Ordem de identificação:

| Modelo | 1ª tentativa | 2ª tentativa | 3ª tentativa |
|---|---|---|---|
| 1 | quinzena (inalterada) | rótulos de coluna (inalterada, `ANCHOR_FALLBACK`) | **grade de células** (`ANCHOR_GEOMETRIC`) |
| 2 | texto `Saída` + rótulos (inalterada) | **grade de células**, só se a falha for de leitura de texto | bloqueia |
| 3 | quinzena (inalterada) | rótulos (inalterada) | bloqueia (sem geometria) |

**Âncora geométrica** (`ancoras.ancora_geometrica`):

- Agrupa células por centro horizontal (tolera inclinação leve) e busca, em cada
  coluna, a maior pilha de células sem vão.
- Exige ≥ 6 linhas em ≥ 4 colunas, cobrindo ≥ 60 % da largura da tabela e pelo
  menos 3 textos de horário/dia dentro da grade.
- Rejeita a grade se o topo das colunas diverge mais de 1,5 altura de célula
  (inclinação ou perspectiva fortes demais para confiar).
- Todos os limites são relativos à altura/largura medianas das células; nenhuma
  coordenada fixa em pixels.
- Modelo 2: sobe sobre faixas formadas **somente** por rótulos impressos
  (`Dia`, `Entrada`, `Saída`...), no máximo 3 faixas; qualquer outro texto
  interrompe a subida.
- Modelos 3, 4 e 5 retornam sempre `None`.

**Falhas do Modelo 2 que continuam bloqueando** (não recuperáveis): saída sem
registros abaixo e topo do cabeçalho avançando sobre os registros.

## 4. Anonimização (Modelo 4)

Antes, para qualquer campo sensível na tabela: bloqueio se `_mesma_linha` **ou**
`_mesma_coluna` com um horário. Agora, **somente para `modelo-4`**, bloqueia se:

1. o campo está na mesma linha de um horário (regra mantida); ou
2. a máscara do campo (caixa + 4 px: 2 px de padding da redação + 2 px de
   folga do OCR) intersecta algum horário/dia.

Sobreposição apenas de coluna deixa de bloquear, porque a redação pinta só a
caixa do campo, nunca uma coluna inteira. Mantido:

- texto misto como `Assinatura 08:00` nunca é mascarado (bloqueia);
- o valor adjacente a um rótulo, que também é pintado, passa pela mesma checagem;
- regiões extras marcadas pelo usuário continuam cobrindo conflitos;
- `redacao_efetiva`, `validar_recorte` e `validar_saida` continuam rodando depois;
- `SENSITIVE_FIELD_CONFLICT` não foi desativado nem ganhou exceção.

Modelos 1, 2, 3 e 5 continuam com a regra original (testado). Em particular, o
caso CAMILA do Modelo 2, que o diagnóstico classificou como conflito real,
**continua bloqueado**.

## 5. Pré-processamento

**Nenhuma transformação nova.** Conforme o diagnóstico (§6), o detector recebe a
imagem original e o pré-processamento só ocorre depois da aprovação, sobre o
recorte já anonimizado; por isso não corrige falha de âncora. Correção de
inclinação/perspectiva e ajustes de contraste **antes da detecção** (itens 5–8
do Modelo 2 no plano) **não foram implementados** (ver §8).

## 6. Testes

Suíte completa: **438 testes** (409 anteriores + 29 novos), 3 pulados (opt-in de
imagens reais), **1 falha que já existia** e não tem relação com este trabalho:
`test_atualizacao...test_nome_da_aba_usa_arquivo_enviado_e_nao_o_nome_do_ocr`
(compara caminho no estilo Windows `C:\fotos\...` e roda em Linux).

Matriz do plano:

| ID | Resultado | Observação |
|---|---|---|
| AZ-01 | aprovado (sintético) | quinzena presente → sem aviso, sem geometria |
| AZ-02 | aprovado (sintético) | topo na grade, área chinesa fica fora |
| AZ-03 | aprovado (sintético) | deriva de 0,9 altura aceita; 3 alturas bloqueia |
| AZ-04 | aprovado (sintético) | sem células, sem horários ou sem grade → `ANCHOR_NOT_FOUND` |
| PD-01 | aprovado (sintético) | âncora textual completa não usa a grade |
| PD-02 | aprovado (sintético) | cabeçalho incompleto → grade + rótulos |
| PD-03 | **pendente** | correção de perspectiva antes da detecção não implementada |
| PD-04 | aprovado (sintético) | sem estrutura → `ANCHOR_NOT_CONFIDENT`; nada é inventado |
| VD-01 | aprovado (sintético) | verde com quinzena igual a antes |
| VD-02 | aprovado (sintético) | sem geometria, fallback de rótulos inalterado, regra de conflito original |
| SI-01 | aprovado (sintético) | falso conflito de coluna → redigido localmente |
| SI-02 | aprovado (sintético) | sobreposição real e mesma linha → bloqueio |
| SI-03 | aprovado (sintético) | sem tabela/cabeçalho → bloqueio |
| SI-04 | aprovado (sintético) | artefato gerado; campo branco, horário preservado |
| SI-05 | aprovado (sintético) | máscara com folga sobre horário e texto misto reprovados |

Testes opt-in com imagens reais (ANA/Vanessa/CAMILA) **não foram executados**:
as imagens não estão no repositório.

## 7. Preservação do modelo verde

- `_ancora_faixa`, `ancora_modelo3` e o fallback de rótulos do `modelo-3` não
  foram tocados; `ancora_geometrica("modelo-3", ...)` retorna `None`.
- O ramo de `_resolver_inicio` para `modelo-3` tem o mesmo resultado de antes
  (verificado por testes VD-01/VD-02).
- `analisar_sensiveis` só usa a regra nova quando `modelo == "modelo-4"`.
- **Pendente:** rodar ANA e Vanessa reais como `modelo-3` (Etapa 4 do plano).

## 8. Pendências e limitações

1. **Sem validação em fotos reais.** Faltam amostras da variação B do Modelo 1,
   das fotos que falharam do Modelo 2 e das 4 imagens bloqueadas do Modelo 4
   (`id=89a321af...`). Rodar o PP-StructureV3 sobre elas é o passo que fecha as
   Etapas 2 e 3.
2. **Depende das células do PP-StructureV3.** A âncora geométrica usa
   `cell_box_list`. Se o detector não devolver células para uma foto, a imagem
   continua bloqueada (por desenho: não há recorte arbitrário).
3. **Pré-processamento antes da detecção** (deskew, perspectiva, contraste,
   reescala) não implementado. Exigiria reexecutar o detector (130–200 s por
   imagem) e comparação com fotos reais para não degradar o manuscrito.
4. **Caracteres chineses classificam como `permitido`** em
   `classificar_texto` (não há letras `a–z`). Hoje a área administrativa da
   variação B fica fora porque o recorte começa na grade; se o topo da tabela
   detectada já estiver abaixo dela, nada muda. Se um dia essa área ficar
   dentro do recorte, ela não seria redigida. Vale tratar na Etapa 4.
5. **Variação B pode perder rótulos de coluna** no Modelo 1 se a linha de
   rótulos não formar parte da grade de células; o topo é sempre conservador
   (não sobe sobre texto desconhecido), por privacidade.
6. Os limites (6 linhas, 4 colunas, 60 %, 1,5 altura, 4 px) são estimativas
   sem calibração em fotos reais.

---

## 9. Diagnóstico em log (adicionado depois, sem alterar decisões)

Os primeiros lotes reais mostraram que o log só dizia o código da falha, sem o
modelo nem o motivo. Foi adicionado diagnóstico para separar as hipóteses
(código antigo rodando, geometria sem confiança, conflito falso ou real).
**Nenhuma regra de decisão mudou**: a suíte completa se comporta como antes.

Formato das linhas (`logs/`):

```text
Imagem bloqueada (id=..., modelo=modelo-4): SENSITIVE_FIELD_CONFLICT | <motivo> | regra=localizada conflitos=1 tipos=mascara
Imagem bloqueada (id=..., modelo=modelo-1): ANCHOR_NOT_FOUND | <motivo> | rotulos=ausentes geometria=poucas_celulas celulas=0
Imagem bloqueada (id=..., modelo=modelo-2): ANCHOR_NOT_CONFIDENT | <motivo> | geometria=deriva_alta celulas=96 colunas=8 colunas_grade=8 deriva_alturas=1.74
Imagem bloqueada (id=..., modelo=modelo-2): ANCHOR_NOT_CONFIDENT | <motivo> | geometria=nao_tentada(falha_nao_recuperavel)
Imagem bloqueada (id=..., modelo=...): TABLE_NOT_DETECTED | <motivo> | caixas_texto=NN
Imagem aprovada em XX.XXs (id=..., modelo=modelo-1, aviso=ANCHOR_GEOMETRIC, N bytes)
```

Como ler:

| Campo | Significado |
|---|---|
| `geometria=nao_tentada(...)` | a falha da âncora textual não é recuperável por geometria (ambiguidade) |
| `geometria=nao_aplicavel` | modelo sem geometria (3, 4, 5) |
| `geometria=poucas_celulas` | o detector não devolveu células suficientes (< 24) |
| `geometria=grade_curta` | menos de 4 colunas com 6 ou mais linhas empilhadas |
| `geometria=deriva_alta` | topo das colunas diverge mais de 1,5 altura (inclinação/perspectiva) |
| `geometria=cobertura_baixa` | a grade cobre menos de 60 % da largura da tabela |
| `geometria=poucos_dados` | menos de 3 horários/dias dentro da grade |
| `tipos=mesma_linha\|coluna\|mascara\|mascara_vizinha\|sobra_cabecalho` | por que o campo sensível conflitou |
| `regra=original\|localizada` | qual regra de conflito foi usada (`localizada` = Modelo 4) |
| `aviso=ANCHOR_GEOMETRIC` | a imagem aprovada usou a âncora geométrica |

Privacidade: o diagnóstico contém apenas rótulos fixos e números agregados
(contagens, razões). **Nunca** texto lido da imagem; um teste garante que nome,
CPF e empresa do cabeçalho não aparecem no log. Todas as mensagens de erro já
eram texto fixo, contagem de páginas ou nome da classe da exceção.

Testes: +12 (total 450 na suíte; 1 falha pré-existente de caminho Windows).

## 10. Ajustes após os logs reais (lotes de 11/11/2026)

Os logs com diagnóstico mostraram três causas distintas nos lotes reais:

| Log | Causa | Ajuste |
| --- | --- | --- |
| Modelo 2, `SENSITIVE_FIELD_CONFLICT regra=original tipos=coluna` | Falso conflito: rótulo sensível na mesma coluna de um horário, sem estar na linha dele nem tocar nenhuma caixa de hora/dia | `MODELOS_MASCARA_LOCALIZADA` passa a incluir `modelo-1` e `modelo-2` (Modelo 3 segue a regra original) |
| Modelo 1, `ANCHOR_NOT_FOUND geometria=grade_curta celulas=140 colunas=3 colunas_grade=0` | Colunas fundidas pelo agrupamento (comparação com a última célula) e/ou foto torta | Agrupamento compara com o centro médio da coluna; `_melhor_grade` testa ângulos de -6° a +6° e três folgas e escolhe o que cobre mais células em colunas de grade (empate: menor ângulo, ou seja, foto reta nunca é girada) |
| Modelo 1, `TABLE_NOT_DETECTED caixas_texto=62` | Detector não reconhece a tabela (antes das âncoras) | Sem alteração; fica para o pré-processamento (PD-03) |

- A âncora devolvida continua em coordenadas da imagem original (topo mínimo das células da grade).
- O diagnóstico `geometria=...` agora inclui `altura_cel`, `larg_q25`, `angulo` e `folga`.
- Correção da hipótese anterior: "FERIADO" é classificado como permitido; o conflito `mesma_linha` do cartão da Alisson vem de outro texto (provavelmente "Total de Desconto" ou "Assinatura do empregado"). O novo log dirá qual tipo restou.
- Testes: `TestGeometriaTolerante` e `TestMascaraLocalizadaModelos1E2` (11 novos); o teste de regra original agora vale para os modelos 3 e 5 e `None`.
