"""Testes das correcoes por modelo (docs/PLANO_CORRECOES_MODELOS_OCR.md).

Todos os dados sao sinteticos: nenhuma imagem real e usada e nada aqui prova o
comportamento em fotografias reais. Os testes cobrem a logica geometrica:

- AZ-xx: Modelo 1 (azul), variacao sem quinzena, via geometria da grade.
- PD-xx: Modelo 2 (padrao), ancora estrutural quando o texto falha.
- VD-xx: Modelo 3 (verde), sem nenhum fallback novo.
- SI-xx: Modelo 4 (simples), mascara localizada na anonimizacao.
"""

from __future__ import annotations

import math
import sys
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from backend.anonimizacao import ancoras, detector, service, validacao  # noqa: E402
from backend.anonimizacao.schemas import (  # noqa: E402
    Caixa,
    CelulaTabela,
    CodigoFalha,
    StatusAnonimizacao,
)
from backend.tests.imagens_sinteticas import (  # noqa: E402
    ALTURA,
    LARGURA,
    TOPO_TABELA,
    criar_folha_sintetica,
    deteccao_sintetica,
)

AVISO_GEOMETRICO = "ANCHOR_GEOMETRIC"

# Grade sintetica: 4 colunas x 8 linhas, celulas de 100 x 40 px.
_X0 = 60
_LARGURA_CEL = 100
_ALTURA_CEL = 40
_COLUNAS = 4
_LINHAS = 8


def _celulas_grade(
    *,
    y0: float,
    inclinacao: float = 0.0,
    linhas: int = _LINHAS,
    alturas: list[float] | None = None,
) -> list[list[float]]:
    """Celulas ``[x1, y1, x2, y2]`` de uma grade regular (opcionalmente inclinada)."""
    celulas: list[list[float]] = []
    for coluna in range(_COLUNAS):
        x1 = _X0 + coluna * _LARGURA_CEL
        deriva = inclinacao * (x1 - _X0)
        y = y0 + deriva
        for linha in range(linhas):
            altura = alturas[linha] if alturas else _ALTURA_CEL
            celulas.append([x1, y, x1 + _LARGURA_CEL, y + altura])
            y += altura
    return celulas


def _horarios_grade(y0: float) -> list[tuple[str, list[float]]]:
    """Textos de horario/dia dentro da grade (a partir da segunda linha)."""
    textos: list[tuple[str, list[float]]] = []
    for linha in range(1, _LINHAS):
        y = y0 + linha * _ALTURA_CEL + 10
        textos.append((f"{linha:02d}", [_X0 + 10, y, _X0 + 50, y + 18]))
        textos.append(("08:00", [_X0 + 110, y, _X0 + 190, y + 18]))
        textos.append(("17:00", [_X0 + 210, y, _X0 + 290, y + 18]))
    return textos


def _dados_ocr(
    textos: list[tuple[str, list[float]]],
    tabela: list[float],
    celulas: list[list[float]] | None = None,
) -> dict[str, object]:
    """Resultado minimo do PP-StructureV3, com celulas opcionais."""
    dados: dict[str, object] = {
        "layout_det_res": {
            "boxes": [
                {"label": "table", "score": 0.99, "coordinate": tabela}
            ]
        },
        "overall_ocr_res": {
            "rec_texts": [texto for texto, _caixa in textos],
            "rec_boxes": [caixa for _texto, caixa in textos],
        },
    }
    if celulas is not None:
        dados["table_res_list"] = [{"cell_box_list": celulas}]
    return dados


# Tabela maior que a grade: inclui a area administrativa acima (Modelo 1,
# variacao B) ou o cabecalho pessoal (Modelo 2).
_TABELA = [40.0, 100.0, 480.0, 760.0]
_Y_GRADE = 400.0
_ADMIN_CHINES = [
    ("二〇二五年十一月", [60, 130, 300, 160]),
    ("姓名 部门", [60, 190, 300, 220]),
]


# ---------------------------------------------------------------------------
# Modelo 1 (azul)
# ---------------------------------------------------------------------------


class TestModelo1VariacaoSemQuinzena(unittest.TestCase):
    def _dados_sem_quinzena(self, **kwargs: object) -> dict[str, object]:
        return _dados_ocr(
            _ADMIN_CHINES + _horarios_grade(_Y_GRADE),
            _TABELA,
            _celulas_grade(y0=_Y_GRADE, **kwargs),  # type: ignore[arg-type]
        )

    def test_az01_com_quinzena_mantem_ancora_atual(self) -> None:
        textos = [
            ("1ª Quinzena", [60, 380, 300, 398]),
        ] + _horarios_grade(_Y_GRADE)
        dados = _dados_ocr(
            textos, _TABELA, _celulas_grade(y0=_Y_GRADE)
        )

        deteccao = detector._extrair(dados, 600, 900, modelo="modelo-1")

        # Resolveu pela quinzena: sem aviso e sem usar a geometria.
        self.assertIsNone(deteccao.aviso_ancora)

    def test_az02_sem_quinzena_identifica_pela_geometria(self) -> None:
        deteccao = detector._extrair(
            self._dados_sem_quinzena(), 600, 900, modelo="modelo-1"
        )

        assert deteccao.tabela is not None
        assert deteccao.cabecalho is not None
        self.assertEqual(deteccao.aviso_ancora, AVISO_GEOMETRICO)
        # O recorte comeca no topo da grade, abaixo da area administrativa.
        self.assertEqual(deteccao.tabela.y, _Y_GRADE)
        self.assertEqual(deteccao.cabecalho.y2, _Y_GRADE)
        textos_acima = [
            caixa
            for caixa in deteccao.caixas_texto
            if caixa.y2 <= _Y_GRADE and caixa.texto
        ]
        self.assertTrue(textos_acima)  # a area chinesa fica fora do recorte

    def test_az03_inclinacao_moderada_ainda_e_reconhecida(self) -> None:
        # Deriva de ~0.9 altura de celula entre a primeira e a ultima coluna.
        inclinacao = (0.9 * _ALTURA_CEL) / (_COLUNAS * _LARGURA_CEL)
        deteccao = detector._extrair(
            self._dados_sem_quinzena(inclinacao=inclinacao),
            600,
            900,
            modelo="modelo-1",
        )

        assert deteccao.tabela is not None
        self.assertEqual(deteccao.aviso_ancora, AVISO_GEOMETRICO)
        self.assertEqual(deteccao.tabela.y, _Y_GRADE)

    def test_az03_inclinacao_excessiva_bloqueia(self) -> None:
        # Deriva ~3.0 alturas: acima do limite do Modelo 1 (2.5).
        inclinacao = (4.0 * _ALTURA_CEL) / (_COLUNAS * _LARGURA_CEL)
        with self.assertRaises(detector.ErroDeteccao) as contexto:
            detector._extrair(
                self._dados_sem_quinzena(inclinacao=inclinacao),
                600,
                900,
                modelo="modelo-1",
            )

        self.assertEqual(
            contexto.exception.codigo, CodigoFalha.ANCHOR_NOT_FOUND
        )

    def test_az04_sem_tabela_identificavel_nao_recorta(self) -> None:
        # Sem celulas: nao ha estrutura para ancorar.
        dados = _dados_ocr(
            _ADMIN_CHINES + _horarios_grade(_Y_GRADE), _TABELA, None
        )

        with self.assertRaises(detector.ErroDeteccao) as contexto:
            detector._extrair(dados, 600, 900, modelo="modelo-1")

        self.assertEqual(
            contexto.exception.codigo, CodigoFalha.ANCHOR_NOT_FOUND
        )

    def test_az02_cabecalho_de_coluna_mais_alto_entra_na_grade(self) -> None:
        # Linhas de alturas diferentes, mas alinhadas entre as colunas, sao uma
        # grade valida (por exemplo, rotulos de coluna em linha mais alta).
        alturas = [70.0] + [_ALTURA_CEL] * (_LINHAS - 1)
        deteccao = detector._extrair(
            self._dados_sem_quinzena(alturas=alturas),
            600,
            900,
            modelo="modelo-1",
        )

        assert deteccao.tabela is not None
        self.assertEqual(deteccao.aviso_ancora, AVISO_GEOMETRICO)
        self.assertEqual(deteccao.tabela.y, _Y_GRADE)

    def test_az04_sem_dados_de_horario_nao_e_aceita(self) -> None:
        dados = _dados_ocr(
            _ADMIN_CHINES, _TABELA, _celulas_grade(y0=_Y_GRADE)
        )

        with self.assertRaises(detector.ErroDeteccao):
            detector._extrair(dados, 600, 900, modelo="modelo-1")

    def test_fallback_de_rotulos_continua_antes_da_geometria(self) -> None:
        textos = [
            ("Dia", [60, 410, 110, 428]),
            ("Entrada", [170, 410, 250, 428]),
            ("Saida", [270, 410, 340, 428]),
        ] + _horarios_grade(_Y_GRADE)
        dados = _dados_ocr(textos, _TABELA, _celulas_grade(y0=_Y_GRADE))

        deteccao = detector._extrair(dados, 600, 900, modelo="modelo-1")

        self.assertEqual(deteccao.aviso_ancora, "ANCHOR_FALLBACK")


