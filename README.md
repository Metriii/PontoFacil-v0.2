# OCR-PontoFacil

Leitura de uma **folha de ponto** em papel (imagem/PDF) e geração de uma planilha
Excel, usando o **MCP do PaddleOCR** (modelo `PaddleOCR-VL-1.6` via AI Studio).

## Como funciona

1. `backend/modelo1/ler_imagem.py` inicia o servidor MCP do PaddleOCR
   (`uvx --from paddleocr-mcp`), envia a imagem e salva o resultado em
   `resultado_ocr.json` (markdown + tabelas).
2. `backend/modelo1/gerar_excel.py` lê esse JSON, extrai da tabela as colunas
   **Dia da semana, Entrar, Saída para intervalo, Volta intervalo, Saída e Hora total**
   e gera `folha_presenca.xlsx`.

> Esses dois scripts são **exclusivos do Modelo 1**
> (`/processamento/modelo-1`); os modelos 2, 3, 4 e 5 têm módulos próprios em
> `backend/modelo2/`, `backend/modelo3/`, `backend/modelo4/` e `backend/modelo5/`.
> Regra do Modelo 1: entrada lida entre 20h e 23h vira 08h
> preservando os minutos (`20:30` → `08:30`).

## Anonimização local (obrigatória)

Antes de qualquer chamada ao MCP/OCR externo, toda imagem ou PDF passa pela
camada local `backend/anonimizacao/`:

1. o PDF (quando houver) é rasterizado localmente;
2. o **PP-StructureV3** roda localmente para achar a tabela de ponto;
3. o cabeçalho inteiro é removido por recorte — não é tarja nem desfoque;
4. nomes, CPF, matrícula, empresa e assinaturas dentro da tabela são removidos
   quando a remoção é segura; em caso de dúvida, a imagem é **bloqueada**;
5. a saída é regravada como PNG sem EXIF e com identificador opaco;
6. somente o `ArtefatoAnonimizado` aprovado chega ao MCP. O cliente MCP recusa
   qualquer outro tipo de bytes.

O modo é **fail closed**: se a tabela/cabeçalho não puderem ser separados com
segurança, o arquivo não segue para o OCR externo. A imagem original nunca é
usada como fallback.

### O que é ocultado em cada modelo (v0.2)

O recorte começa numa **âncora** própria de cada modelo. Tudo acima dela
(empresa, nome, CPF, título) é descartado. As colunas de assinatura são
cobertas de branco.

| Modelo | Âncora do recorte (e alternativas) | Máscara extra |
|---|---|---|
| 1 – Listra Azul | faixa `1ª/2ª Quinzena` → faixa do mês `年/月份` do cartão chinês (`ANCHOR_MONTH_BAND`) → rótulos de coluna (`ANCHOR_FALLBACK`) → geometria da grade (`ANCHOR_GEOMETRIC`) | — |
| 2 – Padrão | linha com `Saída` → geometria → rótulos de coluna (`ANCHOR_FALLBACK`) | colunas **Hora Extra** e **Assinatura**, à direita da Saída final |
| 3 – Listra Verde | faixa `1ª/2ª Quinzena` → rótulos de coluna | — |
| 4 – Simples | caminho genérico | — |
| 5 – Caderno | logo abaixo da faixa `REGISTRO DE PONTO`, pelos rótulos impressos (`LAYOUT_LABELS`) | colunas **ASSINATURA** de entrada e de saída |

Outras regras desta versão:

- **Modelo 1:** se a primeira passada não acha a tabela, o layout é refeito com
  `layout_threshold=0.3`. O limite de deriva da geometria é 2,5 (nos demais, 1,5).
- **Modelos 1, 2 e 3:** com o corte decidido por uma âncora, a segunda
  validação de campo sensível dentro da tabela é dispensada. Carimbos de
  horário lidos como números longos bloqueavam folhas boas.
- **Modelo 5:** a tabela é derivada dos rótulos `ENTRADA`/`SAÍDA`/`ASSINATURA`/`HORAS`,
  inclusive lidos tortos (`BAIDA`, `ARBINATURA`, `HORAB`). A borda direita
  para na última coluna `HORAS`, o que deixa de fora a página vizinha do caderno.
- O aviso da âncora aparece no log (`aviso=...`) e o motivo de cada bloqueio
  em `diag=...`.
- A prévia humana (aprovar/rejeitar) continua obrigatória antes do MCP.

Detalhes em [`docs/ANCORAS_POR_MODELO.md`](docs/ANCORAS_POR_MODELO.md).

### Leitura do Modelo 5 (caderno, duas linhas por data)

Cada data ocupa duas linhas: a **1ª** traz a entrada e a saída da **manhã**,
e a **2ª** a volta e a saída da **tarde**. O parser (`backend/modelo5/`) pareia
as linhas pela data lida e, quando ela falha, pela ordem e pelo turno: a última
batida a partir de 14:30 é tarde. Também corrige leituras típicas do OCR no
caderno:

