"""Detector estrutural local (PP-StructureV3).

O modelo e carregado uma unica vez por processo e nunca fala com a rede. Toda a
importacao do Paddle/PaddleX e tardia: o backend principal continua subindo sem
essas dependencias e a anonimizacao falha fechada quando elas nao existem.

O formato de saida do PP-StructureV3 varia entre versoes. Em vez de depender de
uma classe interna, este modulo normaliza o resultado para ``DeteccaoEstrutural``
com uma leitura tolerante (dict, atributo, ``.json`` e ``.to_dict()``).
"""

from __future__ import annotations

import json
import re
import threading
import unicodedata
from io import BytesIO
from typing import Any

from . import ancoras
from .schemas import (
    Caixa,
    CelulaTabela,
    CodigoFalha,
    DeteccaoEstrutural,
)


class ErroDeteccao(RuntimeError):
    """Falha do modelo estrutural, sempre mapeada para um codigo de bloqueio."""

    def __init__(
        self,
        codigo: CodigoFalha,
        mensagem: str,
        *,
        diagnostico: str | None = None,
    ) -> None:
        super().__init__(mensagem)
        self.codigo = codigo
        # Apenas para log: rotulos fixos e metricas agregadas, sem texto lido.
        self.diagnostico = diagnostico


_modelo: Any = None
_lock = threading.Lock()
_versao: str | None = None


def _carregar_modelo() -> Any:
    global _modelo, _versao
    if _modelo is not None:
        return _modelo
    with _lock:
        if _modelo is not None:
            return _modelo
        try:
            from paddleocr import PPStructureV3
        except ImportError as exc:
            raise ErroDeteccao(
                CodigoFalha.STRUCTURE_MODEL_ERROR,
                "PaddleOCR/PP-StructureV3 nao esta instalado no ambiente.",
            ) from exc

        # Configuracao minima validada na PoC em CPU com 4 GB de RAM. Os modulos
        # de formula/selo/grafico/desdobramento/orientacao e a deteccao de
        # regiao nao participam da anonimizacao e so consumiriam memoria. O OCR
        # mobile e suficiente para achar rotulos e horarios da tabela.
        kwargs: dict[str, Any] = {
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "use_seal_recognition": False,
            "use_formula_recognition": False,
            "use_chart_recognition": False,
            "use_region_detection": False,
            "use_table_recognition": True,
            "text_detection_model_name": "PP-OCRv5_mobile_det",
            "text_recognition_model_name": "PP-OCRv5_mobile_rec",
        }
        try:
            from .. import config

            dispositivo = getattr(config, "ANON_DISPOSITIVO", "cpu")
            if dispositivo:
                kwargs["device"] = dispositivo
        except Exception:  # noqa: BLE001 - configuracao opcional
            pass

        try:
            try:
                _modelo = PPStructureV3(**kwargs)
            except TypeError:
                # Versoes mais antigas nao aceitam todos os argumentos. Mantem
                # apenas o dispositivo, se suportado.
                dispositivo = kwargs.pop("device", None)
                try:
                    _modelo = PPStructureV3(
                        **({"device": dispositivo} if dispositivo else {})
                    )
                except TypeError:
                    _modelo = PPStructureV3()
        except Exception as exc:  # noqa: BLE001
            raise ErroDeteccao(
                CodigoFalha.STRUCTURE_MODEL_ERROR,
                f"Falha ao carregar o PP-StructureV3: {type(exc).__name__}.",
            ) from exc

        try:
            import paddleocr

            _versao = getattr(paddleocr, "__version__", None)
        except Exception:  # noqa: BLE001
            _versao = None
    return _modelo


def versao_modelo() -> str | None:
    return _versao