# ---------------------------------------------------------------------------
# Modelo 2 (padrao)
# ---------------------------------------------------------------------------


class TestModelo2AncoraEstrutural(unittest.TestCase):
    def _textos_cabecalho_incompleto(self) -> list[tuple[str, list[float]]]:
        # So tres rotulos (< 5 distintos): a ancora textual nao fecha.
        return [
            ("CTPS 000/00", [60, 120, 200, 140]),
            ("Dia", [60, 372, 110, 390]),
            ("Entrada", [170, 372, 250, 390]),
            ("Saida", [270, 372, 340, 390]),
        ] + _horarios_grade(_Y_GRADE)

    def test_pd02_cabecalho_incompleto_usa_a_grade(self) -> None:
        dados = _dados_ocr(
            self._textos_cabecalho_incompleto(),
            _TABELA,
            _celulas_grade(y0=_Y_GRADE),
        )

        deteccao = detector._extrair(dados, 600, 900, modelo="modelo-2")

        assert deteccao.tabela is not None
        self.assertEqual(deteccao.aviso_ancora, AVISO_GEOMETRICO)
        # Sobe sobre a faixa de rotulos (y=372), mas nunca sobre o CTPS (y=120).
        self.assertEqual(deteccao.tabela.y, 372.0)

    def test_pd02_nao_sobe_sobre_texto_que_nao_e_rotulo(self) -> None:
        textos = [
            ("Admissao 10/10/2024", [60, 372, 200, 390]),
            ("Saida", [270, 372, 340, 390]),
        ] + _horarios_grade(_Y_GRADE)
        dados = _dados_ocr(textos, _TABELA, _celulas_grade(y0=_Y_GRADE))

        deteccao = detector._extrair(dados, 600, 900, modelo="modelo-2")

        assert deteccao.tabela is not None
        self.assertEqual(deteccao.tabela.y, _Y_GRADE)

    def test_pd04_sem_grade_recorre_aos_rotulos(self) -> None:
        # Sem celulas a grade nao fecha; o ultimo recurso e o de rotulos do
        # Modelo 1 (ANCHOR_FALLBACK).
        dados = _dados_ocr(
            self._textos_cabecalho_incompleto(), _TABELA, None
        )

        deteccao = detector._extrair(dados, 600, 900, modelo="modelo-2")

        self.assertEqual(deteccao.aviso_ancora, "ANCHOR_FALLBACK")

    def test_pd04_sem_grade_e_sem_rotulos_bloqueia(self) -> None:
        textos = [
            ("CTPS 000/00", [60, 120, 200, 140]),
            ("Saida", [270, 372, 340, 390]),
        ] + _horarios_grade(_Y_GRADE)
        dados = _dados_ocr(textos, _TABELA, None)

        with self.assertRaises(detector.ErroDeteccao):
            detector._extrair(dados, 600, 900, modelo="modelo-2")

    def test_ambiguidade_da_ancora_textual_nao_e_recuperavel(self) -> None:
        # Saida presente, mas sem horarios abaixo: a falha NAO e de leitura de
        # texto, por isso a grade nao pode contorna-la.
        textos = [
            ("Saida", [270, 372, 340, 390]),
            ("CTPS 000/00", [60, 120, 200, 140]),
        ]
        dados = _dados_ocr(textos, _TABELA, _celulas_grade(y0=_Y_GRADE))

        with self.assertRaises(detector.ErroDeteccao) as contexto:
            detector._extrair(dados, 600, 900, modelo="modelo-2")

        self.assertEqual(
            contexto.exception.codigo, CodigoFalha.ANCHOR_NOT_CONFIDENT
        )

    def test_pd01_ancora_textual_completa_nao_usa_a_grade(self) -> None:
        textos = [
            ("Dia", [60, 372, 110, 390]),
            ("Entrada", [170, 372, 250, 390]),
            ("Intervalo", [260, 372, 330, 390]),
            ("Saida", [270, 392, 340, 408]),
            ("Hora Extra", [340, 372, 420, 390]),
            ("Assinatura", [420, 372, 480, 390]),
        ] + _horarios_grade(_Y_GRADE)
        dados = _dados_ocr(textos, _TABELA, _celulas_grade(y0=_Y_GRADE))

        deteccao = detector._extrair(dados, 600, 900, modelo="modelo-2")

        self.assertIsNone(deteccao.aviso_ancora)


# ---------------------------------------------------------------------------
# Modelo 3 (verde) e demais: sem fallback geometrico
# ---------------------------------------------------------------------------


class TestModelo3Preservado(unittest.TestCase):
    def _kwargs(self) -> dict[str, object]:
        celulas = tuple(
            CelulaTabela(caixa=Caixa(c[0], c[1], c[2] - c[0], c[3] - c[1]))
            for c in _celulas_grade(y0=_Y_GRADE)
        )
        textos = tuple(
            Caixa(c[0], c[1], c[2] - c[0], c[3] - c[1], texto=t)
            for t, c in _horarios_grade(_Y_GRADE)
        )
        return {
            "tabela": Caixa(40, 100, 440, 660),
            "caixas_texto": textos,
            "celulas": celulas,
        }

    def test_vd_funcao_geometrica_nao_se_aplica_a_outros_modelos(self) -> None:
        kwargs = self._kwargs()

        self.assertIsNotNone(ancoras.ancora_geometrica("modelo-1", **kwargs))
        self.assertIsNotNone(ancoras.ancora_geometrica("modelo-2", **kwargs))
        for modelo in ("modelo-3", "modelo-4", "modelo-5", None):
            self.assertIsNone(
                ancoras.ancora_geometrica(modelo, **kwargs),  # type: ignore[arg-type]
                modelo,
            )

    def test_vd02_modelo3_sem_quinzena_nao_usa_a_grade(self) -> None:
        # Mesma entrada que o Modelo 1 aceita pela geometria: o verde segue
        # bloqueando, exatamente como antes das correcoes.
        dados = _dados_ocr(
            _ADMIN_CHINES + _horarios_grade(_Y_GRADE),
            _TABELA,
            _celulas_grade(y0=_Y_GRADE),
        )

        with self.assertRaises(detector.ErroDeteccao) as contexto:
            detector._extrair(dados, 600, 900, modelo="modelo-3")

        self.assertEqual(
            contexto.exception.codigo, CodigoFalha.ANCHOR_NOT_FOUND
        )

    def test_vd01_modelo3_com_quinzena_mantem_resultado(self) -> None:
        textos = [
            ("2°Quinzena", [60, 380, 300, 398]),
        ] + _horarios_grade(_Y_GRADE)
        dados = _dados_ocr(textos, _TABELA, _celulas_grade(y0=_Y_GRADE))

        deteccao = detector._extrair(dados, 600, 900, modelo="modelo-3")

        self.assertIsNone(deteccao.aviso_ancora)

    def test_vd02_modelo3_fallback_de_rotulos_inalterado(self) -> None:
        textos = [
            ("Dia", [60, 410, 110, 428]),
            ("Entrada", [170, 410, 250, 428]),
            ("Saida", [270, 410, 340, 428]),
        ] + _horarios_grade(_Y_GRADE)
        dados = _dados_ocr(textos, _TABELA, _celulas_grade(y0=_Y_GRADE))

        deteccao = detector._extrair(dados, 600, 900, modelo="modelo-3")

        self.assertEqual(deteccao.aviso_ancora, "ANCHOR_FALLBACK")

    def test_vd_regra_de_conflito_original_vale_fora_dos_modelos_localizados(self) -> None:
        deteccao = _deteccao_falso_conflito()

        for modelo in (None, "modelo-3", "modelo-5"):
            analise = validacao.analisar_sensiveis(deteccao, modelo=modelo)
            self.assertTrue(analise.conflito, modelo)


