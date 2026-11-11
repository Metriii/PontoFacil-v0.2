"""Regras de privacidade e integridade aplicadas antes de liberar a imagem.

Campos identificaveis explicitos dentro da tabela sao removidos quando a remocao
e segura. Textos que o OCR nao consegue classificar dentro da tabela sao
preservados quando estao abaixo da linha de corte do cabecalho. O cabecalho
pessoal acima da tabela e sempre descartado.
"""
from __future__ import annotations

import io
import re
import unicodedata

from PIL import Image

from .metadados import metadados_ausentes
from .recorte import brilho_medio
from .schemas import (
    AnaliseSensiveis,
    Caixa,
    CodigoFalha,
    DeteccaoEstrutural,
    ResultadoValidacao,
)

_HORARIO_RE = re.compile(r"\b\d{1,2}\s*[:hH]\s*\d{2}\b")
_DATA_RE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?\b")
_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_NUMERO_RE = re.compile(r"^[\d\s.,%/:-]+$")
_DIA_RE = re.compile(r"^\s*\d{1,2}\s$")

_ROTULO_COLUNA_RE = re.compile(
    r"\b(?:entrada|sa[ií]da|intervalo|total|hora|horas|extra|extras|normal|"
    r"dia|dias|data|datas|manh[aã]|tarde|noite|jornada|trabalho|repouso|"
    r"quinzena|semanal|assiduidade|frequ[eê]ncia)\b",
    re.IGNORECASE,
)

_SENSIVEL_RE = re.compile(
    r"\b(?:nome|funcion[aá]rio|empregado|colaborador|cpf|cnpj|matr[ií]cula|"
    r"empresa|raz[aã]o\s+social|cargo|fun[cç][aã]o|departamento|setor|"
    r"unidade|admiss[aã]o|ctps|pis|rg|identidade|nascimento|telefone|"
    r"celular|e-?mail|endere[cç]o|assinatura|assinado|filia[cç][aã]o)\b",
    re.IGNORECASE,
)

_PERMITIDO_RE = re.compile(
    r"\b(?:dia|dias|data|datas|semana|entrada|saida|intervalo|volta|"
    r"total|hora|horas|normal|extra|extras|falta|faltas|dsr|feriado|"
    r"ferias|folga|abono|atestado|"
    r"noturno|adicional|banco|saldo|observacao|mes|ano|"
    r"quinzena|periodo|registro|ponto|cartao|folha|controle|"
    r"manha|tarde|noite|jornada|trabalho|"
    r"seg|ter|qua|qui|sex|sab|dom|"
    r"segunda|terca|quarta|quinta|sexta|sabado|domingo)\b",
    re.IGNORECASE,
)


def _normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip()


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(
        caractere
        for caractere in decomposto
        if not unicodedata.combining(caractere)
    ).lower()


def _parece_horario(texto: str | None) -> bool:
    return bool(texto and _HORARIO_RE.search(texto))


def _parece_dia(texto: str | None) -> bool:
    return bool(texto and _DIA_RE.match(texto))


def _eh_rotulo_coluna(texto: str | None) -> bool:
    return bool(texto and _ROTULO_COLUNA_RE.search(_sem_acento(texto)))


def _parece_nome(texto: str) -> bool:
    palavras = re.findall(r"[A-Za-zÀ-ÿ]{2,}", texto)
    if len(palavras) < 2:
        return False
    maiusculas = sum(1 for palavra in palavras if palavra[:1].isupper())
    return maiusculas >= 2