def _como_dicionario(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    for atributo in ("json", "to_dict"):
        try:
            valor = getattr(item, atributo, None)
            if callable(valor):
                valor = valor()
        except Exception:  # noqa: BLE001
            continue
        if isinstance(valor, str):
            try:
                valor = json.loads(valor)
            except ValueError:
                continue
        if isinstance(valor, dict):
            interno = _valor(valor, "res")
            return interno if interno is not None else valor
    try:
        return vars(item)
    except TypeError:
        return {}


def _texto(valor: Any) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, str):
        texto = valor.strip()
        return texto or None
    if isinstance(valor, (list, tuple)):
        partes = [str(parte).strip() for parte in valor if str(parte).strip()]
        return " ".join(partes) or None
    texto = str(valor).strip()
    return texto or None


def _numero(valor: Any) -> float | None:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _coordenadas(valor: Any) -> tuple[float, float, float, float] | None:
    if valor is None:
        return None
    if isinstance(valor, dict):
        for chave in ("coordinate", "box", "bbox", "points", "rect"):
            encontrado = _valor(valor, chave)
            if encontrado is not None:
                return _coordenadas(encontrado)
        return None
    if hasattr(valor, "tolist"):
        valor = valor.tolist()
    if not isinstance(valor, (list, tuple)) or len(valor) < 4:
        return None

    # Formato [x1, y1, x2, y2]. Aceita escalares do numpy (np.float32 etc.).
    numeros = [_numero(item) for item in valor[:4]]
    if all(numero is not None for numero in numeros):
        x1, y1, x2, y2 = (float(numero) for numero in numeros if numero is not None)
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)

    # Formato de poligono [[x, y], ...].
    pontos: list[tuple[float, float]] = []
    for ponto in valor:
        if isinstance(ponto, (list, tuple)) and len(ponto) >= 2:
            x = _numero(ponto[0])
            y = _numero(ponto[1])
            if x is not None and y is not None:
                pontos.append((x, y))
        elif hasattr(ponto, "tolist"):
            convertido = ponto.tolist()
            if isinstance(convertido, (list, tuple)) and len(convertido) >= 2:
                x = _numero(convertido[0])
                y = _numero(convertido[1])
                if x is not None and y is not None:
                    pontos.append((x, y))
    if not pontos:
        return None
    xs = [ponto[0] for ponto in pontos]
    ys = [ponto[1] for ponto in pontos]
    return min(xs), min(ys), max(xs), max(ys)


def _caixa_de(valor: Any, texto: str | None = None, tipo: str | None = None) -> Caixa | None:
    coordenadas = _coordenadas(valor)
    if coordenadas is None:
        return None
    x1, y1, x2, y2 = coordenadas
    if x2 <= x1 or y2 <= y1:
        return None
    return Caixa(
        x=x1,
        y=y1,
        largura=x2 - x1,
        altura=y2 - y1,
        texto=texto,
        tipo=tipo,
    )


def _label(item: dict[str, Any]) -> str:
    for chave in ("label", "type", "category", "cls_name", "class_name"):
        valor = item.get(chave)
        if valor:
            return str(valor).lower()
    return ""


def _score(item: dict[str, Any]) -> float:
    for chave in ("score", "confidence", "conf"):
        valor = item.get(chave)
        if isinstance(valor, (int, float)):
            return float(valor)
    return 1.0


def _valor(item: Any, *chaves: str) -> Any:
    """Primeiro valor preenchido sem usar ``or`` nem ``in``.

    Os resultados do PaddleX sao subclasses de ``dict`` que nem sempre
    implementam ``__contains__`` sobre os mesmos dados expostos por ``get``.
    Tambem podem conter ``numpy.ndarray``, cuja avaliacao booleana levanta
    ``ValueError``. Por isso a leitura e feita sempre por ``get`` e por
    ``len``.
    """
    if item is None:
        return None
    getter = getattr(item, "get", None)
    if not callable(getter):
        return None
    for chave in chaves:
        try:
            valor = getter(chave)
        except Exception:  # noqa: BLE001 - resultado heterogeneo
            continue
        if valor is None:
            continue
        try:
            if len(valor) == 0:
                continue
        except TypeError:
            pass
        return valor
    return None


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(
        caractere
        for caractere in decomposto
        if not unicodedata.combining(caractere)
    ).lower()