# ---------------------------------------------------------------------------
# Modelo 4 (simples): mascara localizada
# ---------------------------------------------------------------------------


def _deteccao_falso_conflito():
    """Rotulo impresso sensivel na mesma coluna de um horario, em outra linha."""
    return deteccao_sintetica(
        caixas_tabela=(
            Caixa(150, TOPO_TABELA + 30, 200, 18, texto="LOCAL DO TRABALHO"),
        )
    )


class TestModelo4MascaraLocalizada(unittest.TestCase):
    def test_si01_falso_conflito_de_coluna_e_redigido_localmente(self) -> None:
        analise = validacao.analisar_sensiveis(
            _deteccao_falso_conflito(), modelo="modelo-4"
        )

        self.assertFalse(analise.conflito)
        self.assertEqual(
            [caixa.texto for caixa in analise.redigir], ["LOCAL DO TRABALHO"]
        )

    def test_si01_nenhuma_mascara_atinge_horarios_aprovados(self) -> None:
        deteccao = _deteccao_falso_conflito()
        analise = validacao.analisar_sensiveis(deteccao, modelo="modelo-4")

        horarios = [
            caixa
            for caixa in deteccao.caixas_texto
            if caixa.texto and validacao._parece_horario(caixa.texto)
        ]
        self.assertTrue(horarios)
        for mascara in analise.redigir:
            self.assertFalse(
                validacao._mascara_atinge_dados(mascara, horarios)
            )

    def test_si02_sobreposicao_real_mantem_bloqueio(self) -> None:
        # Caixa alta o bastante para cobrir parte do horario de 08:00 sem
        # estar na mesma linha dele.
        deteccao = deteccao_sintetica(
            caixas_tabela=(
                Caixa(150, TOPO_TABELA + 60, 100, 60, texto="Empregado: JOAO"),
            )
        )

        analise = validacao.analisar_sensiveis(deteccao, modelo="modelo-4")

        self.assertTrue(analise.conflito)
        self.assertEqual(analise.redigir, ())

    def test_si02_mesma_linha_de_horario_mantem_bloqueio(self) -> None:
        deteccao = deteccao_sintetica(
            caixas_tabela=(
                Caixa(500, TOPO_TABELA + 100, 150, 18, texto="Empregado: JOAO"),
            )
        )

        analise = validacao.analisar_sensiveis(deteccao, modelo="modelo-4")

        self.assertTrue(analise.conflito)

    def test_si05_mascara_com_folga_sobre_horario_e_reprovada(self) -> None:
        # A caixa termina 2 px acima do horario: o padding da redacao (2 px) mais
        # a imprecisao do OCR chegaria nele.
        deteccao = deteccao_sintetica(
            caixas_tabela=(
                Caixa(
                    160,
                    TOPO_TABELA + 100 - 18 - 2,
                    90,
                    18,
                    texto="Empregado: JOAO",
                ),
            )
        )

        analise = validacao.analisar_sensiveis(deteccao, modelo="modelo-4")

        self.assertTrue(analise.conflito)

    def test_si05_texto_misto_com_horario_nunca_e_mascarado(self) -> None:
        deteccao = deteccao_sintetica(
            caixas_tabela=(
                Caixa(
                    55,
                    TOPO_TABELA + 200,
                    300,
                    18,
                    texto="Assinatura 08:00 12:00",
                ),
            )
        )

        analise = validacao.analisar_sensiveis(deteccao, modelo="modelo-4")

        self.assertTrue(analise.conflito)
        self.assertEqual(analise.redigir, ())

    def test_regiao_extra_do_usuario_continua_cobrindo_conflito(self) -> None:
        deteccao = deteccao_sintetica(
            caixas_tabela=(
                Caixa(500, TOPO_TABELA + 100, 150, 18, texto="Empregado: JOAO"),
            )
        )
        extra = (495.0, float(TOPO_TABELA + 95), 160.0, 28.0)

        analise = validacao.analisar_sensiveis(
            deteccao, extras=(extra,), modelo="modelo-4"
        )

        self.assertFalse(analise.conflito)
        self.assertEqual(len(analise.redigir), 1)


def _imagem_com_marcas(
    horarios: list[Caixa], sensiveis: list[Caixa]
) -> bytes:
    """Folha branca com blocos pretos nos horarios e nos campos sensiveis."""
    imagem = Image.new("RGB", (LARGURA, ALTURA), "white")
    desenho = ImageDraw.Draw(imagem)
    for caixa in horarios + sensiveis:
        desenho.rectangle(
            (caixa.x + 2, caixa.y + 2, caixa.x2 - 2, caixa.y2 - 2),
            fill="black",
        )
    buffer = BytesIO()
    imagem.save(buffer, format="PNG")
    return buffer.getvalue()


class TestModelo4PipelineAnonimizacao(unittest.IsolatedAsyncioTestCase):
    async def _processar(self, deteccao, dados: bytes, modelo: str | None):
        with patch.object(
            service.detector,
            "detectar",
            lambda _dados, modelo=None: deteccao,
        ):
            resultados = await service.processar_lote(
                [("folha.png", dados)], modelo=modelo
            )
        return resultados[0]

    async def test_si04_anonimizado_corretamente_segue_para_o_ocr(self) -> None:
        deteccao = _deteccao_falso_conflito()
        rotulo = Caixa(150, TOPO_TABELA + 30, 200, 18)
        horario = Caixa(160, TOPO_TABELA + 100, 90, 18)
        dados = _imagem_com_marcas([horario], [rotulo])

        resultado = await self._processar(deteccao, dados, "modelo-4")

        self.assertEqual(resultado.status, StatusAnonimizacao.APPROVED)
        assert resultado.geometria is not None
        origem_x = resultado.geometria.x
        origem_y = resultado.geometria.y
        with Image.open(BytesIO(bytes(resultado.artefato))) as saida:
            saida = saida.convert("L")

            def brilho(caixa: Caixa) -> float:
                centro = (
                    int(caixa.x + caixa.largura / 2 - origem_x),
                    int(caixa.y + caixa.altura / 2 - origem_y),
                )
                return float(saida.getpixel(centro))

            # Campo sensivel pintado de branco; horario preservado (preto).
            self.assertGreaterEqual(brilho(rotulo), 245)
            self.assertLess(brilho(horario), 60)

    async def test_si01_mesma_imagem_bloqueia_fora_do_modelo4(self) -> None:
        deteccao = _deteccao_falso_conflito()
        dados = criar_folha_sintetica()

        for modelo in (None, "modelo-3"):
            resultado = await self._processar(deteccao, dados, modelo)
            self.assertEqual(resultado.status, StatusAnonimizacao.BLOCKED)
            self.assertEqual(
                resultado.codigo_falha, CodigoFalha.SENSITIVE_FIELD_CONFLICT
            )
            self.assertIsNone(resultado.artefato)

    async def test_si02_sobreposicao_real_bloqueia_sem_artefato(self) -> None:
        deteccao = deteccao_sintetica(
            caixas_tabela=(
                Caixa(150, TOPO_TABELA + 60, 100, 60, texto="Empregado: JOAO"),
            )
        )

        resultado = await self._processar(
            deteccao, criar_folha_sintetica(), "modelo-4"
        )

        self.assertEqual(resultado.status, StatusAnonimizacao.BLOCKED)
        self.assertEqual(
            resultado.codigo_falha, CodigoFalha.SENSITIVE_FIELD_CONFLICT
        )
        self.assertIsNone(resultado.artefato)

    async def test_si03_sem_tabela_ou_cabecalho_mantem_bloqueio(self) -> None:
        from dataclasses import replace

        base = _deteccao_falso_conflito()

        sem_tabela = replace(base, tabela=None, cabecalho=None)
        resultado = await self._processar(
            sem_tabela, criar_folha_sintetica(), "modelo-4"
        )
        self.assertEqual(resultado.status, StatusAnonimizacao.BLOCKED)
        self.assertEqual(
            resultado.codigo_falha, CodigoFalha.TABLE_NOT_DETECTED
        )

        sem_cabecalho = replace(base, cabecalho=None)
        resultado = await self._processar(
            sem_cabecalho, criar_folha_sintetica(), "modelo-4"
        )
        self.assertEqual(resultado.status, StatusAnonimizacao.BLOCKED)
        self.assertEqual(
            resultado.codigo_falha, CodigoFalha.HEADER_NOT_DETECTED
        )