| OCR leu | Vira |
|---|---|
| `D2`, `D10` (zero lido como letra) | `02`, `10` |
| `08%` + `00` (traço da coluna HORAS) | `08:00` |
| `08800` (dois-pontos lido como `8`) | `08:00`, marcado para revisão |
| anos diferentes na coluna Nº | ano mais frequente |
| datas deslocadas uma linha | corrigidas pela ordem, marcadas para revisão |

Quando o OCR troca um dígito do horário, o parser não adivinha: o dia sai
sinalizado para revisão. Foto escura ou desfocada continua sendo recusada.
Mais em [`docs/PROCESSO_MODELO_5.md`](docs/PROCESSO_MODELO_5.md).

A resposta de `/processar` ganhou o campo compatível
`bloqueadas_anonimizacao`, com a quantidade de arquivos barrados nessa etapa.
Os demais campos e o contrato do frontend foram preservados.

### Instalação do ambiente de anonimização

O processo do backend precisa do Paddle no **mesmo ambiente Python** que executa
a API. Em uma instalação normal:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r backend/requirements.txt
pip install -r backend/requirements-anonimizacao.txt
```

O `.venv-anon` é um ambiente separado usado apenas para PoC, calibração e
inspeção do PP-StructureV3:

```powershell
python -m venv .venv-anon
.\.venv-anon\Scripts\Activate.ps1
pip install -r backend/requirements-anonimizacao.txt
```

Versões fixadas e detalhes de hardware, tempos, códigos de bloqueio, limites de
PDF e retenção estão em
[`docs/ANONIMIZACAO_LOCAL.md`](docs/ANONIMIZACAO_LOCAL.md).

## Requisitos

- Python 3.10+ (recomendado 3.12)
- [uv](https://docs.astral.sh/uv/) (fornece o `uvx`)

## Instalação

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install uv
```

## Token do AI Studio

Gere um access token em https://aistudio.baidu.com e coloque em um arquivo
`token.txt` na raiz (não versionado), ou defina a variável de ambiente
`PADDLEOCR_MCP_AISTUDIO_ACCESS_TOKEN`.

## Acesso e usuários

Não há cadastro público. As contas são provisionadas pela CLI, que pede a
senha no prompt (nunca em argumento de shell). A senha deve ter pelo menos 8
caracteres:

```bash
.\.venv\Scripts\python.exe -m backend.usuarios criar --email pessoa@empresa.com --nome "Pessoa"
.\.venv\Scripts\python.exe -m backend.usuarios senha --email pessoa@empresa.com
.\.venv\Scripts\python.exe -m backend.usuarios desativar --email pessoa@empresa.com
.\.venv\Scripts\python.exe -m backend.usuarios listar
```

O login tem duas etapas: senha + código de 6 dígitos enviado por e-mail. No
modo `dev`, se `PONTOFACIL_EMAIL_MODO=arquivo`, o e-mail é gravado em
`data/emails/` (útil para testar sem SMTP). Em produção o envio é sempre por
SMTP real e o startup recusa configurações inseguras.

Variáveis principais (ver `.env.example`):

| Variável | Padrão |
|---|---|
| `PONTOFACIL_AMBIENTE` | `dev` (`producao` valida SMTP, segredo e cookie Secure) |
| `PONTOFACIL_SEGREDO_SESSAO` | vazio em dev (efêmero); obrigatório em produção |
| `PONTOFACIL_EMAIL_MODO` | `arquivo` em dev, `smtp` em produção |
| `PONTOFACIL_SMTP_HOST` / `_PORTA` / `_SEGURANCA` | vazio / `587` / `starttls` |
| `PONTOFACIL_SMTP_USUARIO` / `_SENHA` / `_REMETENTE` | vazio |
| `PONTOFACIL_CORS_ORIGINS` | `*` em dev; obrigatório listar a origem em produção |
| `PONTOFACIL_COOKIE_SECURE` | `1` em produção, `0` em dev |

## Processamento, aviso de OCR e idempotência

- Ao clicar em **Processar** em qualquer modelo, o frontend abre o aviso
  obrigatório sobre as limitações do OCR. **Cancelar** apenas fecha o diálogo:
  nenhuma requisição é enviada. Só **Estou ciente, continuar** inicia o OCR.
- O backend também exige `aceite_ocr=true` no `POST /processar`; sem isso a
  requisição é recusada com 400 e o OCR não começa.
- Cada tentativa envia `Idempotency-Key` (UUID). O backend reserva a chave antes
  do OCR e devolve **409** com `processing_id` se a mesma chave já estiver em
  andamento ou concluída. Assim, duplo clique, retry de rede ou duas abas não
  geram processamento duplicado.
- Uma chave cujo processamento falhou pode ser reutilizada para nova tentativa.

## Auditoria

Os eventos são gravados no servidor na tabela `auditoria` do SQLite, sempre com
o e-mail da sessão autenticada (nunca com dado enviado pelo cliente), timestamp
UTC, ação, modelo, `processing_id`, status e resumo de erro sem tokens/senhas.

- `auth_login`, `auth_codigo` e `auth_logout`: eventos de autenticação.
- `ocr_aviso`: confirmação do aviso, gravada **antes** do OCR.
- `ocr_processing`: `started`, `completed` ou `failed` do processamento.