# Rotulos de coluna que pertencem a tabela de ponto. Eles podem ficar dentro do
# recorte; nao sao dados pessoais. "assinatura" fica de fora de proposito: e um
# campo sensivel e deve ser removido ou bloquear.
_ROTULO_COLUNA_RE = re.compile(
    r"\b(?:entrada|saida|intervalo|total|hora|horas|extra|extras|normal|"
    r"dia|dias|data|datas|manha|tarde|noite|jornada|trabalho|repouso|"
    r"semanal|assiduidade|frequencia)\b"
)
_HORARIO_RE = re.compile(r"\b\d{1,2}\s*[:hH]\s*\d{2}\b")
_DIA_RE = re.compile(r"^\s*\d{1,2}\s*$")


def _eh_rotulo_coluna(texto: str | None) -> bool:
    return bool(texto and _ROTULO_COLUNA_RE.search(_sem_acento(texto)))


def _eh_horario(texto: str | None) -> bool:
    return bool(texto and _HORARIO_RE.search(texto))


def _eh_dia(texto: str | None) -> bool:
    return bool(texto and _DIA_RE.match(texto))


def _normalizar_titulo(texto: str) -> str:
    """Normaliza texto para comparar titulos tolerando variacoes de OCR."""
    limpo = _sem_acento(texto).lower()
    for marca in ("\u00aa", "\u00ba", "\u00b0", "."):
        limpo = limpo.replace(marca, "")
    return " ".join(limpo.split())


def _localizar_titulo_secao(
    caixas_texto: tuple[Caixa, ...], termos: list[str]
) -> float | None:
    """Localiza a linha (``y``) do titulo de secao mais alto entre ``termos``.

    A comparacao ignora acentos, caixa alta/baixa e o indicador ordinal
    (``\u00aa``/``\u00b0``), aceitando variacoes comuns de OCR como
    ``1\u00aa Quinzena``, ``1\u00b0 Quinzena`` e ``Quinzena``.

    Retorna ``None`` quando nenhum termo aparece. Nesse caso o chamador deve
    manter o comportamento conservador (bloqueio), nunca inventar uma posicao.
    """
    if not caixas_texto or not termos:
        return None

    termos_norm = [t for t in (_normalizar_titulo(t) for t in termos) if t]
    if not termos_norm:
        return None

    encontrados: list[float] = []
    for caixa in caixas_texto:
        if not caixa.texto:
            continue
        texto_norm = _normalizar_titulo(caixa.texto)
        if not texto_norm:
            continue
        for termo_norm in termos_norm:
            if termo_norm in texto_norm:
                encontrados.append(caixa.y)
                break

    if not encontrados:
        return None
    # O titulo mais alto garante que a linha inteira de rotulos seja preservada.
    return min(encontrados)


def _localizar_titulo_amanda(caixas_texto: tuple[Caixa, ...]) -> float | None:
    """Localiza a linha de titulos do modelo com colunas "Dias"/"Intervalo".

    O rotulo "Dias" e o marcador que identifica esse modelo; "Intervalo" e
    usado apenas para subir o recorte ate a linha completa de rotulos, desde
    que esteja na mesma faixa vertical. Sem "Dias" nao ha identificacao segura
    e a funcao retorna ``None`` (o chamador mantem o comportamento antigo).
    """
    dias_y = _localizar_titulo_secao(caixas_texto, ["Dias"])
    if dias_y is None:
        return None
    intervalo_y = _localizar_titulo_secao(caixas_texto, ["Intervalo"])
    if (
        intervalo_y is not None
        and intervalo_y < dias_y
        and (dias_y - intervalo_y) <= 80.0
    ):
        return intervalo_y
    return dias_y