# ---------------------------------------------------------------------------
# Diagnostico em log (nao altera nenhuma decisao)
# ---------------------------------------------------------------------------


def _celulas_tabela(grade: list[list[float]]) -> tuple[CelulaTabela, ...]:
    return tuple(
        CelulaTabela(caixa=Caixa(c[0], c[1], c[2] - c[0], c[3] - c[1]))
        for c in grade
    )


def _textos_caixas(
    textos: list[tuple[str, list[float]]],
) -> tuple[Caixa, ...]:
    return tuple(
        Caixa(c[0], c[1], c[2] - c[0], c[3] - c[1], texto=t)
        for t, c in textos
    )


class TestDiagnosticoGeometria(unittest.TestCase):
    def _avaliar(self, modelo, grade, textos):
        return ancoras.avaliar_geometria(
            modelo,
            tabela=Caixa(40, 100, 440, 660),
            caixas_texto=_textos_caixas(textos),
            celulas=_celulas_tabela(grade),
        )

    def test_grade_valida_informa_metricas(self) -> None:
        resultado = self._avaliar(
            "modelo-1",
            _celulas_grade(y0=_Y_GRADE),
            _horarios_grade(_Y_GRADE),
        )

        self.assertIsNotNone(resultado.ancora)
        self.assertEqual(resultado.motivo, "ok")
        self.assertEqual(resultado.metricas["colunas_grade"], _COLUNAS)
        self.assertIn("geometria=ok", resultado.resumo())

    def test_motivos_de_rejeicao(self) -> None:
        horarios = _horarios_grade(_Y_GRADE)

        self.assertEqual(
            self._avaliar("modelo-1", [], horarios).motivo, "poucas_celulas"
        )
        self.assertEqual(
            self._avaliar(
                "modelo-1", _celulas_grade(y0=_Y_GRADE, linhas=3), horarios
            ).motivo,
            "poucas_celulas",
        )
        inclinada = _celulas_grade(
            y0=_Y_GRADE,
            inclinacao=(4.0 * _ALTURA_CEL) / (_COLUNAS * _LARGURA_CEL),
        )
        resultado = self._avaliar("modelo-1", inclinada, horarios)
        self.assertEqual(resultado.motivo, "deriva_alta")
        self.assertGreater(resultado.metricas["deriva_alturas"], 1.5)
        self.assertEqual(
            self._avaliar(
                "modelo-1", _celulas_grade(y0=_Y_GRADE), []
            ).motivo,
            "poucos_dados",
        )

    def test_modelos_sem_geometria_sao_nao_aplicaveis(self) -> None:
        for modelo in ("modelo-3", "modelo-4", "modelo-5", None):
            resultado = self._avaliar(
                modelo,
                _celulas_grade(y0=_Y_GRADE),
                _horarios_grade(_Y_GRADE),
            )
            self.assertIsNone(resultado.ancora, modelo)
            self.assertEqual(resultado.motivo, "nao_aplicavel")

    def test_erro_do_detector_carrega_o_diagnostico(self) -> None:
        dados = _dados_ocr(
            _ADMIN_CHINES + _horarios_grade(_Y_GRADE), _TABELA, None
        )

        with self.assertRaises(detector.ErroDeteccao) as modelo1:
            detector._extrair(dados, 600, 900, modelo="modelo-1")
        self.assertIn("geometria=poucas_celulas", modelo1.exception.diagnostico)

        with self.assertRaises(detector.ErroDeteccao) as modelo3:
            detector._extrair(dados, 600, 900, modelo="modelo-3")
        self.assertIn("nao_aplicavel", modelo3.exception.diagnostico)

    def test_modelo2_nao_recuperavel_informa_que_nao_tentou(self) -> None:
        textos = [
            ("Saida", [270, 372, 340, 390]),
            ("CTPS 000/00", [60, 120, 200, 140]),
        ]
        dados = _dados_ocr(textos, _TABELA, _celulas_grade(y0=_Y_GRADE))

        with self.assertRaises(detector.ErroDeteccao) as contexto:
            detector._extrair(dados, 600, 900, modelo="modelo-2")

        self.assertIn(
            "falha_nao_recuperavel", contexto.exception.diagnostico
        )


class TestDiagnosticoConflito(unittest.TestCase):
    def test_tipo_de_conflito_por_regra(self) -> None:
        falso = _deteccao_falso_conflito()

        original = validacao.analisar_sensiveis(falso)
        self.assertEqual(
            original.detalhe, "regra=original conflitos=1 tipos=coluna"
        )

        sobreposta = deteccao_sintetica(
            caixas_tabela=(
                Caixa(150, TOPO_TABELA + 60, 100, 60, texto="Empregado: JOAO"),
            )
        )
        localizada = validacao.analisar_sensiveis(
            sobreposta, modelo="modelo-4"
        )
        self.assertEqual(
            localizada.detalhe, "regra=localizada conflitos=1 tipos=mascara"
        )

        mesma_linha = deteccao_sintetica(
            caixas_tabela=(
                Caixa(500, TOPO_TABELA + 100, 150, 18, texto="Empregado: JOAO"),
            )
        )
        self.assertEqual(
            validacao.analisar_sensiveis(
                mesma_linha, modelo="modelo-4"
            ).detalhe,
            "regra=localizada conflitos=1 tipos=mesma_linha",
        )

    def test_sem_conflito_nao_tem_detalhe(self) -> None:
        analise = validacao.analisar_sensiveis(
            _deteccao_falso_conflito(), modelo="modelo-4"
        )

        self.assertFalse(analise.conflito)
        self.assertIsNone(analise.detalhe)

    def test_decisao_nao_muda_com_o_diagnostico(self) -> None:
        # O detalhe e so informativo: conflito e redigir seguem os mesmos.
        analise = validacao.analisar_sensiveis(_deteccao_falso_conflito())

        self.assertTrue(analise.conflito)
        self.assertEqual(analise.redigir, ())


