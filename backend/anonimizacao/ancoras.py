"""Regras por modelo para identificar o limite superior do recorte.

Cada modelo tem a sua propria ancora; o recorte e a redacao continuam
compartilhados em ``recorte.py``/``validacao.py``. Este modulo devolve apenas o
``y`` onde o recorte deve comecar, nunca altera a imagem.

Regras:

- Modelo 1 e Modelo 3: faixa horizontal com ``1a/2a Quinzena``. O corte comeca
  no topo da faixa, nao no ``y`` do texto.
- Modelo 2: linha de cabecalho que contem ``Saida``. O corte sobe ate o topo do
  bloco de rotulos para preservar ``Dia``/``Entrada``/``Intervalo``/``Hora
  Extra``/``Assinatura``; a largura nunca e alterada.
- Modelos 4 e 5 nao passam por aqui (mantem o caminho generico do detector).
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .schemas import Caixa, CelulaTabela, CodigoFalha


@dataclass(frozen=True)
class Ancora:
    """Limite superior calculado para o recorte."""

    y: float
    origem: str


class ErroAncora(RuntimeError):
    """Ancora ausente ou sem confianca suficiente.

    ``recuperavel_por_geometria`` indica que a falha veio da leitura do texto
    (rotulo ausente ou cabecalho incompleto) e que a estrutura da grade pode
    ser consultada como alternativa. Falhas de ambiguidade ou de conteudo
    sobre os registros nunca sao recuperaveis: continuam bloqueando.
    """

    def __init__(
        self,
        codigo: CodigoFalha,
        mensagem: str,
        *,
        recuperavel_por_geometria: bool = False,
    ) -> None:
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem
        self.recuperavel_por_geometria = recuperavel_por_geometria


# Aceita 1a/1o/1o/1/2a... e variacoes de OCR. Depois de ``_sem_acento``
# ``1ª`` vira ``1a`` e ``1º`` vira ``1o``; ``1°`` mantem o simbolo de grau.
_QUINZENA_RE = re.compile(
    r"\b(?:[12]\s*[ao\u00b0]?\s*)?quinzen(?:a|na)\b"
)
_SAIDA_RE = re.compile(r"\bsa[i1l]da\b")
_HORARIO_RE = re.compile(r"\b\d{1,2}\s*[:h]\s*\d{2}\b")
_DIA_RE = re.compile(r"^\s*\d{1,2}\s*$")

_ROTULOS_MODELO2 = {
    "dia": "dia",
    "dias": "dia",
    "data": "dia",
    "datas": "dia",
    "entrada": "entrada",
    "entradas": "entrada",
    "saida": "saida",
    "saidas": "saida",
    "intervalo": "intervalo",
    "intervalos": "intervalo",
    "hora": "hora",
    "horas": "hora",
    "extra": "extra",
    "extras": "extra",
    "assinatura": "assinatura",
    "assinaturas": "assinatura",
}

_ESSENCIAIS_MODELO2 = {"dia", "entrada", "saida"}


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(
        caractere
        for caractere in decomposto
        if not unicodedata.combining(caractere)
    ).lower()


def _normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    return " ".join(_sem_acento(texto).split())


def _eh_dado(texto: str | None) -> bool:
    normalizado = _normalizar(texto)
    return bool(
        normalizado
        and (_HORARIO_RE.search(normalizado) or _DIA_RE.match(normalizado))
    )


def _rotulos_modelo2(texto: str | None) -> set[str]:
    normalizado = _normalizar(texto)
    if not normalizado:
        return set()
    palavras = re.findall(r"[a-z]{2,}", normalizado)
    return {
        _ROTULOS_MODELO2[palavra]
        for palavra in palavras
        if palavra in _ROTULOS_MODELO2
    }


def localizar_limite(
    modelo: str,
    *,
    tabela: Caixa,
    caixas_texto: tuple[Caixa, ...],
    celulas: tuple[CelulaTabela, ...] = (),
    linhas: tuple[float, ...] = (),
    imagem: Any | None = None,
) -> Ancora | None:
    """Seleciona a regra do modelo. Modelos 4/5 retornam ``None``."""
    if modelo == "modelo-1":
        return ancora_modelo1(
            tabela=tabela,
            caixas_texto=caixas_texto,
            celulas=celulas,
            linhas=linhas,
            imagem=imagem,
        )
    if modelo == "modelo-2":
        return ancora_modelo2(
            tabela=tabela,
            caixas_texto=caixas_texto,
            celulas=celulas,
            linhas=linhas,
            imagem=imagem,
        )
    if modelo == "modelo-3":
        return ancora_modelo3(
            tabela=tabela,
            caixas_texto=caixas_texto,
            celulas=celulas,
            linhas=linhas,
            imagem=imagem,
        )
    return None


def ancora_modelo1(**kwargs: Any) -> Ancora:
    """Modelo 1 (faixa azul): limite no topo da faixa da quinzena."""
    return _ancora_faixa(origem="QUINZENA_MODELO1", **kwargs)


def ancora_modelo3(**kwargs: Any) -> Ancora:
    """Modelo 3 (cartao EVO): mesma regra funcional do Modelo 1."""
    return _ancora_faixa(origem="QUINZENA_MODELO3", **kwargs)


def _ancora_faixa(
    *,
    tabela: Caixa,
    caixas_texto: tuple[Caixa, ...],
    celulas: tuple[CelulaTabela, ...] = (),
    linhas: tuple[float, ...] = (),
    imagem: Any | None = None,
    origem: str,
) -> Ancora:
    candidatos = [
        caixa
        for caixa in caixas_texto
        if caixa.texto and _QUINZENA_RE.search(_normalizar(caixa.texto))
    ]
    if not candidatos:
        raise ErroAncora(
            CodigoFalha.ANCHOR_NOT_FOUND,
            "Ancora de quinzena nao encontrada.",
        )

    associados = [
        caixa
        for caixa in candidatos
        if _associada_a_tabela(caixa, tabela)
    ]
    if not associados:
        raise ErroAncora(
            CodigoFalha.ANCHOR_NOT_CONFIDENT,
            "Ancora de quinzena fora da tabela de registros.",
        )

    resolvidos: list[float] = []
    for caixa in associados:
        topo = _topo_faixa(caixa, tabela, celulas, linhas, caixas_texto, imagem)
        if topo is not None:
            resolvidos.append(topo)

    if not resolvidos:
        raise ErroAncora(
            CodigoFalha.ANCHOR_NOT_CONFIDENT,
            "Topo da faixa da quinzena nao identificado.",
        )

    # Varias faixas distintas no mesmo documento sao ambiguas.
    alturas = sorted(caixa.altura for caixa in associados)
    altura = alturas[len(alturas) // 2] if alturas else 16.0
    tolerancia = max(20.0, altura * 1.5)
    grupos: list[list[float]] = []
    for valor in sorted(resolvidos):
        if grupos and valor - grupos[-1][-1] <= tolerancia:
            grupos[-1].append(valor)
        else:
            grupos.append([valor])
    if len(grupos) > 1:
        raise ErroAncora(
            CodigoFalha.ANCHOR_NOT_CONFIDENT,
            "Mais de uma faixa de quinzena associada a tabela.",
        )

    return Ancora(y=min(resolvidos), origem=origem)


def ancora_modelo2(
    *,
    tabela: Caixa,
    caixas_texto: tuple[Caixa, ...],
    celulas: tuple[CelulaTabela, ...] = (),
    linhas: tuple[float, ...] = (),
    imagem: Any | None = None,
) -> Ancora:
    """Modelo 2: limite no topo do bloco de cabecalho que contem ``Saida``."""
    del celulas, linhas, imagem  # o Modelo 2 usa apenas texto/geometria

    saidas = [
        caixa
        for caixa in caixas_texto
        if caixa.texto
        and _SAIDA_RE.search(_normalizar(caixa.texto))
        and _associada_a_tabela(caixa, tabela)
    ]
    if not saidas:
        raise ErroAncora(
            CodigoFalha.ANCHOR_NOT_FOUND,
            "Ancora de saida nao encontrada no cabecalho.",
            recuperavel_por_geometria=True,
        )

    alturas = sorted(caixa.altura for caixa in saidas)
    altura = alturas[len(alturas) // 2] if alturas else 16.0
    grupos = _agrupar_por_centro(saidas, max(6.0, altura * 0.6))
    grupos.sort(key=lambda grupo: min(caixa.y for caixa in grupo))

    escolhido: list[Caixa] | None = None
    for grupo in grupos:
        base = max(caixa.y2 for caixa in grupo)
        if _tem_dados_abaixo(tabela, caixas_texto, base):
            escolhido = grupo
            break
    if escolhido is None:
        raise ErroAncora(
            CodigoFalha.ANCHOR_NOT_CONFIDENT,
            "Saida encontrada, mas sem registros abaixo do cabecalho.",
        )

    y_saida = min(caixa.y for caixa in escolhido)
    janela_topo = y_saida - max(40.0, altura * 3.0)
    janela_base = y_saida + altura * 1.5
    rotulos = [
        caixa
        for caixa in caixas_texto
        if caixa.texto
        and caixa.x2 > tabela.x
        and caixa.x < tabela.x2
        and janela_topo <= caixa.y <= janela_base
        and _rotulos_modelo2(caixa.texto)
    ]
    distintos: set[str] = set()
    for caixa in rotulos:
        distintos |= _rotulos_modelo2(caixa.texto)
    if not _ESSENCIAIS_MODELO2 <= distintos or len(distintos) < 5:
        raise ErroAncora(
            CodigoFalha.ANCHOR_NOT_CONFIDENT,
            "Saida encontrada, mas o cabecalho da tabela esta incompleto.",
            recuperavel_por_geometria=True,
        )

    topo = min(caixa.y for caixa in rotulos)

    primeiro_dado = min(
        (
            caixa.y
            for caixa in caixas_texto
            if caixa.texto
            and _eh_dado(caixa.texto)
            and y_saida < caixa.y < tabela.y2
        ),
        default=None,
    )
    if primeiro_dado is not None and topo >= primeiro_dado:
        raise ErroAncora(
            CodigoFalha.ANCHOR_NOT_CONFIDENT,
            "O topo do cabecalho avanca sobre os registros.",
        )

    return Ancora(y=float(topo), origem="CABECALHO_SAIDA")


def _associada_a_tabela(caixa: Caixa, tabela: Caixa) -> bool:
    """Confere se a caixa esta no eixo da tabela e na faixa imediatamente acima."""
    sobreposicao = min(caixa.x2, tabela.x2) - max(caixa.x, tabela.x)
    if sobreposicao <= 0:
        return False
    # A caixa precisa estar predominantemente dentro do eixo X da tabela.
    # Comparar com a largura do proprio texto evita rejeitar rotulos curtos
    # como "Saida" apenas por serem estreitos.
    if sobreposicao / max(1.0, caixa.largura) < 0.5:
        return False
    tolerancia = max(140.0, tabela.altura * 0.35)
    return (tabela.y - tolerancia) <= caixa.y < tabela.y2


def _agrupar_por_centro(
    caixas: list[Caixa], tolerancia: float
) -> list[list[Caixa]]:
    grupos: list[list[Caixa]] = []
    for caixa in sorted(caixas, key=lambda item: item.y):
        centro = caixa.y + caixa.altura / 2
        for grupo in grupos:
            centro_grupo = sum(
                item.y + item.altura / 2 for item in grupo
            ) / len(grupo)
            if abs(centro - centro_grupo) <= tolerancia:
                grupo.append(caixa)
                break
        else:
            grupos.append([caixa])
    return grupos


def _tem_dados_abaixo(
    tabela: Caixa, caixas_texto: tuple[Caixa, ...], y: float
) -> bool:
    return any(
        caixa.texto
        and _eh_dado(caixa.texto)
        and caixa.y > y + 2
        and tabela.intersecta(caixa)
        for caixa in caixas_texto
    )


def _topo_faixa(
    texto: Caixa,
    tabela: Caixa,
    celulas: tuple[CelulaTabela, ...],
    linhas: tuple[float, ...],
    caixas_texto: tuple[Caixa, ...],
    imagem: Any | None,
) -> float | None:
    """Resolve o topo da faixa (nao o topo do texto)."""
    piso = _texto_anterior_acima(texto, tabela, caixas_texto)
    if piso is not None:
        piso += 1.0

    candidatos: list[tuple[int, float]] = []
    topo_cor = _topo_faixa_cor(imagem, tabela, texto)
    if topo_cor is not None:
        candidatos.append((0, topo_cor))

    celula = _celula_do_texto(texto, celulas)
    if celula is not None:
        candidatos.append((1, celula.y))

    linha = _linha_acima(texto, linhas)
    if linha is not None:
        candidatos.append((2, linha))

    mesma_faixa = _topo_mesma_faixa(texto, caixas_texto)
    if mesma_faixa is not None:
        candidatos.append((3, mesma_faixa))

    for prioridade, valor in sorted(candidatos):
        if valor > texto.y:
            continue
        if piso is not None and not (
            # A faixa colorida pode encostar em 1-3 px de antialiasing do
            # texto anterior. Nesse caso preservar a faixa inteira e mais
            # seguro do que cortar a borda superior.
            prioridade == 0 and 0.0 <= piso - valor <= 3.0
        ):
            valor = max(valor, piso)
        if valor > tabela.y2:
            continue
        return float(valor)
    return None


def _texto_anterior_acima(
    texto: Caixa, tabela: Caixa, caixas_texto: tuple[Caixa, ...]
) -> float | None:
    """``y2`` do texto mais baixo da linha imediatamente acima da faixa.

    O corte nunca pode subir acima desse ponto: assim nenhum texto do cabecalho
    pessoal volta a aparecer, mesmo que a faixa esteja inclinada.
    """
    limite = max(4.0 * texto.altura, 160.0)
    candidatos = [
        caixa.y2
        for caixa in caixas_texto
        if caixa.texto
        and caixa.y2 <= texto.y + 2
        and texto.y - caixa.y2 <= limite
        and caixa.x2 > tabela.x
        and caixa.x < tabela.x2
        and caixa.y + caixa.altura / 2 < texto.y
    ]
    return max(candidatos, default=None)


def _celula_do_texto(
    texto: Caixa, celulas: tuple[CelulaTabela, ...]
) -> Caixa | None:
    centro_x = texto.x + texto.largura / 2
    centro_y = texto.y + texto.altura / 2
    contendo = [
        celula.caixa
        for celula in celulas
        if celula.caixa.y <= centro_y <= celula.caixa.y2
        and celula.caixa.x <= centro_x <= celula.caixa.x2
    ]
    if not contendo:
        return None
    return min(contendo, key=lambda caixa: caixa.area)


def _linha_acima(texto: Caixa, linhas: tuple[float, ...]) -> float | None:
    limite = max(3.0 * texto.altura, 80.0)
    candidatas = [
        linha
        for linha in linhas
        if linha <= texto.y + 1 and texto.y - linha <= limite
    ]
    return max(candidatas, default=None)


def _topo_mesma_faixa(
    texto: Caixa, caixas_texto: tuple[Caixa, ...]
) -> float | None:
    centro = texto.y + texto.altura / 2
    tolerancia = max(8.0, texto.altura * 0.6)
    mesma = [
        caixa.y
        for caixa in caixas_texto
        if caixa.texto
        and abs((caixa.y + caixa.altura / 2) - centro) <= tolerancia
    ]
    return min(mesma, default=None)


def _topo_faixa_cor(
    imagem: Any | None, tabela: Caixa, texto: Caixa
) -> float | None:
    """Detecta o topo da faixa pela cor, quando a imagem esta disponivel.

    A cor e apenas auxiliar: se a faixa for cinza/branca o detector usa a
    celula, as linhas da tabela ou a geometria do texto.
    """
    if imagem is None:
        return None
    try:
        largura_imagem = int(getattr(imagem, "width", 0))
        if largura_imagem <= 0:
            return None
        x1 = max(0, int(round(tabela.x)))
        x2 = min(largura_imagem, int(round(tabela.x2)))
        if x2 - x1 < 40:
            return None
        passo = max(1, (x2 - x1) // 120)
        y = min(int(getattr(imagem, "height", 0)) - 1, int(texto.y) - 2)
        minimo = max(0, int(texto.y) - int(max(4.0 * texto.altura, 120.0)))
        topo: int | None = None
        while y >= minimo:
            if _fracao_faixa(imagem, x1, x2, passo, y) < 0.55:
                break
            topo = y
            y -= 1
        if topo is None:
            return None
        return float(topo)
    except Exception:  # noqa: BLE001 - cor e opcional, nunca quebra o lote
        return None


def _fracao_faixa(
    imagem: Any, x1: int, x2: int, passo: int, y: int
) -> float:
    total = 0
    coloridos = 0
    for x in range(x1, x2, passo):
        pixel = imagem.getpixel((x, y))
        if isinstance(pixel, int):
            vermelho = verde = azul = pixel
        else:
            vermelho, verde, azul = pixel[:3]
        brilho = (vermelho + verde + azul) / 3.0
        chroma = max(vermelho, verde, azul) - min(vermelho, verde, azul)
        total += 1
        if chroma >= 18 and brilho <= 220:
            coloridos += 1
    return coloridos / total if total else 0.0


# ---------------------------------------------------------------------------
# Ancora da faixa do mes (apenas Modelo 1, variacao chinesa sem quinzena)
# ---------------------------------------------------------------------------

AVISO_ANCORA_FAIXA_MES = "ANCHOR_MONTH_BAND"
ORIGEM_FAIXA_MES_MODELO1 = "FAIXA_MES_MODELO1"

# Rotulos IMPRESSOS da faixa do mes: "年" e "月份". O OCR costuma devolver a
# faixa como uma unica caixa ("年月份"); aceita tambem "年", "月" e "月份".
# Nunca depende do mes escrito a mao.
_MARCO_MES_RE = re.compile(r"年月份?|月份?|年")
# Contexto que confirma o cartao chines: titulo 考勤表 ou rotulos 上班/下班.
_CONTEXTO_CHINES_RE = re.compile(r"考勤|勤表|[上下]班")
_TITULO_CHINES_RE = re.compile(r"考勤|勤表")
# Primeira linha do cabecalho das colunas e rotulos de apoio.
_CABECALHO_LINHA1 = ("上午", "下午", "加班")
_CABECALHO_TOKENS = ("上午", "下午", "加班", "上班", "下班", "日期")
_MIN_TOKENS_CABECALHO = 2
_MOTIVOS_FAIXA_MES_BLOQUEIAM = frozenset({"ambigua", "limite_incerto"})


def _compacto(texto: str | None) -> str:
    """Texto sem espacos e normalizado (NFKC), para casar caracteres CJK."""
    if not texto:
        return ""
    return "".join(unicodedata.normalize("NFKC", texto).split())


@dataclass(frozen=True)
class ResultadoFaixaMes:
    """Resultado da ancora da faixa do mes, com rotulos fixos para o log.

    ``motivo``: ``ok``, ``nao_aplicavel`` (modelo diferente do Modelo 1),
    ``sem_contexto`` (nao e o cartao chines), ``nao_encontrada``,
    ``sem_cabecalho`` (a ancora nao vale; o chamador segue para o proximo
    fallback), ``ambigua`` ou ``limite_incerto`` (o chamador deve bloquear).
    Nunca guarda texto lido da imagem.
    """

    ancora: Ancora | None
    motivo: str
    metricas: dict[str, float | int] = field(default_factory=dict)

    @property
    def bloqueia(self) -> bool:
        return self.motivo in _MOTIVOS_FAIXA_MES_BLOQUEIAM

    def resumo(self) -> str:
        partes = [f"faixa_mes={self.motivo}"]
        for nome, valor in self.metricas.items():
            partes.append(
                f"{nome}={valor:.2f}"
                if isinstance(valor, float)
                else f"{nome}={valor}"
            )
        return " ".join(partes)


def _altura_referencia(
    celulas: tuple[CelulaTabela, ...], fallback: float
) -> float:
    alturas = [c.caixa.altura for c in celulas if c.caixa.altura > 0]
    return _mediana(alturas) if alturas else fallback


def avaliar_faixa_mes(
    modelo: str | None,
    *,
    tabela: Caixa,
    caixas_texto: tuple[Caixa, ...],
    celulas: tuple[CelulaTabela, ...] = (),
    linhas: tuple[float, ...] = (),
) -> ResultadoFaixaMes:
    """Limite do recorte logo abaixo da faixa impressa 年/月份 (Modelo 1).

    A faixa fica entre o titulo 考勤表 e o cabecalho das colunas (日期 / 上午
    下午 加班 / 上班 下班). O limite e a base da faixa mais uma margem pequena
    (ou a linha horizontal logo abaixo dela) e precisa ficar ACIMA do centro dos
    rotulos 上午/下午/加班: se nao couber os dois, bloqueia. A faixa serve de
    ancora mesmo em branco; so os rotulos impressos importam.

    Sem 年/月份, sem contexto chines ou sem cabecalho/dados abaixo, a ancora
    nao vale e o chamador segue para o proximo fallback. Mais de uma faixa,
    rotulos fora da regiao da tabela ou limite que cortaria o cabecalho sao
    ambiguos e bloqueiam. Modelos 2, 3, 4 e 5 nunca usam esta ancora.
    """
    if modelo != "modelo-1":
        return ResultadoFaixaMes(None, "nao_aplicavel")

    textos = [
        (caixa, _compacto(caixa.texto))
        for caixa in caixas_texto
        if caixa.texto and caixa.largura > 0 and caixa.altura > 0
    ]
    if not any(_CONTEXTO_CHINES_RE.search(t) for _c, t in textos):
        return ResultadoFaixaMes(None, "sem_contexto")

    def _centro(caixa: Caixa) -> float:
        return caixa.y + caixa.altura / 2

    def _no_eixo_da_tabela(caixa: Caixa) -> bool:
        sobreposicao = min(caixa.x2, tabela.x2) - max(caixa.x, tabela.x)
        return sobreposicao > 0 and sobreposicao / max(1.0, caixa.largura) >= 0.5

    titulos = [c for c, t in textos if _TITULO_CHINES_RE.search(t)]
    centro_titulo = max((_centro(c) for c in titulos), default=None)

    def _abaixo_do_titulo(caixa: Caixa) -> bool:
        return centro_titulo is None or _centro(caixa) > centro_titulo

    linha1 = [
        c
        for c, t in textos
        if any(token in t for token in _CABECALHO_LINHA1)
        and _no_eixo_da_tabela(c)
        and _abaixo_do_titulo(c)
    ]
    if not linha1:
        linha1 = [
            c
            for c, t in textos
            if ("上班" in t or "下班" in t)
            and _no_eixo_da_tabela(c)
            and _abaixo_do_titulo(c)
        ]
    if not linha1:
        # Sem rotulo de mes nem cabecalho, nao ha o que ancorar; com a faixa
        # presente mas sem cabecalho abaixo, a estrutura nao confere.
        tem_marco = any(_MARCO_MES_RE.fullmatch(t) for _c, t in textos)
        return ResultadoFaixaMes(
            None, "sem_cabecalho" if tem_marco else "nao_encontrada"
        )
    centro_cab = min(_centro(c) for c in linha1)
    topo_cab = min(c.y for c in linha1)
    altura_cab = _mediana([c.altura for c in linha1])

    explicitos = [c for c, t in textos if _MARCO_MES_RE.fullmatch(t)]
    na_regiao = [
        c
        for c in explicitos
        if _no_eixo_da_tabela(c)
        and _abaixo_do_titulo(c)
        and _centro(c) < centro_cab
    ]
    metricas: dict[str, float | int] = {"marcos": len(na_regiao)}
    if len(na_regiao) != len(explicitos):
        # Rotulo de mes fora da regiao da tabela: ambiguo.
        metricas["marcos_fora"] = len(explicitos) - len(na_regiao)
        return ResultadoFaixaMes(None, "ambigua", metricas)

    marcos = na_regiao
    if not marcos and centro_titulo is not None:
        # O OCR pode ler os rotulos impressos de forma errada: aceita caixas
        # curtas com ideograma entre o titulo e o topo do cabecalho.
        marcos = [
            c
            for c, t in textos
            if 1 <= len(t) <= 4
            and any("一" <= ch <= "鿿" for ch in t)
            and not _CONTEXTO_CHINES_RE.search(t)
            and _no_eixo_da_tabela(c)
            and centro_titulo < _centro(c) < topo_cab
        ]
        metricas["marcos"] = len(marcos)
    if not marcos:
        return ResultadoFaixaMes(None, "nao_encontrada", metricas)

    altura_marco = _mediana([c.altura for c in marcos])
    grupos = _agrupar_por_centro(marcos, max(20.0, 0.8 * altura_marco))
    metricas["faixas"] = len(grupos)
    if len(grupos) > 1:
        return ResultadoFaixaMes(None, "ambigua", metricas)

    base = max(c.y2 for c in marcos)
    limite = base + max(2.0, 0.05 * altura_marco)
    # Linha horizontal logo abaixo da base da faixa, quando existir.
    abaixo = [
        linha
        for linha in linhas
        if base < linha <= base + 0.5 * altura_marco and linha < centro_cab
    ]
    if abaixo:
        limite = min(abaixo) + 1.0
    limite = max(limite, tabela.y)

    altura_celula = _altura_referencia(celulas, altura_cab)
    metricas["dist_cabecalho_alturas"] = round(
        (centro_cab - limite) / max(1.0, altura_celula), 2
    )
    if limite >= centro_cab:
        # Nao cabe entre a base da faixa e o centro de 上午/下午: bloquear.
        return ResultadoFaixaMes(None, "limite_incerto", metricas)

    tokens = {
        token
        for c, t in textos
        if _centro(c) >= limite and _no_eixo_da_tabela(c)
        for token in _CABECALHO_TOKENS
        if token in t
    }
    metricas["rotulos_cabecalho"] = len(tokens)
    if len(tokens) < _MIN_TOKENS_CABECALHO or not _tem_dados_abaixo(
        tabela, caixas_texto, limite
    ):
        return ResultadoFaixaMes(None, "sem_cabecalho", metricas)

    return ResultadoFaixaMes(
        Ancora(y=float(limite), origem=ORIGEM_FAIXA_MES_MODELO1),
        "ok",
        metricas,
    )


# ---------------------------------------------------------------------------
# Ancora geometrica (alternativa estrutural, apenas Modelo 1 e Modelo 2)
# ---------------------------------------------------------------------------

AVISO_ANCORA_GEOMETRICA = "ANCHOR_GEOMETRIC"
ORIGEM_GEOMETRIA_MODELO1 = "GEOMETRIA_TABELA_MODELO1"
ORIGEM_GEOMETRIA_MODELO2 = "GEOMETRIA_GRADE_MODELO2"

# Todos os limites abaixo sao relativos (altura/largura medianas das celulas
# ou largura da tabela); nenhum depende da resolucao da imagem.
_MIN_LINHAS_GRADE = 6
_MIN_COLUNAS_GRADE = 4
_MIN_DADOS_GRADE = 3
_COBERTURA_MIN_GRADE = 0.6
_DERIVA_MAX_GRADE = 1.5  # em alturas de celula; acima disso a grade e incerta
# Modelo 1 (cartao chines): cabecalhos mesclados fazem as colunas comecarem em
# alturas diferentes mesmo com a foto reta (observado 1.87 e 1.96 com angulo 0).
_DERIVA_MAX_GRADE_MODELO1 = 2.5


def _mediana(valores: list[float]) -> float:
    ordenados = sorted(valores)
    return ordenados[len(ordenados) // 2]


def _agrupar_colunas(caixas: list[Caixa], folga: float) -> list[list[Caixa]]:
    """Agrupa celulas pelo centro horizontal.

    Cada celula entra na coluna cujo centro medio esta a ate ``folga`` dela. A
    comparacao e com a media da coluna (e nao com a ultima celula), para que
    celulas de larguras diferentes nao encadeiem colunas vizinhas em uma so.
    """
    colunas: list[list[Caixa]] = []
    somas: list[float] = []
    for caixa in sorted(caixas, key=lambda item: item.x + item.largura / 2):
        centro = caixa.x + caixa.largura / 2
        if colunas and abs(centro - somas[-1] / len(colunas[-1])) <= folga:
            colunas[-1].append(caixa)
            somas[-1] += centro
        else:
            colunas.append([caixa])
            somas.append(centro)
    return colunas


def _maior_corrida(coluna: list[Caixa], altura: float) -> list[Caixa]:
    """Maior sequencia de celulas empilhadas sem vao, de cima para baixo."""
    ordenada = sorted(coluna, key=lambda item: item.y)
    corridas: list[list[Caixa]] = [[ordenada[0]]]
    for caixa in ordenada[1:]:
        anterior = corridas[-1][-1]
        vao = caixa.y - anterior.y2
        if -0.5 * altura <= vao <= 0.5 * altura:
            corridas[-1].append(caixa)
        else:
            corridas.append([caixa])
    return max(corridas, key=len)


_ANGULOS_GRADE = (0, -1, 1, -2, 2, -3, 3, -4, 4, -5, 5, -6, 6)  # graus
_FATORES_FOLGA = (0.6, 0.4, 0.8)


def _girar(caixas: list[Caixa], graus: float) -> tuple[list[Caixa], dict[int, Caixa]]:
    """Gira os centros das celulas em torno do centro do conjunto.

    O tamanho de cada celula e mantido; so a posicao muda. Devolve tambem o
    mapa de volta para a celula original (por ``id``).
    """
    if not graus:
        return list(caixas), {id(caixa): caixa for caixa in caixas}
    rad = math.radians(graus)
    cos, sen = math.cos(rad), math.sin(rad)
    cx = sum(c.x + c.largura / 2 for c in caixas) / len(caixas)
    cy = sum(c.y + c.altura / 2 for c in caixas) / len(caixas)
    giradas: list[Caixa] = []
    mapa: dict[int, Caixa] = {}
    for caixa in caixas:
        dx = caixa.x + caixa.largura / 2 - cx
        dy = caixa.y + caixa.altura / 2 - cy
        nx = cx + dx * cos - dy * sen
        ny = cy + dx * sen + dy * cos
        nova = Caixa(
            x=nx - caixa.largura / 2,
            y=ny - caixa.altura / 2,
            largura=caixa.largura,
            altura=caixa.altura,
        )
        giradas.append(nova)
        mapa[id(nova)] = caixa
    return giradas, mapa


def _melhor_grade(caixas: list[Caixa], altura: float, base: float):
    """Escolhe a inclinacao e a folga que cobrem mais celulas em colunas de grade.

    Empata sempre a favor do menor angulo, e depois da folga mais estreita
    (ordem de ``_FATORES_FOLGA``), para que uma foto reta nunca seja girada.
    """
    melhor = None
    placar = (-1, 0)  # (celulas na grade, -|angulo|)
    for graus in _ANGULOS_GRADE:
        giradas, mapa = _girar(caixas, graus)
        for fator in _FATORES_FOLGA:
            folga = max(10.0, fator * base)
            colunas = _agrupar_colunas(giradas, folga)
            corridas = [_maior_corrida(coluna, altura) for coluna in colunas]
            grade = [c for c in corridas if len(c) >= _MIN_LINHAS_GRADE]
            # Celulas cobertas pelas corridas: coluna fragmentada cobre menos.
            valor = (sum(len(c) for c in grade), -abs(graus))
            if valor > placar:
                placar = valor
                melhor = (colunas, grade, graus, folga, mapa)
    return melhor


def _estender_por_rotulos(
    topo: float,
    tabela: Caixa,
    caixas_texto: tuple[Caixa, ...],
    altura: float,
) -> float:
    """Sobe o topo apenas sobre faixas formadas so por rotulos de coluna.

    Preserva ``Dia``/``Entrada``/``Saida``/``Intervalo``/``Hora Extra``/
    ``Assinatura`` quando a grade comeca nos dados. Qualquer faixa com texto
    que nao seja rotulo impresso interrompe a subida.
    """
    atual = topo
    limite = topo - 3.5 * altura
    for _ in range(3):
        faixa = [
            caixa
            for caixa in caixas_texto
            if caixa.texto
            and caixa.x2 > tabela.x
            and caixa.x < tabela.x2
            and caixa.y2 <= atual + 2.0
            and caixa.y >= limite
            and (caixa.y + caixa.altura / 2) >= atual - 1.5 * altura
        ]
        if not faixa or not all(_rotulos_modelo2(c.texto) for c in faixa):
            break
        novo = min(caixa.y for caixa in faixa)
        if novo >= atual:
            break
        atual = novo
    return atual


@dataclass(frozen=True)
class ResultadoGeometria:
    """Resultado da avaliacao da grade, com as metricas que levaram a ele.

    ``motivo`` e ``metricas`` servem ao log de diagnostico: sao rotulos fixos e
    numeros agregados, nunca texto lido da imagem.
    """

    ancora: Ancora | None
    motivo: str
    metricas: dict[str, float | int] = field(default_factory=dict)

    def resumo(self) -> str:
        partes = [f"geometria={self.motivo}"]
        for nome, valor in self.metricas.items():
            partes.append(
                f"{nome}={valor:.2f}"
                if isinstance(valor, float)
                else f"{nome}={valor}"
            )
        return " ".join(partes)


def avaliar_geometria(
    modelo: str | None,
    *,
    tabela: Caixa,
    caixas_texto: tuple[Caixa, ...],
    celulas: tuple[CelulaTabela, ...] = (),
) -> ResultadoGeometria:
    """Topo da grade de registros, deduzido da estrutura das celulas.

    Usada somente quando a ancora textual do modelo falha por falta de texto
    (Modelo 1 sem quinzena; Modelo 2 com cabecalho incompleto). Sem confianca
    suficiente, ``ancora`` e ``None`` e o chamador deve bloquear a imagem,
    nunca recortar de forma arbitraria.

    Os Modelos 3, 4 e 5 nunca recebem ancora geometrica.
    """
    if modelo == "modelo-1":
        origem = ORIGEM_GEOMETRIA_MODELO1
    elif modelo == "modelo-2":
        origem = ORIGEM_GEOMETRIA_MODELO2
    else:
        return ResultadoGeometria(None, "nao_aplicavel")

    caixas = [
        celula.caixa
        for celula in celulas
        if celula.caixa.largura > 0 and celula.caixa.altura > 0
    ]
    metricas: dict[str, float | int] = {"celulas": len(caixas)}
    if len(caixas) < _MIN_LINHAS_GRADE * _MIN_COLUNAS_GRADE:
        return ResultadoGeometria(None, "poucas_celulas", metricas)

    altura = _mediana([caixa.altura for caixa in caixas])
    larguras = sorted(caixa.largura for caixa in caixas)
    base = larguras[len(larguras) // 4]
    metricas["altura_cel"] = round(altura, 1)
    metricas["larg_q25"] = round(base, 1)

    melhor = _melhor_grade(caixas, altura, base)
    colunas, grade, graus, folga, mapa = melhor
    metricas["colunas"] = len(colunas)
    metricas["colunas_grade"] = len(grade)
    metricas["angulo"] = graus
    metricas["folga"] = round(folga, 1)
    if len(grade) < _MIN_COLUNAS_GRADE:
        return ResultadoGeometria(None, "grade_curta", metricas)

    # A grade esta em coordenadas giradas; o topo do recorte volta ao original.
    originais = [mapa[id(caixa)] for corrida in grade for caixa in corrida]
    topos = [min(caixa.y for caixa in corrida) for corrida in grade]
    deriva = (max(topos) - min(topos)) / altura
    metricas["deriva_alturas"] = round(deriva, 2)
    limite_deriva = (
        _DERIVA_MAX_GRADE_MODELO1 if modelo == "modelo-1" else _DERIVA_MAX_GRADE
    )
    if deriva > limite_deriva:
        return ResultadoGeometria(None, "deriva_alta", metricas)

    esquerda = min(caixa.x for corrida in grade for caixa in corrida)
    direita = max(caixa.x2 for corrida in grade for caixa in corrida)
    cobertura = (direita - esquerda) / max(1.0, tabela.largura)
    metricas["cobertura"] = round(cobertura, 2)
    if cobertura < _COBERTURA_MIN_GRADE:
        return ResultadoGeometria(None, "cobertura_baixa", metricas)

    topo = min(caixa.y for caixa in originais)
    dados = [
        caixa
        for caixa in caixas_texto
        if caixa.texto
        and _eh_dado(caixa.texto)
        and caixa.y >= topo - 2.0
        and caixa.y2 <= tabela.y2 + 2.0
        and caixa.x2 > tabela.x
        and caixa.x < tabela.x2
    ]
    metricas["dados"] = len(dados)
    if len(dados) < _MIN_DADOS_GRADE:
        return ResultadoGeometria(None, "poucos_dados", metricas)

    if modelo == "modelo-2":
        topo = _estender_por_rotulos(topo, tabela, caixas_texto, altura)

    return ResultadoGeometria(
        Ancora(y=float(max(tabela.y, topo)), origem=origem), "ok", metricas
    )


def ancora_geometrica(
    modelo: str | None,
    *,
    tabela: Caixa,
    caixas_texto: tuple[Caixa, ...],
    celulas: tuple[CelulaTabela, ...] = (),
) -> Ancora | None:
    """Atalho de ``avaliar_geometria`` que devolve so a ancora (ou ``None``)."""
    return avaliar_geometria(
        modelo, tabela=tabela, caixas_texto=caixas_texto, celulas=celulas
    ).ancora
