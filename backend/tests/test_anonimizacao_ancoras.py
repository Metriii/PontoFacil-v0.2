"""Testes das ancoras de recorte por modelo (1, 2 e 3)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from backend.anonimizacao import ancoras, detector, validacao  # noqa: E402
from backend.anonimizacao.schemas import (  # noqa: E402
    Caixa,
    CelulaTabela,
    CodigoFalha,
)
from backend.tests.imagens_sinteticas import (  # noqa: E402
    TOPO_TABELA,
    deteccao_sintetica,
)


def _dados_ocr(textos: list[tuple[str, list[float]]], tabela: list[float]):
    """Monta um resultado minimo do PP-StructureV3 para ``_extrair``."""
    return {
        "layout_det_res": {
            "boxes": [
                {
                    "label": "table",
                    "score": 0.99,
                    "coordinate": tabela,
                }
            ]
        },
        "overall_ocr_res": {
            "rec_texts": [texto for texto, _caixa in textos],
            "rec_boxes": [caixa for _texto, caixa in textos],
        },
    }


class TestAncoraModelo1E3(unittest.TestCase):
    def test_modelo1_usa_celula_e_nao_invade_o_texto_anterior(self) -> None:
        tabela = Caixa(140.2, 43.7, 698.4, 1423.8)
        caixas = (
            Caixa(163.0, 503.0, 73.0, 22.0, texto="Trabalho"),
            Caixa(434.0, 549.0, 142.0, 31.0, texto="Quinzena"),
        )
        celulas = (
            CelulaTabela(
                caixa=Caixa(143.8, 517.3, 685.7, 85.2),
                linha=17,
                coluna=0,
            ),
        )

        ancora = ancoras.ancora_modelo1(
            tabela=tabela,
            caixas_texto=caixas,
            celulas=celulas,
            linhas=(43.7, 517.3, 602.5),
        )

        # O topo da faixa e 526: a celula comeca em 517.3, mas o texto
        # "Trabalho" termina em 525 e nao pode reaparecer.
        self.assertEqual(ancora.y, 526.0)
        self.assertEqual(ancora.origem, "QUINZENA_MODELO1")

    def test_modelo3_preserva_faixa_azul_inteira(self) -> None:
        tabela = Caixa(172.0, 527.6, 626.9, 764.9)
        caixas = (
            Caixa(
                258.0,
                422.0,
                441.0,
                44.0,
                texto="Nos termos da portaria 3.162 e 3.081.",
            ),
            Caixa(393.0, 485.0, 179.0, 36.0, texto="2\u00b0Quinzena"),
        )
        imagem = Image.new("RGB", (900, 800), "white")
        desenho = ImageDraw.Draw(imagem)
        desenho.rectangle((172, 465, 798, 535), fill=(70, 110, 180))

        ancora = ancoras.ancora_modelo3(
            tabela=tabela,
            caixas_texto=caixas,
            imagem=imagem,
        )

        # O aviso legal termina em 466 e a faixa comeca em 465: o detector
        # tolera o antialiasing e nao corta a borda da faixa.
        self.assertEqual(ancora.y, 465.0)
        self.assertEqual(ancora.origem, "QUINZENA_MODELO3")

    def test_duas_quinzenas_distintas_sao_ambiguas(self) -> None:
        tabela = Caixa(40.0, 300.0, 820.0, 800.0)
        caixas = (
            Caixa(400.0, 500.0, 120.0, 24.0, texto="1\u00aa Quinzena"),
            Caixa(400.0, 900.0, 120.0, 24.0, texto="2\u00aa Quinzena"),
        )

        with self.assertRaises(ancoras.ErroAncora) as contexto:
            ancoras.ancora_modelo1(tabela=tabela, caixas_texto=caixas)

        self.assertEqual(
            contexto.exception.codigo, CodigoFalha.ANCHOR_NOT_CONFIDENT
        )


class TestAncoraModelo2(unittest.TestCase):
    def _dados_camila(self) -> dict[str, object]:
        return _dados_ocr(
            [
                ("CTPS/Sere: 1516084/2411", [16, 110, 135, 126]),
                ("Tipo de Escala: Sem Escala", [133, 119, 255, 137]),
                ("Admissao:19/10/2024", [16, 123, 117, 136]),
                ("Assinatura", [484, 124, 542, 140]),
                ("Hora Extra", [364, 128, 423, 145]),
                ("Intervalo", [178, 134, 225, 150]),
                ("Dias", [29, 141, 55, 157]),
                ("Saida", [280, 146, 314, 161]),
                ("Saida", [408, 142, 442, 157]),
                ("Entrada", [211, 147, 256, 164]),
                ("19:1019.061", [202, 156, 333, 181]),
                ("17:01", [262, 175, 331, 196]),
            ],
            [6.3, 1.6, 579.4, 941.3],
        )

    def test_corta_no_topo_do_bloco_de_cabecalho(self) -> None:
        deteccao = detector._extrair(
            self._dados_camila(), 586, 945, modelo="modelo-2"
        )

        assert deteccao.tabela is not None
        assert deteccao.cabecalho is not None
        self.assertEqual(deteccao.tabela.y, 124.0)
        self.assertEqual(deteccao.cabecalho.y2, 124.0)
        self.assertIsNone(deteccao.aviso_ancora)
        # Sem corte horizontal: x, largura e x2 permanecem os da tabela.
        self.assertEqual(deteccao.tabela.x, 6.3)
        self.assertAlmostEqual(deteccao.tabela.x2, 579.4, places=1)

    def test_sem_saida_bloqueia(self) -> None:
        dados = _dados_ocr(
            [
                ("Dia", [50, 310, 110, 330]),
                ("Entrada", [160, 310, 280, 330]),
                ("08:00", [160, 350, 250, 370]),
            ],
            [40, 300, 860, 1100],
        )

        # Sem Saida, o Modelo 2 recorre aos rotulos de coluna (como o Modelo 1).
        deteccao = detector._extrair(dados, 900, 1200, modelo="modelo-2")
        self.assertEqual(deteccao.aviso_ancora, "ANCHOR_FALLBACK")

    def test_sem_saida_e_sem_rotulos_bloqueia(self) -> None:
        dados = _dados_ocr(
            [("08:00", [160, 350, 250, 370]), ("17:00", [260, 350, 350, 370])],
            [40, 300, 860, 1100],
        )

        with self.assertRaises(detector.ErroDeteccao) as contexto:
            detector._extrair(dados, 900, 1200, modelo="modelo-2")

        self.assertEqual(contexto.exception.codigo, CodigoFalha.ANCHOR_NOT_FOUND)


class TestIntegracaoAncoras(unittest.TestCase):
    def test_modelo1_sem_quinzena_usa_fallback_com_aviso(self) -> None:
        dados = _dados_ocr(
            [
                ("Dia", [50, 310, 110, 330]),
                ("Entrada", [160, 310, 280, 330]),
                ("Saida", [310, 310, 400, 330]),
                ("08:00", [160, 350, 250, 370]),
            ],
            [40, 300, 860, 1100],
        )

        deteccao = detector._extrair(dados, 900, 1200, modelo="modelo-1")

        assert deteccao.tabela is not None
        self.assertEqual(deteccao.tabela.y, 306.0)
        self.assertEqual(deteccao.aviso_ancora, "ANCHOR_FALLBACK")

    def test_modelo1_sem_ancora_nem_rotulos_bloqueia(self) -> None:
        dados = _dados_ocr(
            [("XYZABC", [50, 310, 110, 330])],
            [40, 300, 860, 1100],
        )

        with self.assertRaises(detector.ErroDeteccao) as contexto:
            detector._extrair(dados, 900, 1200, modelo="modelo-1")

        self.assertEqual(contexto.exception.codigo, CodigoFalha.ANCHOR_NOT_FOUND)

    def test_modelo4_mantem_caminho_generico(self) -> None:
        dados = _dados_ocr(
            [
                ("1a Quinzena", [50, 250, 200, 280]),
                ("08:00", [160, 350, 250, 370]),
            ],
            [40, 300, 860, 1100],
        )

        deteccao = detector._extrair(dados, 900, 1200, modelo="modelo-4")

        assert deteccao.tabela is not None
        self.assertEqual(deteccao.tabela.y, 250.0)
        self.assertIsNone(deteccao.aviso_ancora)


class TestValidacaoAposAncoras(unittest.TestCase):
    def test_caixa_que_termina_no_limite_e_sobra_de_cabecalho(self) -> None:
        deteccao = deteccao_sintetica(
            caixas_tabela=(
                Caixa(50, TOPO_TABELA - 2, 100, 8, texto="CTPS"),
            )
        )

        analise = validacao.analisar_sensiveis(deteccao)

        self.assertFalse(analise.conflito)
        self.assertTrue(any(c.texto == "CTPS" for c in analise.redigir))

    def test_cabecalho_redigido_nao_bloqueia_o_recorte(self) -> None:
        cabecalho = Caixa(50, TOPO_TABELA - 5, 100, 6, texto="CTPS")
        deteccao = deteccao_sintetica(caixas_tabela=(cabecalho,))
        analise = validacao.analisar_sensiveis(deteccao)
        recorte = Caixa(34, float(TOPO_TABELA), 832, 806)

        resultado = validacao.validar_recorte(
            deteccao, recorte, analise.redigir
        )

        self.assertTrue(resultado.ok, resultado.mensagem)


if __name__ == "__main__":
    unittest.main()