class TestLogDeDiagnostico(unittest.IsolatedAsyncioTestCase):
    async def _processar(self, deteccao, dados: bytes, modelo: str):
        with patch.object(
            service.detector,
            "detectar",
            lambda _dados, modelo=None: deteccao,
        ), patch.object(service.logs, "warning") as aviso, patch.object(
            service.logs, "info"
        ) as info:
            resultados = await service.processar_lote(
                [("folha.png", dados)], lote="lote-teste", modelo=modelo
            )
        mensagens = [chamada.args[1] for chamada in aviso.call_args_list]
        mensagens += [chamada.args[1] for chamada in info.call_args_list]
        return resultados[0], mensagens

    async def test_bloqueio_registra_modelo_motivo_e_diagnostico(self) -> None:
        deteccao = deteccao_sintetica(
            caixas_tabela=(
                Caixa(150, TOPO_TABELA + 60, 100, 60, texto="Empregado: JOAO"),
            )
        )

        resultado, mensagens = await self._processar(
            deteccao, criar_folha_sintetica(), "modelo-4"
        )

        self.assertEqual(resultado.status, StatusAnonimizacao.BLOCKED)
        linha = next(m for m in mensagens if "bloqueada" in m)
        self.assertIn("modelo=modelo-4", linha)
        self.assertIn("SENSITIVE_FIELD_CONFLICT", linha)
        self.assertIn("regra=localizada", linha)
        self.assertIn("tipos=mascara", linha)

    async def test_log_nunca_contem_texto_lido_da_imagem(self) -> None:
        deteccao = deteccao_sintetica(
            caixas_tabela=(
                Caixa(150, TOPO_TABELA + 60, 100, 60, texto="Empregado: JOAO"),
            )
        )

        _resultado, mensagens = await self._processar(
            deteccao, criar_folha_sintetica(), "modelo-4"
        )

        registro = "\n".join(mensagens)
        for proibido in ("JOAO", "SILVA", "123.456", "EMPRESA"):
            self.assertNotIn(proibido, registro)

    async def test_aprovada_registra_modelo_e_aviso(self) -> None:
        deteccao = _deteccao_falso_conflito()
        dados = _imagem_com_marcas(
            [Caixa(160, TOPO_TABELA + 100, 90, 18)],
            [Caixa(150, TOPO_TABELA + 30, 200, 18)],
        )

        resultado, mensagens = await self._processar(
            deteccao, dados, "modelo-4"
        )

        self.assertEqual(resultado.status, StatusAnonimizacao.APPROVED)
        linha = next(m for m in mensagens if "aprovada" in m)
        self.assertIn("modelo=modelo-4", linha)
        self.assertIn("aviso=-", linha)

    async def test_sem_tabela_registra_contagem_de_caixas(self) -> None:
        from dataclasses import replace

        sem_tabela = replace(
            _deteccao_falso_conflito(), tabela=None, cabecalho=None
        )

        _resultado, mensagens = await self._processar(
            sem_tabela, criar_folha_sintetica(), "modelo-1"
        )

        linha = next(m for m in mensagens if "bloqueada" in m)
        self.assertIn("TABLE_NOT_DETECTED", linha)
        self.assertIn("caixas_texto=", linha)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Grade girada (foto torta) e colunas de larguras diferentes
# ---------------------------------------------------------------------------


def _grade_girada(graus: float, *, y0: float = _Y_GRADE, linhas: int = _LINHAS):
    """Celulas da grade regular giradas em torno do seu centro."""
    import math

    base = _celulas_grade(y0=y0, linhas=linhas)
    cx = _X0 + _COLUNAS * _LARGURA_CEL / 2
    cy = y0 + linhas * _ALTURA_CEL / 2
    rad = math.radians(graus)
    girada = []
    for x1, y1, x2, y2 in base:
        mx, my = (x1 + x2) / 2 - cx, (y1 + y2) / 2 - cy
        nx = cx + mx * math.cos(rad) - my * math.sin(rad)
        ny = cy + mx * math.sin(rad) + my * math.cos(rad)
        meia_l, meia_a = (x2 - x1) / 2, (y2 - y1) / 2
        girada.append([nx - meia_l, ny - meia_a, nx + meia_l, ny + meia_a])
    return girada


class TestGeometriaTolerante(unittest.TestCase):
    def _avaliar(self, grade, modelo="modelo-1", tabela=None):
        return ancoras.avaliar_geometria(
            modelo,
            tabela=tabela or Caixa(40, 100, 440, 660),
            caixas_texto=_textos_caixas(_horarios_grade(_Y_GRADE)),
            celulas=_celulas_tabela(grade),
        )

    def test_grade_reta_nao_e_girada(self) -> None:
        resultado = self._avaliar(_celulas_grade(y0=_Y_GRADE))

        self.assertEqual(resultado.motivo, "ok")
        self.assertEqual(resultado.metricas["angulo"], 0)

    def test_foto_torta_com_linhas_inclinadas_e_reconhecida(self) -> None:
        # Antes: colunas desalinhadas pela rotacao viravam grade_curta.
        for graus in (-4, -2, 2, 4):
            resultado = self._avaliar(_grade_girada(graus))
            self.assertEqual(resultado.motivo, "ok", graus)
            self.assertEqual(resultado.metricas["colunas_grade"], _COLUNAS)

    def test_grade_alta_e_torta_precisa_do_giro(self) -> None:
        # 30 linhas girando 4 graus: o desvio horizontal passa da folga e,
        # sem girar, as colunas se misturam.
        resultado = self._avaliar(_grade_girada(4, linhas=30), tabela=Caixa(40, 100, 440, 1500))

        self.assertEqual(resultado.motivo, "ok")
        self.assertEqual(resultado.metricas["colunas_grade"], _COLUNAS)
        self.assertNotEqual(resultado.metricas["angulo"], 0)

    def test_ancora_geometrica_fica_em_coordenadas_originais(self) -> None:
        grade = _grade_girada(3)
        topo_original = min(celula[1] for celula in grade)

        resultado = self._avaliar(grade)

        assert resultado.ancora is not None
        self.assertAlmostEqual(resultado.ancora.y, topo_original, places=3)

    def test_coluna_estreita_de_dia_nao_quebra_a_grade(self) -> None:
        grade = _celulas_grade(y0=_Y_GRADE)
        for linha in range(_LINHAS):
            y = _Y_GRADE + linha * _ALTURA_CEL
            grade.append([_X0 - 40, y, _X0 - 10, y + _ALTURA_CEL])
        resultado = self._avaliar(grade)

        self.assertEqual(resultado.motivo, "ok")
        self.assertEqual(resultado.metricas["colunas_grade"], _COLUNAS + 1)

    def test_modelo2_tambem_usa_a_grade_girada(self) -> None:
        resultado = self._avaliar(_grade_girada(3), modelo="modelo-2")

        self.assertEqual(resultado.motivo, "ok")

    def test_modelo3_continua_sem_geometria(self) -> None:
        resultado = self._avaliar(_grade_girada(3), modelo="modelo-3")

        self.assertIsNone(resultado.ancora)
        self.assertEqual(resultado.motivo, "nao_aplicavel")


# ---------------------------------------------------------------------------
# Mascara localizada nos Modelos 1 e 2
# ---------------------------------------------------------------------------