def _caixas_ocr(data: dict[str, Any]) -> list[Caixa]:
    """Extrai as linhas de texto do OCR global (texto + coordenadas)."""
    caixas: list[Caixa] = []
    global_ocr = _valor(data, "overall_ocr_res", "ocr_res")
    if global_ocr is None:
        return caixas
    if isinstance(global_ocr, list) and global_ocr:
        global_ocr = global_ocr[0]
    if not isinstance(global_ocr, dict):
        return caixas

    textos = _valor(global_ocr, "rec_texts", "texts")
    caixas_ocr = _valor(global_ocr, "rec_boxes", "rec_polys", "dt_polys")
    if textos is None or caixas_ocr is None:
        return caixas

    # ``caixas_ocr`` pode ser ``numpy.ndarray``; ``zip`` itera sem avaliar o
    # array como booleano (o que levanta erro com mais de um elemento).
    for texto, coordenadas in zip(textos, caixas_ocr):
        caixa = _caixa_de(coordenadas, _texto(texto), "text")
        if caixa is not None:
            caixas.append(caixa)
    return caixas


def _blocos_texto(data: dict[str, Any]) -> list[Caixa]:
    """Texto de leitura, preferindo as linhas do OCR global.

    O ``parsing_res_list`` agrupa a folha inteira em um unico bloco ``table``
    quando o layout e reconhecido como tabela; usar esses blocos esconderia o
    cabecalho e os campos sensiveis. O OCR global entrega linha a linha, que e
    o que a anonimizacao precisa para separar cabecalho, rotulos e dados.
    """
    caixas = _caixas_ocr(data)
    if caixas:
        return caixas

    # Fallback para versoes/testes que so trazem blocos de parsing.
    blocos = _valor(data, "parsing_res_list", "parsing_res")
    if blocos is None:
        blocos = []
    if isinstance(blocos, dict):
        blocos = blocos.get("blocks", [])
    for bloco in blocos or []:
        if not isinstance(bloco, dict):
            bloco = _como_dicionario(bloco)
        coordenadas = _valor(
            bloco, "block_bbox", "bbox", "box", "coordinate"
        )
        texto = _texto(_valor(bloco, "block_content", "text", "content"))
        tipo = _valor(bloco, "block_label", "label")
        caixa = _caixa_de(coordenadas, texto, str(tipo) if tipo else None)
        if caixa is not None:
            caixas.append(caixa)
    return caixas


def _tabelas(data: dict[str, Any]) -> list[tuple[Caixa, float, dict[str, Any]]]:
    candidatas: list[tuple[Caixa, float, dict[str, Any]]] = []

    layout = _valor(data, "layout_det_res", "layout_det_results")
    if layout is None:
        layout = {}
    if isinstance(layout, list) and layout:
        layout = layout[0]
    caixas_layout = []
    if isinstance(layout, dict):
        caixas_layout = _valor(layout, "boxes", "detections")
        if caixas_layout is None:
            caixas_layout = []
    for item in caixas_layout or []:
        if not isinstance(item, dict):
            item = _como_dicionario(item)
        rotulo = _label(item)
        if "table" not in rotulo:
            continue
        caixa = _caixa_de(
            _valor(item, "coordinate", "box", "bbox"),
            None,
            rotulo or "table",
        )
        if caixa is not None:
            candidatas.append((caixa, _score(item), {}))

    tabelas = _valor(data, "table_res_list", "table_res")
    if tabelas is None:
        tabelas = []
    if isinstance(tabelas, dict):
        tabelas = tabelas.get("tables", [tabelas])
    for item in tabelas or []:
        if not isinstance(item, dict):
            item = _como_dicionario(item)
        caixa = _caixa_de(
            _valor(
                item,
                "table_box",
                "table_boxes",
                "box",
                "bbox",
                "coordinate",
            ),
            None,
            "table",
        )
        # O PP-StructureV3 real devolve ``table_res_list`` sem ``table_box``.
        # Nesse caso, usa a maior tabela do layout como regiao do resultado.
        if caixa is None and candidatas:
            caixa = max(candidatas, key=lambda candidata: candidata[0].area)[0]
        if caixa is not None:
            candidatas.append((caixa, _score(item), item))

    # Ordena por area e confianca; a maior tabela e a folha de ponto.
    candidatas.sort(key=lambda candidata: candidata[0].area * candidata[1], reverse=True)
    return candidatas