def classificar_texto(texto: str | None) -> str:
    """Classifica um texto da tabela em ``permitido``/``sensivel``/``desconhecido``."""
    if not texto:
        return "permitido"
    limpo = _normalizar(texto)
    if not limpo:
        return "permitido"
    normalizado = _sem_acento(limpo)
    if _SENSIVEL_RE.search(normalizado) or _CPF_RE.search(limpo):
        return "sensivel"
    if _HORARIO_RE.search(limpo) or _DATA_RE.search(limpo) or _NUMERO_RE.match(limpo):
        return "permitido"
    palavras = re.findall(r"[a-z]{2,}", normalizado)
    if not palavras:
        return "permitido"
    if all(_PERMITIDO_RE.search(palavra) for palavra in palavras):
        return "permitido"
    if _parece_nome(limpo):
        return "sensivel"
    return "desconhecido"


def _caixas_na_tabela(deteccao: DeteccaoEstrutural) -> list[Caixa]:
    tabela = deteccao.tabela
    if tabela is None:
        return []
    return [
        caixa
        for caixa in deteccao.caixas_texto
        if caixa.texto and tabela.intersecta(caixa)
    ]


def _mesma_linha(a: Caixa, b: Caixa) -> bool:
    centro_a = a.y + a.altura / 2
    centro_b = b.y + b.altura / 2
    tolerancia = max(8.0, min(a.altura, b.altura) * 0.6)
    return abs(centro_a - centro_b) <= tolerancia


def _mesma_coluna(a: Caixa, b: Caixa) -> bool:
    return not (a.x2 <= b.x or b.x2 <= a.x)


# Modelos em que o campo sensivel e ocultado por mascara localizada: o conflito
# so existe quando a mascara de fato atinge um horario/dia ou o campo esta na
# mesma linha de um horario. Nos demais modelos vale a regra original, que
# tambem bloqueia por sobreposicao de coluna.
MODELOS_MASCARA_LOCALIZADA = frozenset({"modelo-4"})

# ``recorte.redigir_caixas`` pinta ``padding=2`` alem da caixa; mais 2 px de
# folga para imprecisao da caixa do OCR.
_MARGEM_MASCARA_PX = 4.0


def _mascara_atinge_dados(caixa: Caixa, dados: list[Caixa]) -> bool:
    """True quando a mascara de ``caixa`` cobriria algum horario/dia.

    Uma caixa que ja e horario/dia (texto misto, como ``ASSINATURA 08:00``)
    esta em ``dados`` e intersecta a si mesma: nunca e mascarada.
    """
    mascara = Caixa(
        x=caixa.x - _MARGEM_MASCARA_PX,
        y=caixa.y - _MARGEM_MASCARA_PX,
        largura=caixa.largura + 2 * _MARGEM_MASCARA_PX,
        altura=caixa.altura + 2 * _MARGEM_MASCARA_PX,
    )
    return any(mascara.intersecta(dado) for dado in dados)


def _chave_caixa(caixa: Caixa) -> tuple[float, float, float, float]:
    """Identidade geometrica usada para reconhecer caixas ja redigidas."""
    return (
        round(caixa.x, 1),
        round(caixa.y, 1),
        round(caixa.x2, 1),
        round(caixa.y2, 1),
    )


def _caixa_covered(caixa: Caixa, extras: tuple[tuple[float, float, float, float], ...]) -> bool:
    """True quando uma regiao extra cobre (quase) toda a caixa sensível."""
    if caixa.area <= 0:
        return False
    for ex in extras:
        ex_caixa = Caixa(x=ex[0], y=ex[1], largura=ex[2], altura=ex[3])
        if ex_caixa.contem(caixa, tolerancia=2):
            return True
        if ex_caixa.interseccao(caixa) / caixa.area >= 0.9:
            return True
    return False