class TestMascaraLocalizadaModelos1E2(unittest.TestCase):
    def test_falso_conflito_de_coluna_e_redigido_nos_modelos_1_e_2(self) -> None:
        for modelo in ("modelo-1", "modelo-2"):
            analise = validacao.analisar_sensiveis(
                _deteccao_falso_conflito(), modelo=modelo
            )
            self.assertFalse(analise.conflito, modelo)
            self.assertEqual(
                [caixa.texto for caixa in analise.redigir],
                ["LOCAL DO TRABALHO"],
                modelo,
            )

    def test_mesma_linha_continua_bloqueando(self) -> None:
        deteccao = deteccao_sintetica(
            caixas_tabela=(
                Caixa(500, TOPO_TABELA + 100, 150, 18, texto="Empregado: JOAO"),
            )
        )
        for modelo in ("modelo-1", "modelo-2"):
            analise = validacao.analisar_sensiveis(deteccao, modelo=modelo)
            self.assertTrue(analise.conflito, modelo)
            self.assertIn("mesma_linha", analise.detalhe or "")

    def test_mascara_sobre_horario_continua_bloqueando(self) -> None:
        deteccao = deteccao_sintetica(
            caixas_tabela=(
                Caixa(150, TOPO_TABELA + 60, 100, 60, texto="Empregado: JOAO"),
            )
        )
        for modelo in ("modelo-1", "modelo-2"):
            analise = validacao.analisar_sensiveis(deteccao, modelo=modelo)
            self.assertTrue(analise.conflito, modelo)

    def test_modelo3_segue_a_regra_original(self) -> None:
        analise = validacao.analisar_sensiveis(
            _deteccao_falso_conflito(), modelo="modelo-3"
        )

        self.assertTrue(analise.conflito)
        self.assertIn("regra=original", analise.detalhe or "")


# ---------------------------------------------------------------------------
# Modelo 1: ancora da faixa do mes (年 / 月份) no cartao chines sem quinzena
# ---------------------------------------------------------------------------

AVISO_FAIXA_MES = "ANCHOR_MONTH_BAND"

# Tabela que comeca acima do cabecalho das colunas e inclui a faixa do mes.
_TABELA_FM = [40.0, 60.0, 480.0, 760.0]
_Y_DADOS_FM = 285.0
_BASE_FAIXA = 220.0  # y2 da caixa "年月份" na foto reta
_NOME_MANUSCRITO = "Anderson Rafael"


def _textos_faixa_mes(
    *,
    faixa: bool = True,
    cabecalho: bool = True,
    dados: bool = True,
    inclinacao: float = 0.0,
    extra: tuple[tuple[str, list[float]], ...] = (),
) -> list[tuple[str, list[float]]]:
    """Cartao chines: nome, 单位, salario, titulo, faixa 年/月份, cabecalho, dados."""
    textos: list[tuple[str, list[float]]] = [
        (_NOME_MANUSCRITO, [100, 30, 300, 70]),
        ("单位", [60, 75, 110, 100]),
        ("日薪", [60, 105, 120, 125]),
        ("考勤表", [180, 135, 340, 175]),
    ]
    if faixa:
        textos.append(("年月份", [300, 190, 440, _BASE_FAIXA]))
    if cabecalho:
        textos += [
            ("上午", [90, 232, 200, 252]),
            ("下午", [210, 232, 320, 252]),
            ("加班", [330, 232, 430, 252]),
            ("日期上班下班", [60, 255, 200, 275]),
        ]
    if dados:
        textos += _horarios_grade(_Y_DADOS_FM)
    textos += list(extra)
    if not inclinacao:
        return textos
    inclinados = []
    for texto, (x1, y1, x2, y2) in textos:
        desvio = inclinacao * ((x1 + x2) / 2 - _TABELA_FM[0])
        inclinados.append((texto, [x1, y1 + desvio, x2, y2 + desvio]))
    return inclinados


def _extrair_fm(textos, modelo="modelo-1", celulas=None):
    dados = _dados_ocr(textos, _TABELA_FM, celulas)
    return detector._extrair(dados, 600, 900, modelo=modelo)


def _avaliar_fm(textos, modelo="modelo-1"):
    return ancoras.avaliar_faixa_mes(
        modelo,
        tabela=Caixa(*_TABELA_FM[:2], _TABELA_FM[2] - _TABELA_FM[0], _TABELA_FM[3] - _TABELA_FM[1]),
        caixas_texto=_textos_caixas(textos),
    )