def _celulas(
    tabela: Caixa, tabelas: list[tuple[Caixa, float, dict[str, Any]]]
) -> tuple[CelulaTabela, ...]:
    dados_tabela: dict[str, Any] = {}
    for caixa, _score_item, item in tabelas:
        if caixa is tabela or (
            abs(caixa.x - tabela.x) < 2 and abs(caixa.y - tabela.y) < 2
        ):
            dados_tabela = item
            break
    if not dados_tabela:
        # A tabela foi ajustada (cabecalho removido) e pode nao coincidir mais
        # com a caixa original. Como a folha tem uma unica tabela, usa o
        # primeiro resultado que traga celulas.
        for _caixa, _score_item, item in tabelas:
            if _valor(item, "cell_box_list", "cells", "cell_bboxes") is not None:
                dados_tabela = item
                break
    if not dados_tabela:
        return ()

    brutas = _valor(dados_tabela, "cell_box_list", "cells", "cell_bboxes")
    if brutas is None:
        brutas = []
    caixas: list[Caixa] = []
    for bruta in brutas or []:
        caixa = _caixa_de(bruta, None, "cell")
        if caixa is not None and tabela.contem(caixa, tolerancia=8):
            caixas.append(caixa)

    if not caixas:
        return ()

    ys = sorted({round(caixa.y, 1) for caixa in caixas})
    xs = sorted({round(caixa.x, 1) for caixa in caixas})

    def _indice(valor: float, base: list[float]) -> int | None:
        for indice, referencia in enumerate(base):
            if abs(valor - referencia) <= 4:
                return indice
        return None

    return tuple(
        CelulaTabela(
            caixa=caixa,
            linha=_indice(round(caixa.y, 1), ys),
            coluna=_indice(round(caixa.x, 1), xs),
        )
        for caixa in caixas
    )


def _linhas_horizontais(
    tabela: Caixa, celulas: tuple[CelulaTabela, ...]
) -> tuple[float, ...]:
    valores = {round(tabela.y, 1), round(tabela.y2, 1)}
    for celula in celulas:
        valores.add(round(celula.caixa.y, 1))
        valores.add(round(celula.caixa.y2, 1))
    return tuple(sorted(valores))