def analisar_sensiveis(
    deteccao: DeteccaoEstrutural,
    *,
    extras: tuple[tuple[float, float, float, float], ...] = (),
    modelo: str | None = None,
) -> AnaliseSensiveis:
    """Decide o que redigir e o que bloqueia a folha.

    Politica (Opcao A): texto acima do limite do cabecalho e redigido (ocultado).
    Textos abaixo do limite sao preservados como dado de ponto.
    Se ha conflito com horarios, bloqueia a folha.

    ``modelo`` seleciona a regra de conflito para campos sensiveis dentro da
    tabela. Em ``MODELOS_MASCARA_LOCALIZADA`` (Modelo 4) o conflito e geometrico
    e local: mesma linha de um horario, ou mascara que atingiria um
    horario/dia. Sobreposicao apenas de coluna deixa de bloquear, porque a
    mascara cobre so a caixa do campo. Sem ``modelo`` (ou em outros modelos)
    a regra original e mantida.

    ``extras`` sao regioes informadas pelo usuario (coordenadas da imagem
    original) que devem ser ocultadas adicionalmente. Elas podem cobrir um
    campo sensível que estaria em conflito, permitindo que a folha siga para
    revisao humana.
    """
    caixas = _caixas_na_tabela(deteccao)
    if not caixas:
        return AnaliseSensiveis()

    tabela = deteccao.tabela
    if tabela is None:
        return AnaliseSensiveis()
    # Limite de corte: imediatamente abaixo do cabecalho da tabela.
    # Estoque preservar dado de ponto abaixo e redigir o que estiver acima.
    limite = tabela.y + 2.0

    # Caixas que comecem acima do corte e terminem dentro dele: sobra de
    # cabecalho por causa de inclinacao/rotacao. Rotulos de coluna e dados
    # (horarios/dias) sao mantidos; o resto e redigido ou bloqueia.
    straddling = [
        caixa
        for caixa in caixas
        if caixa.y < limite <= caixa.y2
        and not (
            _parece_horario(caixa.texto)
            or _parece_dia(caixa.texto)
            or _eh_rotulo_coluna(caixa.texto)
        )
    ]
    descartadas = {id(caixa) for caixa in straddling}
    conteudo = [caixa for caixa in caixas if id(caixa) not in descartadas]

    horarios = [
        caixa for caixa in conteudo if _parece_horario(caixa.texto)
    ]
    dados = [
        caixa
        for caixa in conteudo
        if _parece_horario(caixa.texto) or _parece_dia(caixa.texto)
    ]
    redigir: list[Caixa] = []
    conflitou = False
    motivo: str | None = None

    for caixa in straddling:
        # A sobra de cabecalho pode atravessar a largura da tabela; so bloqueia
        # se ela encostar de fato em um horario/dia (mesma linha ou sobreposicao
        # fisica). Caso contrario, a redacao e segura.
        if any(
            _mesma_linha(caixa, dado) or caixa.intersecta(dado)
            for dado in dados
        ):
            if _caixa_covered(caixa, extras):
                # Usuario marcou uma regiao que cobre o conflito; segue para
                # revisao humana em vez de bloquear.
                redigir.append(caixa)
                continue
            conflitou = True
            motivo = "Sobra de cabecalho encosta em dados da tabela."
            continue
        redigir.append(caixa)

    for caixa in conteudo:
        categoria = classificar_texto(caixa.texto)
        if categoria == "permitido":
            continue
        if categoria == "desconhecido":
            # Texto que o OCR nao classifica dentro da tabela e acima do limite
            # de corte do cabecalho e redigido (ocultado). Abaixo do limite,
            # e preservado como dado de ponto (campo de registo de ponto).
            if caixa.y < limite:
                redigir.append(caixa)
            # Caixa abaixo do limite e preservada (dado de ponto) - nao faz nada
            continue

        # Campo sensivel: verifica se o conflito com horarios e real.
        if modelo in MODELOS_MASCARA_LOCALIZADA:
            em_conflito = any(
                _mesma_linha(caixa, horario) for horario in horarios
            ) or _mascara_atinge_dados(caixa, dados)
            motivo_conflito = (
                "Campo identificavel na linha de um horario ou com mascara "
                "sobre horarios."
            )
        else:
            em_conflito = any(
                _mesma_linha(caixa, horario) or _mesma_coluna(caixa, horario)
                for horario in horarios
            )
            motivo_conflito = (
                "Campo identificavel compartilha linha/coluna com horarios."
            )
        if em_conflito and not _caixa_covered(caixa, extras):
            conflitou = True
            motivo = motivo_conflito
            continue

        redigir.append(caixa)

        # Remove tambem o valor adjacente na mesma linha, a direita do rotulo e
        # antes do primeiro horario da linha.
        for vizinha in conteudo:
            if vizinha is caixa or _parece_horario(vizinha.texto):
                continue
            if not _mesma_linha(caixa, vizinha):
                continue
            if vizinha.x < caixa.x2 - 2:
                continue
            if horarios:
                primeiro = min(
                    (
                        horario.x
                        for horario in horarios
                        if _mesma_linha(caixa, horario)
                    ),
                    default=float("inf"),
                )
                if vizinha.x >= primeiro:
                    continue
            if classificar_texto(vizinha.texto) != "permitido":
                if (
                    modelo in MODELOS_MASCARA_LOCALIZADA
                    and _mascara_atinge_dados(vizinha, dados)
                    and not _caixa_covered(vizinha, extras)
                ):
                    conflitou = True
                    motivo = motivo_conflito
                    continue
                redigir.append(vizinha)

    return AnaliseSensiveis(
        redigir=tuple(dict.fromkeys(redigir)),
        suspeitos=(),  # Na politica Opcao A, nao ha suspeitos gerados automaticamente
        conflito=conflitou,
        motivo=motivo,
    )