class TestModelo1FaixaDoMes(unittest.TestCase):
    def test_fm01_faixa_em_branco_corta_logo_abaixo_dela(self) -> None:
        textos = _textos_faixa_mes()
        deteccao = _extrair_fm(textos)

        assert deteccao.tabela is not None
        assert deteccao.cabecalho is not None
        self.assertEqual(deteccao.aviso_ancora, AVISO_FAIXA_MES)
        # Abaixo da base da faixa e acima do centro de 上午/下午/加班 (242).
        self.assertGreater(deteccao.tabela.y, _BASE_FAIXA)
        self.assertLess(deteccao.tabela.y, 242.0)
        self.assertEqual(deteccao.cabecalho.y2, deteccao.tabela.y)
        # Nome, 单位, salario, titulo e a propria faixa ficam fora do recorte.
        for caixa in deteccao.caixas_texto:
            if caixa.texto in (_NOME_MANUSCRITO, "单位", "日薪", "考勤表", "年月份"):
                self.assertLessEqual(caixa.y2, deteccao.tabela.y, caixa.texto)
        # O cabecalho das colunas e os dados ficam dentro.
        for caixa in deteccao.caixas_texto:
            if caixa.texto in ("上午", "下午", "加班"):
                self.assertGreater(caixa.y + caixa.altura / 2, deteccao.tabela.y)

    def test_fm02_manuscrito_dentro_e_vazando_para_cima_nao_move_o_limite(self) -> None:
        sem = _extrair_fm(_textos_faixa_mes())
        com = _extrair_fm(
            _textos_faixa_mes(
                extra=(
                    # "Setembro" invade o titulo; "9" fica dentro da faixa.
                    ("Setembro", [330, 150, 420, 205]),
                    ("9", [300, 195, 322, 216]),
                )
            )
        )

        assert sem.tabela is not None and com.tabela is not None
        self.assertEqual(com.aviso_ancora, AVISO_FAIXA_MES)
        self.assertEqual(com.tabela.y, sem.tabela.y)
        for caixa in com.caixas_texto:
            if caixa.texto in ("Setembro", "9"):
                self.assertLessEqual(caixa.y2, com.tabela.y, caixa.texto)

    def test_fm03_foto_inclinada_limite_entre_a_faixa_e_o_cabecalho(self) -> None:
        for graus in (-4, -2, 2, 4):
            inclinacao = math.tan(math.radians(graus))
            textos = _textos_faixa_mes(inclinacao=inclinacao)
            resultado = _avaliar_fm(textos)

            self.assertEqual(resultado.motivo, "ok", graus)
            assert resultado.ancora is not None
            caixas = {t: Caixa(b[0], b[1], b[2] - b[0], b[3] - b[1]) for t, b in textos}
            base = caixas["年月份"].y2
            centros = [
                caixas[t].y + caixas[t].altura / 2 for t in ("上午", "下午")
            ]
            self.assertGreater(resultado.ancora.y, base, graus)
            self.assertLess(resultado.ancora.y, min(centros), graus)

    def test_fm03_limite_que_cortaria_o_cabecalho_bloqueia(self) -> None:
        # Caixa da faixa inflada (foto torta): a base passa do centro de 上午.
        textos = _textos_faixa_mes()
        textos = [
            (t, [300, 190, 440, 250] if t == "年月份" else b) for t, b in textos
        ]
        resultado = _avaliar_fm(textos)
        self.assertEqual(resultado.motivo, "limite_incerto")
        self.assertTrue(resultado.bloqueia)

        with self.assertRaises(detector.ErroDeteccao) as contexto:
            _extrair_fm(textos)
        self.assertEqual(contexto.exception.codigo, CodigoFalha.ANCHOR_NOT_CONFIDENT)
        self.assertIn("faixa_mes=limite_incerto", contexto.exception.diagnostico)

    def test_fm04_sem_faixa_cai_no_fallback_de_rotulos(self) -> None:
        textos = [
            ("考勤表", [180, 300, 340, 340]),
            ("Dia", [60, 410, 110, 428]),
            ("Entrada", [170, 410, 250, 428]),
            ("Saida", [270, 410, 340, 428]),
        ] + _horarios_grade(_Y_GRADE)
        dados = _dados_ocr(textos, _TABELA, _celulas_grade(y0=_Y_GRADE))

        deteccao = detector._extrair(dados, 600, 900, modelo="modelo-1")

        self.assertEqual(deteccao.aviso_ancora, "ANCHOR_FALLBACK")
        resultado = _avaliar_fm(textos)
        self.assertEqual(resultado.motivo, "nao_encontrada")
        self.assertFalse(resultado.bloqueia)

    def test_fm05_duas_faixas_bloqueiam(self) -> None:
        textos = _textos_faixa_mes(extra=(("月份", [300, 150, 440, 180]),))
        resultado = _avaliar_fm(textos)
        self.assertEqual(resultado.motivo, "ambigua")

        with self.assertRaises(detector.ErroDeteccao) as contexto:
            _extrair_fm(textos)
        self.assertEqual(contexto.exception.codigo, CodigoFalha.ANCHOR_NOT_CONFIDENT)
        self.assertIn("faixa_mes=ambigua", contexto.exception.diagnostico)

    def test_fm05_rotulo_fora_da_regiao_da_tabela_bloqueia(self) -> None:
        # "年月份" abaixo do cabecalho das colunas (nos dados) e ambiguo.
        textos = _textos_faixa_mes(extra=(("年月份", [300, 600, 440, 630]),))
        self.assertEqual(_avaliar_fm(textos).motivo, "ambigua")

        # E fora do eixo horizontal da tabela.
        textos = _textos_faixa_mes(extra=(("月份", [520, 190, 590, 220]),))
        self.assertEqual(_avaliar_fm(textos).motivo, "ambigua")

    def test_fm06_com_quinzena_resultado_identico(self) -> None:
        base = [("1ª Quinzena", [60, 380, 300, 398])] + _horarios_grade(_Y_GRADE)
        chines = [
            t for t in _textos_faixa_mes(dados=False)
        ] + base
        celulas = _celulas_grade(y0=_Y_GRADE)
        for modelo in ("modelo-1", "modelo-3"):
            sem = detector._extrair(
                _dados_ocr(base, _TABELA, celulas), 600, 900, modelo=modelo
            )
            com = detector._extrair(
                _dados_ocr(chines, _TABELA, celulas), 600, 900, modelo=modelo
            )
            assert sem.tabela is not None and com.tabela is not None
            self.assertIsNone(com.aviso_ancora, modelo)
            self.assertEqual(com.tabela.y, sem.tabela.y, modelo)

    def test_fm07_nunca_e_usada_em_outros_modelos(self) -> None:
        textos = _textos_faixa_mes()
        for modelo in ("modelo-2", "modelo-3", "modelo-4", "modelo-5", None):
            resultado = _avaliar_fm(textos, modelo=modelo)
            self.assertIsNone(resultado.ancora, modelo)
            self.assertEqual(resultado.motivo, "nao_aplicavel", modelo)
        for modelo in ("modelo-4", "modelo-5"):
            deteccao = _extrair_fm(textos, modelo=modelo)
            self.assertNotEqual(deteccao.aviso_ancora, AVISO_FAIXA_MES, modelo)
        for modelo in ("modelo-2", "modelo-3"):
            try:
                deteccao = _extrair_fm(textos, modelo=modelo)
            except detector.ErroDeteccao:
                continue
            self.assertNotEqual(deteccao.aviso_ancora, AVISO_FAIXA_MES, modelo)

    def test_fm08_sem_cabecalho_ou_sem_dados_nao_usa_a_ancora(self) -> None:
        sem_cabecalho = _avaliar_fm(_textos_faixa_mes(cabecalho=False))
        self.assertEqual(sem_cabecalho.motivo, "sem_cabecalho")
        self.assertFalse(sem_cabecalho.bloqueia)

        sem_dados = _avaliar_fm(_textos_faixa_mes(dados=False))
        self.assertEqual(sem_dados.motivo, "sem_cabecalho")

        with self.assertRaises(detector.ErroDeteccao) as contexto:
            _extrair_fm(_textos_faixa_mes(dados=False))
        self.assertEqual(contexto.exception.codigo, CodigoFalha.ANCHOR_NOT_FOUND)
        self.assertIn("faixa_mes=sem_cabecalho", contexto.exception.diagnostico)

    def test_fm09_sem_contexto_chines_nao_se_aplica(self) -> None:
        textos = [t for t in _textos_faixa_mes() if t[0] not in ("考勤表", "日期上班下班")]
        self.assertEqual(_avaliar_fm(textos).motivo, "sem_contexto")

    def test_fm10_faixa_lida_em_duas_caixas_vale_como_uma(self) -> None:
        textos = [t for t in _textos_faixa_mes() if t[0] != "年月份"] + [
            ("年", [300, 190, 330, 220]),
            ("月份", [380, 190, 440, 220]),
        ]
        resultado = _avaliar_fm(textos)

        self.assertEqual(resultado.motivo, "ok")
        self.assertEqual(resultado.metricas["faixas"], 1)

    def test_fm11_ocr_errado_nos_rotulos_usa_caixa_curta_da_regiao(self) -> None:
        textos = [
            ("牛月伤" if t == "年月份" else t, b) for t, b in _textos_faixa_mes()
        ]
        resultado = _avaliar_fm(textos)

        self.assertEqual(resultado.motivo, "ok")
        assert resultado.ancora is not None
        self.assertGreater(resultado.ancora.y, _BASE_FAIXA)

    def test_fm12_linha_horizontal_abaixo_da_faixa_define_o_limite(self) -> None:
        resultado = ancoras.avaliar_faixa_mes(
            "modelo-1",
            tabela=Caixa(40, 60, 440, 700),
            caixas_texto=_textos_caixas(_textos_faixa_mes()),
            linhas=(180.0, _BASE_FAIXA + 4.0, 260.0),
        )

        assert resultado.ancora is not None
        self.assertEqual(resultado.ancora.y, _BASE_FAIXA + 5.0)

    def test_fm13_diagnostico_nao_contem_texto_lido(self) -> None:
        with self.assertRaises(detector.ErroDeteccao) as contexto:
            _extrair_fm(_textos_faixa_mes(dados=False))
        diagnostico = contexto.exception.diagnostico or ""
        for proibido in (_NOME_MANUSCRITO, "单位", "考勤表", "年月份"):
            self.assertNotIn(proibido, diagnostico)
        self.assertIn("dist_cabecalho_alturas=", diagnostico)