A confirmação e o início do OCR são auditorias obrigatórias: se não puderem ser
gravadas, o processamento não começa. A consulta fica em `GET /auditoria`
(protegida por sessão), com filtros `limite`, `offset`, `email`, `action` e
`processing_id`.

## Pré-visualização de planilhas

O botão **Visualizar** no histórico abre uma prévia somente leitura no
navegador (`GET /planilhas/{arquivo}/previa`), sem abrir o Excel local. A aba
oculta `_PontoFacil` não é exposta e a resposta é limitada a 200 linhas, 50
colunas e 20 abas por arquivo.

## Banco de dados e logs

- **SQLite:** `data/pontofacil.db` (criado automaticamente na inicialização).
- **Logs diários:** `logs/YYYY-MM-DD.txt` (UTF-8, uma entrada por linha).
- Esses diretórios não são versionados. Para fazer backup, pare o backend e
  copie `data/pontofacil.db`; para restaurar, substitua o arquivo com o
  backend parado. Os logs podem ser copiados separadamente.

Variáveis opcionais:

| Variável | Padrão |
|---|---|
| `PONTOFACIL_DATA_DIR` | `data/` |
| `PONTOFACIL_DB_PATH` | `data/pontofacil.db` |
| `PONTOFACIL_LOG_DIR` | `logs/` |
| `PONTOFACIL_TIMEZONE` | `America/Sao_Paulo` |
| `PONTOFACIL_SYNC_ENABLED` | `1` |
| `PONTOFACIL_SYNC_DIRS` | vazio (pastas extras separadas por `;`) |
| `PONTOFACIL_DOWNLOADS_DIR` | Downloads do Windows |
| `PONTOFACIL_SYNC_DEBOUNCE` | `2.0` segundos |
| `PONTOFACIL_SYNC_TENTATIVAS` | `3` |
| `PONTOFACIL_SYNC_ESPERA` | `0.5` segundos |

## Sincronização automática do Excel

O backend monitora `backend/_out`, a pasta Downloads e as pastas extras de
`PONTOFACIL_SYNC_DIRS`. Quando uma planilha gerada pelo PontoFácil é salva, a
sincronização localiza o registro por modelo + funcionário + dia e atualiza a
própria linha no SQLite.

- A planilha precisa ter a aba muito oculta `_PontoFacil`, criada
  automaticamente nos novos arquivos.
- Alterar um horário atualiza a linha existente, sem duplicar.
- Apagar uma célula em uma linha parcialmente preenchida grava `NULL`.
- Uma linha com os quatro horários vazios é ignorada e não apaga o banco.
- Horário inválido ou fórmula nas colunas de horário é rejeitado com aviso.
- Apagar uma linha inteira no Excel não exclui o registro do banco.
- O watcher não faz varredura inicial; arquivos editados com o backend parado
  precisam ser salvos novamente ou importados manualmente.
- Planilhas antigas, sem metadados, devem ser importadas manualmente:

```bash
.\.venv\Scripts\python.exe -m backend.importar_excel caminho\consolidado.xlsx
```

A importação manual também atualiza linhas existentes e mostra os contadores
`Lidos`, `Inseridos`, `Atualizados`, `Inalterados`, `Vazios` e `Invalidos`.

O resultado aparece em `logs/YYYY-MM-DD.txt` com o componente
`SincronizacaoExcel`. Para desativar o monitoramento, defina
`PONTOFACIL_SYNC_ENABLED=0` e reinicie o backend.

## Uso

```bash
.\.venv\Scripts\python.exe -m backend.modelo1.ler_imagem folha.jpg
.\.venv\Scripts\python.exe -m backend.modelo1.gerar_excel resultado_ocr.json --cabecalho
```

- `python -m backend.modelo1.ler_imagem <imagem>` → cria `resultado_ocr.json`
- `python -m backend.modelo1.gerar_excel <json>` → cria `folha_presenca.xlsx`
  (`--cabecalho` deixa a primeira linha em negrito e congelada)

## Estrutura

```
backend/modelo1/
├── ler_imagem.py   # OCR via MCP do PaddleOCR -> JSON (exclusivo do Modelo 1)
└── gerar_excel.py  # JSON -> planilha .xlsx (exclusivo do Modelo 1)

backend/anonimizacao/
├── detector.py     # PP-StructureV3 local + geometria da tabela
├── ancoras.py      # ancoras de recorte por modelo (quinzena, Saida, faixa do mes, grade)
├── recorte.py      # recorte, redacao e deskew
├── validacao.py    # campos sensiveis, integridade e cabecalho
├── metadados.py    # regrava PNG sem EXIF
├── pdf.py          # rasterizacao local (1 pagina)
├── artefato.py     # ArtefatoAnonimizado (fronteira do MCP)
└── service.py      # orquestracao fail closed

requirements.txt                    # dependencias do backend
backend/requirements-anonimizacao.txt  # Paddle/PP-StructureV3 (versoes fixas)
```