def _detectar_inicio_tabela(
    tabela: Caixa, caixas_texto: tuple[Caixa, ...]
) -> float | None:
    """Acha a primeira linha da tabela de ponto (rotulos de coluna).

    O PP-StructureV3 costuma devolver a folha inteira como uma unica tabela,
    incluindo empresa, nome, CPF e jornada. O topo real da tabela e a linha de
    rotulos como ``Entrada``/``Saida``/``Dias``. Sem essa linha, a folha e
    bloqueada (fail closed).
    """
    dentro = [
        caixa
        for caixa in caixas_texto
        if caixa.texto and tabela.intersecta(caixa)
    ]
    rotulos = [caixa for caixa in dentro if _eh_rotulo_coluna(caixa.texto)]
    if not rotulos:
        return None

    alturas = sorted(caixa.altura for caixa in rotulos)
    mediana = alturas[len(alturas) // 2] if alturas else 20.0
    tolerancia = max(12.0, mediana * 0.9)

    grupos: list[list[Caixa]] = []
    vistos: set[frozenset[int]] = set()
    for ancora in rotulos:
        centro = ancora.y + ancora.altura / 2
        grupo = [
            caixa
            for caixa in rotulos
            if abs((caixa.y + caixa.altura / 2) - centro) <= tolerancia
        ]
        chave = frozenset(id(caixa) for caixa in grupo)
        if chave in vistos:
            continue
        vistos.add(chave)
        grupos.append(grupo)

    candidatos: list[tuple[float, float]] = []
    for grupo in grupos:
        distintos = {_sem_acento(caixa.texto or "") for caixa in grupo}
        if len(distintos) < 2:
            continue
        base = max(caixa.y2 for caixa in grupo)
        # A linha precisa ter dados (horarios) abaixo dela; isso evita confundir
        # um rodape com "Total" ou "Hora Extra" com o cabecalho da tabela.
        if not any(
            _eh_horario(caixa.texto)
            and (caixa.y + caixa.altura / 2) > base + 2
            for caixa in dentro
        ):
            continue
        candidatos.append((base, min(caixa.y for caixa in grupo)))

    if not candidatos:
        return None

    base, topo = max(candidatos, key=lambda item: item[0])
    horarios_abaixo = [
        caixa
        for caixa in dentro
        if _eh_horario(caixa.texto)
        and (caixa.y + caixa.altura / 2) > base + 2
    ]
    if horarios_abaixo:
        primeiro = min(caixa.y for caixa in horarios_abaixo)
        # O corte nao pode avancar sobre a primeira linha de dados.
        if topo - 4.0 >= primeiro - 2.0:
            return None
    return topo


def _limitar_rodape(tabela: Caixa, caixas_texto: tuple[Caixa, ...]) -> float | None:
    """Corta rodapes longos depois da ultima marcacao/dia da tabela."""
    dentro = [
        caixa
        for caixa in caixas_texto
        if caixa.texto and tabela.intersecta(caixa)
    ]
    dados = [
        caixa
        for caixa in dentro
        if _eh_horario(caixa.texto) or _eh_dia(caixa.texto)
    ]
    if not dados:
        return None

    fim = max(caixa.y2 for caixa in dados)
    if tabela.y2 - fim <= 40.0:
        return None

    limite = fim + 12.0
    # Se houver horario/dia abaixo do limite, o "rodape" e conteudo real.
    if any(
        caixa.y > limite
        and (_eh_horario(caixa.texto) or _eh_dia(caixa.texto))
        for caixa in dentro
    ):
        return None
    return min(tabela.y2, limite)


_MODELOS_COM_ANCORA = {"modelo-1", "modelo-2", "modelo-3"}


def _inicio_generico(
    tabela: Caixa, caixas_texto: tuple[Caixa, ...]
) -> float | None:
    """Caminho antigo para os Modelos 4/5 e para chamadas sem modelo.

    Localiza o titulo de secao e, na ausencia dele, a linha de rotulos da
    tabela. Nenhuma regra usa nome de pessoa ou de arquivo.
    """
    termos_quinzena = ["Quinzena", "1\u00aa Quinzena", "2\u00aa Quinzena"]
    titulo_secao_y = _localizar_titulo_secao(caixas_texto, termos_quinzena)
    if titulo_secao_y is None:
        titulo_secao_y = _localizar_titulo_amanda(caixas_texto)

    if titulo_secao_y is not None:
        # O titulo deve estar na faixa imediatamente acima da tabela. Uma
        # ocorrencia muito acima (por exemplo, a palavra "Dias" no cabecalho
        # pessoal) nao pode puxar o recorte para cima e reexpor nome/CPF.
        # A tolerancia e relativa a altura da tabela, nunca uma coordenada fixa.
        tolerancia_titulo = max(80.0, tabela.altura * 0.25)
        if not (tabela.y - tolerancia_titulo <= titulo_secao_y < tabela.y2):
            titulo_secao_y = None

    if titulo_secao_y is not None:
        return max(0.0, titulo_secao_y)

    topo = _detectar_inicio_tabela(tabela, caixas_texto)
    if topo is None:
        return None
    return max(0.0, topo - 4.0)


def _resolver_inicio(
    modelo: str | None,
    tabela: Caixa,
    caixas_texto: tuple[Caixa, ...],
    celulas: tuple[CelulaTabela, ...],
    linhas: tuple[float, ...],
    imagem: Any | None,
) -> tuple[float | None, str | None]:
    """Resolve o ``y`` inicial do recorte e um eventual aviso.

    Os Modelos 1/2/3 usam a ancora especifica do modelo. Se a ancora do
    Modelo 1/3 nao existir (``ANCHOR_NOT_FOUND``), o fallback de rotulos e
    aceito com o aviso ``ANCHOR_FALLBACK``; qualquer outra falha bloqueia.

    Alternativa estrutural (``ANCHOR_GEOMETRIC``), exclusiva de cada modelo:

    - Modelo 1 (variacao sem quinzena): ultimo recurso, depois do fallback de
      rotulos que ja existia.
    - Modelo 2: somente quando a ancora textual falha por falta de texto
      (``recuperavel_por_geometria``); ambiguidades continuam bloqueando.
    - Modelo 3 (verde): nenhuma alternativa nova; comportamento preservado.
    """
    if modelo not in _MODELOS_COM_ANCORA:
        return _inicio_generico(tabela, caixas_texto), None

    try:
        ancora = ancoras.localizar_limite(
            modelo,
            tabela=tabela,
            caixas_texto=caixas_texto,
            celulas=celulas,
            linhas=linhas,
            imagem=imagem,
        )
    except ancoras.ErroAncora as exc:
        if (
            modelo in {"modelo-1", "modelo-3"}
            and exc.codigo is CodigoFalha.ANCHOR_NOT_FOUND
        ):
            topo = _detectar_inicio_tabela(tabela, caixas_texto)
            if topo is not None:
                return max(0.0, topo - 4.0), "ANCHOR_FALLBACK"
            diagnostico = "rotulos=ausentes geometria=nao_aplicavel"
            if modelo == "modelo-1":
                avaliacao = ancoras.avaliar_geometria(
                    modelo,
                    tabela=tabela,
                    caixas_texto=caixas_texto,
                    celulas=celulas,
                )
                if avaliacao.ancora is not None:
                    return (
                        max(0.0, avaliacao.ancora.y),
                        ancoras.AVISO_ANCORA_GEOMETRICA,
                    )
                diagnostico = f"rotulos=ausentes {avaliacao.resumo()}"
            raise ErroDeteccao(
                CodigoFalha.ANCHOR_NOT_FOUND,
                "Nao foi possivel localizar o inicio da tabela de ponto.",
                diagnostico=diagnostico,
            ) from exc
        diagnostico = "geometria=nao_tentada"
        if modelo == "modelo-2" and exc.recuperavel_por_geometria:
            avaliacao = ancoras.avaliar_geometria(
                modelo,
                tabela=tabela,
                caixas_texto=caixas_texto,
                celulas=celulas,
            )
            if avaliacao.ancora is not None:
                return (
                    max(0.0, avaliacao.ancora.y),
                    ancoras.AVISO_ANCORA_GEOMETRICA,
                )
            diagnostico = avaliacao.resumo()
        elif modelo == "modelo-2":
            diagnostico = "geometria=nao_tentada(falha_nao_recuperavel)"
        raise ErroDeteccao(
            exc.codigo, exc.mensagem, diagnostico=diagnostico
        ) from exc

    if ancora is None:
        return _inicio_generico(tabela, caixas_texto), None
    return max(0.0, ancora.y), None


def _extrair(
    data: dict[str, Any],
    largura: int,
    altura: int,
    *,
    modelo: str | None = None,
    imagem: Any | None = None,
) -> DeteccaoEstrutural:
    tabelas = _tabelas(data)
    caixas_texto = tuple(_blocos_texto(data))

    if not tabelas:
        return DeteccaoEstrutural(
            largura=largura,
            altura=altura,
            tabela=None,
            cabecalho=None,
            caixas_texto=caixas_texto,
            modelo_versao=_versao,
        )

    tabela, confianca, _item = tabelas[0]
    # A tabela nao pode extrapolar a imagem.
    x1 = max(0.0, tabela.x)
    y1 = max(0.0, tabela.y)
    x2 = min(float(largura), tabela.x2)
    y2 = min(float(altura), tabela.y2)
    tabela = Caixa(x=x1, y=y1, largura=max(0.0, x2 - x1), altura=max(0.0, y2 - y1))

    # As ancoras precisam da tabela inteira (inclusive as celulas do cabecalho)
    # antes do ajuste do recorte.
    celulas_completas = _celulas(tabela, tabelas)
    linhas_completas = _linhas_horizontais(tabela, celulas_completas)

    inicio, aviso_ancora = _resolver_inicio(
        modelo,
        tabela,
        caixas_texto,
        celulas_completas,
        linhas_completas,
        imagem,
    )

    cabecalho: Caixa | None = None
    if inicio is not None and 8.0 <= inicio < tabela.y2:
        # Comeca na ancora, inclusive. O cabecalho cobre de y=0 ate essa
        # linha, o que mantem a validacao do servico (HEADER_NOT_DETECTED) e
        # evita que o recorte avance sobre dados pessoais acima da ancora.
        tabela = Caixa(
            x=tabela.x,
            y=inicio,
            largura=tabela.largura,
            altura=tabela.y2 - inicio,
        )
        cabecalho = Caixa(
            x=0.0,
            y=0.0,
            largura=float(largura),
            altura=inicio,
        )
    elif inicio is not None and modelo in _MODELOS_COM_ANCORA:
        # Modelos com ancora nao podem recuar para fora da tabela: seria
        # impossivel garantir que o cabecalho foi removido.
        raise ErroDeteccao(
            CodigoFalha.ANCHOR_NOT_CONFIDENT,
            "O inicio calculado nao esta dentro da tabela de ponto.",
        )

    # Limita o rodape após o ajuste do recorte
    if cabecalho is not None:
        fim = _limitar_rodape(tabela, caixas_texto)
        if fim is not None and tabela.y + 32.0 < fim < tabela.y2:
            tabela = Caixa(
                x=tabela.x,
                y=tabela.y,
                largura=tabela.largura,
                altura=fim - tabela.y,
            )

    celulas = _celulas(tabela, tabelas)
    linhas = _linhas_horizontais(tabela, celulas)

    return DeteccaoEstrutural(
        largura=largura,
        altura=altura,
        tabela=tabela,
        cabecalho=cabecalho,
        caixas_texto=caixas_texto,
        celulas=celulas,
        linhas_horizontais=linhas,
        confianca=confianca,
        modelo_versao=_versao,
        aviso_ancora=aviso_ancora,
    )


def detectar(
    dados: bytes,
    modelo: str | None = None,
) -> DeteccaoEstrutural:
    """Executa o PP-StructureV3 local e normaliza o resultado.

    ``modelo`` seleciona a ancora de recorte (``modelo-1``/``modelo-2``/
    ``modelo-3``). Sem ele, ou para os Modelos 4/5, o caminho generico anterior
    e preservado.
    """
    from PIL import Image

    try:
        with Image.open(BytesIO(dados)) as origem:
            largura, altura = origem.size
            imagem = origem.convert("RGB")
            try:
                import numpy as np

                entrada: Any = np.array(imagem)
            except ImportError:
                # Ambiente sem numpy (por exemplo, testes unitarios): o modelo
                # falso pode receber a propria imagem PIL.
                entrada = imagem.copy()
    except Exception as exc:  # noqa: BLE001
        raise ErroDeteccao(
            CodigoFalha.INVALID_INPUT,
            "Nao foi possivel abrir a imagem para deteccao.",
        ) from exc

    estrutura = _carregar_modelo()
    try:
        saida = estrutura.predict(entrada)
        if not isinstance(saida, (list, tuple)):
            saida = list(saida)
        if not saida:
            raise ErroDeteccao(
                CodigoFalha.TABLE_NOT_DETECTED,
                "O modelo estrutural nao retornou resultado.",
            )
        data = _como_dicionario(saida[0])
        return _extrair(
            data,
            largura,
            altura,
            modelo=modelo,
            imagem=imagem,
        )
    except ErroDeteccao:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ErroDeteccao(
            CodigoFalha.STRUCTURE_MODEL_ERROR,
            f"Falha na inferencia estrutural: {type(exc).__name__}.",
        ) from exc