def validar_recorte(
    deteccao: DeteccaoEstrutural,
    recorte: Caixa,
    redigidas: tuple[Caixa, ...] | list[Caixa] = (),
) -> ResultadoValidacao:
    """Confere se o recorte preserva a tabela e elimina o cabecalho."""
    tabela = deteccao.tabela
    if tabela is None:
        return ResultadoValidacao(
            ok=False,
            codigo=CodigoFalha.TABLE_NOT_DETECTED,
            mensagem="Nenhuma tabela detectada.",
        )

    if (
        recorte.x < 0
        or recorte.y < 0
        or recorte.x2 > deteccao.largura
        or recorte.y2 > deteccao.altura
        or recorte.largura <= 0
        or recorte.altura <= 0
    ):
        return ResultadoValidacao(
            ok=False,
            codigo=CodigoFalha.CROP_OUT_OF_BOUNDS,
            mensagem="Recorte fora dos limites da imagem.",
        )

    if not recorte.contem(tabela, tolerancia=4):
        return ResultadoValidacao(
            ok=False,
            codigo=CodigoFalha.POSSIBLE_ROW_LOSS,
            mensagem="O recorte nao contem a tabela inteira.",
        )

    if deteccao.cabecalho is not None and recorte.intersecta(deteccao.cabecalho):
        return ResultadoValidacao(
            ok=False,
            codigo=CodigoFalha.SENSITIVE_CONTENT_REMAINS,
            mensagem="O recorte avanca sobre o cabecalho.",
        )

    for celula in deteccao.celulas:
        if not recorte.contem(celula.caixa, tolerancia=6):
            return ResultadoValidacao(
                ok=False,
                codigo=CodigoFalha.POSSIBLE_ROW_LOSS,
                mensagem="Uma celula da tabela ficou fora do recorte.",
            )

    redigidas_chaves = {_chave_caixa(caixa) for caixa in redigidas}
    for caixa in deteccao.caixas_texto:
        if not caixa.texto:
            continue
        # Uma caixa redigida foi pintada de branco; a cobertura e conferida por
        # ``redacao_efetiva``. Nao faz sentido acusa-la como texto visivel.
        if _chave_caixa(caixa) in redigidas_chaves:
            continue
        if caixa.y2 <= tabela.y + 2 and recorte.intersecta(caixa):
            return ResultadoValidacao(
                ok=False,
                codigo=CodigoFalha.SENSITIVE_CONTENT_REMAINS,
                mensagem="Texto de cabecalho permanece no recorte.",
            )

    for caixa in redigidas:
        if not recorte.intersecta(caixa):
            return ResultadoValidacao(
                ok=False,
                codigo=CodigoFalha.SENSITIVE_CONTENT_REMAINS,
                mensagem="Campo sensivel fora da area recortada.",
            )

    return ResultadoValidacao(
        ok=True,
        verificacao_sensiveis="ok",
        verificacao_integridade="ok",
    )