class TestAncoraDispensaConflitoInterno(unittest.TestCase):
    """Corte decidido por ancora: sem segunda validacao de campo sensivel."""

    def _deteccao(self, ancorada: bool, texto: str = "Empregado: JOAO"):
        deteccao = deteccao_sintetica(
            caixas_tabela=(Caixa(500, TOPO_TABELA + 100, 150, 18, texto=texto),)
        )
        return replace(deteccao, ancorada=ancorada)

    def test_modelos_1_2_e_3_ancorados_nao_bloqueiam_nem_redigem(self) -> None:
        for modelo in ("modelo-1", "modelo-2", "modelo-3"):
            analise = validacao.analisar_sensiveis(
                self._deteccao(True), modelo=modelo
            )
            self.assertFalse(analise.conflito, modelo)
            self.assertEqual(analise.redigir, (), modelo)

    def test_sem_ancora_continua_bloqueando(self) -> None:
        for modelo in ("modelo-1", "modelo-2", "modelo-3"):
            analise = validacao.analisar_sensiveis(
                self._deteccao(False), modelo=modelo
            )
            self.assertTrue(analise.conflito, modelo)

    def test_modelos_4_e_5_nao_sao_afetados(self) -> None:
        for modelo in ("modelo-4", "modelo-5", None):
            analise = validacao.analisar_sensiveis(
                self._deteccao(True), modelo=modelo
            )
            self.assertTrue(analise.conflito, modelo)

    def test_sobra_de_cabecalho_na_linha_de_corte_continua_sendo_redigida(self) -> None:
        sobra = Caixa(160, TOPO_TABELA - 12, 80, 30, texto="JOAO DA SILVA")
        deteccao = replace(
            deteccao_sintetica(caixas_tabela=(sobra,)), ancorada=True
        )

        analise = validacao.analisar_sensiveis(deteccao, modelo="modelo-1")

        self.assertFalse(analise.conflito)
        self.assertIn(sobra, analise.redigir)

    def test_carimbo_lido_como_numero_colado_com_quinzena_nao_bloqueia(self) -> None:
        # Caso real (Alisson): o OCR cola os carimbos de horario em numeros que
        # a regra tomava por CPF; a faixa "2 Quinzena" ja ancorou o recorte.
        carimbo = "81306801347"
        self.assertEqual(validacao.classificar_texto(carimbo), "sensivel")
        textos = [
            ("2° Quinzena", [60, 380, 300, 398]),
            (carimbo, [70, 450, 150, 468]),
        ] + _horarios_grade(_Y_GRADE)
        celulas = _celulas_grade(y0=_Y_GRADE)

        for modelo in ("modelo-1", "modelo-3"):
            deteccao = detector._extrair(
                _dados_ocr(textos, _TABELA, celulas), 600, 900, modelo=modelo
            )
            self.assertTrue(deteccao.ancorada, modelo)
            analise = validacao.analisar_sensiveis(deteccao, modelo=modelo)
            self.assertFalse(analise.conflito, modelo)

    def test_ancorada_so_quando_ha_ancora_do_modelo(self) -> None:
        textos = [("1ª Quinzena", [60, 380, 300, 398])] + _horarios_grade(_Y_GRADE)
        dados = _dados_ocr(textos, _TABELA, _celulas_grade(y0=_Y_GRADE))

        for modelo in ("modelo-1", "modelo-3"):
            self.assertTrue(
                detector._extrair(dados, 600, 900, modelo=modelo).ancorada, modelo
            )
        for modelo in ("modelo-4", "modelo-5", None):
            self.assertFalse(
                detector._extrair(dados, 600, 900, modelo=modelo).ancorada, modelo
            )

    def test_faixa_do_mes_tambem_conta_como_ancora(self) -> None:
        deteccao = _extrair_fm(_textos_faixa_mes())

        self.assertEqual(deteccao.aviso_ancora, AVISO_FAIXA_MES)
        self.assertTrue(deteccao.ancorada)


class TestDerivaModelo1(unittest.TestCase):
    def _avaliar(self, modelo: str):
        # Deriva ~2.0 alturas (0.75 x 2.7): entre 1.5 e 2.5.
        inclinacao = (2.7 * _ALTURA_CEL) / (_COLUNAS * _LARGURA_CEL)
        return ancoras.avaliar_geometria(
            modelo,
            tabela=Caixa(40, 100, 440, 660),
            caixas_texto=_textos_caixas(_horarios_grade(_Y_GRADE)),
            celulas=_celulas_tabela(_celulas_grade(y0=_Y_GRADE, inclinacao=inclinacao)),
        )

    def test_modelo1_aceita_deriva_ate_2_5_alturas(self) -> None:
        resultado = self._avaliar("modelo-1")

        self.assertEqual(resultado.motivo, "ok")
        self.assertGreater(resultado.metricas["deriva_alturas"], 1.5)

    def test_modelo2_continua_com_limite_de_1_5(self) -> None:
        resultado = self._avaliar("modelo-2")

        self.assertEqual(resultado.motivo, "deriva_alta")


class TestMascaraColunasDireitaModelo2(unittest.TestCase):
    """Modelo 2: so ate a Saida final; Hora Extra e Assinatura sao mascaradas."""

    def _deteccao(self):
        topo = TOPO_TABELA
        rotulos = (
            # a fixture base ja traz Entrada (160) e Saida (310)
            Caixa(380, topo + 8, 60, 18, texto="Entrada"),
            Caixa(460, topo + 8, 50, 18, texto="Saida"),
            Caixa(560, topo + 8, 60, 18, texto="Entrada"),
            Caixa(670, topo + 8, 50, 18, texto="Saida"),
            Caixa(790, topo + 8, 90, 18, texto="Assinatura"),
        )
        dados = (
            Caixa(460, topo + 60, 60, 20, texto="17:08"),
            Caixa(120, topo + 60, 60, 20, texto="08:00"),
        )
        base = deteccao_sintetica(caixas_tabela=rotulos + dados)
        return replace(base, ancorada=True)

    def test_mascara_comeca_depois_da_saida_final(self) -> None:
        deteccao = self._deteccao()
        mascaras = validacao.mascara_colunas_direita_modelo2(deteccao)

        self.assertTrue(mascaras)
        x_corte = min(m.x for m in mascaras)
        self.assertGreater(x_corte, 510)  # horarios da Saida final preservados
        self.assertLessEqual(x_corte, 560)

    def test_analise_inclui_mascara_so_no_modelo2_ancorado(self) -> None:
        deteccao = self._deteccao()
        analise = validacao.analisar_sensiveis(deteccao, modelo="modelo-2")
        self.assertTrue(any(m.x > 510 for m in analise.redigir))
        outro = validacao.analisar_sensiveis(deteccao, modelo="modelo-3")
        self.assertFalse(any(m.x > 510 for m in outro.redigir))

    def test_sem_rotulos_nao_mascara(self) -> None:
        deteccao = replace(deteccao_sintetica(), ancorada=True)
        self.assertEqual(validacao.mascara_colunas_direita_modelo2(deteccao), ())


class TestMascaraAssinaturasModelo5(unittest.TestCase):
    """Modelo 5: assinaturas de ENTRADA e SAIDA mascaradas, horas preservadas."""

    def _deteccao(self):
        topo = TOPO_TABELA
        caixas = [
            Caixa(266, topo + 8, 90, 24, texto="ASSINATURA"),
            Caixa(413, topo + 10, 57, 24, texto="HORAS"),
            Caixa(532, topo + 8, 90, 24, texto="ASSINATURA"),
            Caixa(680, topo + 10, 66, 24, texto="HORAS"),
        ]
        for i in range(10):
            y = topo + 50 + i * 35
            caixas += [
                Caixa(98, y, 110, 28, texto="020926"),
                Caixa(223, y, 140, 28, texto="nome"),
                Caixa(405, y, 75, 26, texto="08:06"),
                Caixa(510, y, 140, 28, texto="nome"),
                Caixa(676, y, 80, 26, texto="12:07"),
            ]
        return deteccao_sintetica(caixas_tabela=tuple(caixas))

    def test_mascara_as_duas_assinaturas_e_preserva_datas_e_horas(self) -> None:
        mascaras = validacao.mascara_assinaturas_modelo5(self._deteccao())

        self.assertTrue(mascaras)
        for horario in ((405, 480), (676, 756), (98, 208)):
            self.assertFalse(
                any(m.x < horario[1] and m.x2 > horario[0] for m in mascaras),
                horario,
            )
        self.assertTrue(any(m.x <= 223 and m.x2 >= 363 for m in mascaras))
        self.assertTrue(any(m.x <= 510 and m.x2 >= 650 for m in mascaras))

    def test_analise_aplica_so_no_modelo5(self) -> None:
        deteccao = self._deteccao()
        self.assertTrue(
            validacao.analisar_sensiveis(deteccao, modelo="modelo-5").redigir
        )
        outro = validacao.analisar_sensiveis(deteccao, modelo="modelo-4").redigir
        self.assertFalse(any(m.altura == 40.0 for m in outro))

    def test_sem_rotulos_nao_mascara(self) -> None:
        self.assertEqual(
            validacao.mascara_assinaturas_modelo5(deteccao_sintetica()), ()
        )