def redacao_efetiva(
    imagem: Image.Image,
    caixas: tuple[Caixa, ...] | list[Caixa],
    recorte: Caixa,
    *,
    brilho_minimo: float = 245.0,
) -> bool:
    """Confere se as regioes redigidas ficaram brancas na imagem recortada."""
    for caixa in caixas:
        local = Caixa(
            x=caixa.x - recorte.x,
            y=caixa.y - recorte.y,
            largura=caixa.largura,
            altura=caixa.altura,
        )
        if brilho_medio(imagem, local) < brilho_minimo:
            return False
    return True


def validar_saida(
    dados: bytes,
    *,
    largura_minima: int = 32,
    altura_minima: int = 32,
) -> ResultadoValidacao:
    """Valida a imagem final e a ausencia de metadados identificaveis."""
    try:
        with Image.open(io.BytesIO(dados)) as imagem:
            imagem.load()
            if imagem.width < largura_minima or imagem.height < altura_minima:
                return ResultadoValidacao(
                    ok=False,
                    codigo=CodigoFalha.OUTPUT_IMAGE_INVALID,
                    mensagem="Imagem final pequena demais.",
                )
            if not imagem.getbbox():
                return ResultadoValidacao(
                    ok=False,
                    codigo=CodigoFalha.OUTPUT_IMAGE_INVALID,
                    mensagem="Imagem final vazia.",
                )
            minimo, maximo = imagem.convert("L").getextrema()
            if minimo == maximo:
                return ResultadoValidacao(
                    ok=False,
                    codigo=CodigoFalha.OUTPUT_IMAGE_INVALID,
                    mensagem="Imagem final sem conteudo.",
                )
    except (OSError, ValueError):
        return ResultadoValidacao(
            ok=False,
            codigo=CodigoFalha.OUTPUT_IMAGE_INVALID,
            mensagem="Imagem final nao abre.",
        )

    if not metadados_ausentes(dados):
        return ResultadoValidacao(
            ok=False,
            codigo=CodigoFalha.OUTPUT_IMAGE_INVALID,
            mensagem="Metadados identificaveis permanecem na imagem.",
        )

    return ResultadoValidacao(
        ok=True,
        verificacao_sensiveis="ok",
        verificacao_integridade="ok",
    )


def texto_cabecalho(deteccao: DeteccaoEstrutural) -> str | None:
    """Concatena o texto local do cabecalho e dos rotulos da tabela.

    Inclui os rotulos da primeira linha da tabela (``Entrada``, ``Saida``,
    ``Hora Normal``...) porque alguns layouts trazem a jornada prevista ali.
    Esse texto fica apenas em memoria e nunca e enviado ao OCR externo.
    """
    tabela = deteccao.tabela
    if tabela is None:
        return None
    caixas = []
    for caixa in deteccao.caixas_texto:
        if not caixa.texto:
            continue
        if caixa.y2 <= tabela.y + 2:
            caixas.append(caixa)
        elif caixa.y < tabela.y + 80 and _eh_rotulo_coluna(caixa.texto):
            caixas.append(caixa)
    if not caixas:
        return None
    caixas.sort(key=lambda caixa: (round(caixa.y / 8), caixa.x))
    partes = [_normalizar(caixa.texto or "") for caixa in caixas]
    texto = "\n".join(parte for parte in partes if parte)
    return texto or None