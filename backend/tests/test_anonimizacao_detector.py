"""Testes da leitura tolerante do resultado do PP-StructureV3."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from backend.anonimizacao import detector  # noqa: E402
from backend.anonimizacao.schemas import Caixa  # noqa: E402
from backend.tests.imagens_sinteticas import criar_folha_sintetica  # noqa: E402


class TestExtracaoResultado(unittest.TestCase):
    def test_extrai_tabela_texto_e_celulas(self) -> None:
        dados = {
            "layout_det_res": {
                "boxes": [
                    {
                        "label": "table",
                        "score": 0.98,
                        "coordinate": [40, 300, 860, 1100],
                    },
                    {
                        "label": "text",
                        "score": 0.9,
                        "coordinate": [50, 40, 420, 70],
                    },
                ]
            },
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_content": "Nome: JOAO DA SILVA",
                    "block_bbox": [50, 40, 420, 70],
                },
                {
                    "block_label": "table",
                    "block_content": "",
                    "block_bbox": [40, 300, 860, 1100],
                },
            ],
            "overall_ocr_res": {
                "rec_texts": [
                    "Nome: JOAO DA SILVA",
                    "Dia",
                    "Entrada",
                    "08:00",
                ],
                "rec_boxes": [
                    [50, 40, 420, 70],
                    [50, 310, 110, 330],
                    [160, 310, 280, 330],
                    [160, 350, 250, 370],
                ],
            },
            "table_res_list": [
                {
                    "table_box": [40, 300, 860, 1100],
                    "cell_box_list": [
                        [40, 310, 160, 360],
                        [160, 310, 280, 360],
                    ],
                }
            ],
        }

        deteccao = detector._extrair(dados, 900, 1200)

        self.assertIsNotNone(deteccao.tabela)
        assert deteccao.tabela is not None
        self.assertEqual(deteccao.tabela.x, 40)
        # O topo passa a ser a linha de rotulos (310) menos a margem de 4px.
        self.assertEqual(deteccao.tabela.y, 306)
        self.assertIsNotNone(deteccao.cabecalho)
        assert deteccao.cabecalho is not None
        self.assertEqual(deteccao.cabecalho.y2, 306)
        self.assertEqual(len(deteccao.celulas), 2)
        self.assertEqual(deteccao.caixas_texto[0].texto, "Nome: JOAO DA SILVA")

    def test_extrai_texto_do_ocr_global_quando_nao_ha_parsing(self) -> None:
        dados = {
            "layout_det_res": {
                "boxes": [
                    {
                        "label": "table",
                        "score": 0.9,
                        "coordinate": [40, 300, 860, 1100],
                    }
                ]
            },
            "overall_ocr_res": {
                "rec_texts": ["Dia", "08:00"],
                "rec_boxes": [[50, 310, 110, 330], [160, 350, 250, 370]],
            },
        }

        deteccao = detector._extrair(dados, 900, 1200)

        self.assertEqual(
            [caixa.texto for caixa in deteccao.caixas_texto],
            ["Dia", "08:00"],
        )

    def test_sem_tabela_nao_inventa_regiao(self) -> None:
        deteccao = detector._extrair({"layout_det_res": {"boxes": []}}, 900, 1200)
        self.assertIsNone(deteccao.tabela)
        self.assertIsNone(deteccao.cabecalho)

    def test_aceita_escalares_tipo_numpy(self) -> None:
        class Numero:
            def __init__(self, valor: float) -> None:
                self.valor = valor

            def __float__(self) -> float:
                return float(self.valor)

        coordenadas = detector._coordenadas(
            [Numero(1), Numero(2), Numero(3), Numero(4)]
        )
        self.assertEqual(coordenadas, (1.0, 2.0, 3.0, 4.0))

    def test_nao_avalia_array_como_booleano(self) -> None:
        class Array:
            def __init__(self, itens: list[object]) -> None:
                self.itens = itens

            def __len__(self) -> int:
                return len(self.itens)

            def __iter__(self):
                return iter(self.itens)

            def __bool__(self) -> bool:
                raise AssertionError("array nao pode ser avaliado como booleano")

        dados = {
            "overall_ocr_res": {
                "rec_texts": ["Dia"],
                "rec_boxes": Array([[10, 20, 30, 40]]),
            }
        }

        caixas = detector._caixas_ocr(dados)

        self.assertEqual(len(caixas), 1)
        self.assertEqual(caixas[0].texto, "Dia")

    def test_le_layout_de_objeto_paddle_sem_contains(self) -> None:
        class Resultado(dict):
            def __contains__(self, _chave: object) -> bool:
                raise AssertionError("__contains__ nao deve ser usado")

        dados = Resultado(
            {
                "layout_det_res": Resultado(
                    {
                        "boxes": [
                            {
                                "label": "table",
                                "score": 0.9,
                                "coordinate": [40, 300, 860, 1100],
                            }
                        ]
                    }
                )
            }
        )

        tabelas = detector._tabelas(dados)

        self.assertEqual(len(tabelas), 1)
        self.assertEqual(tabelas[0][0].y, 300)


class TestTituloSecao(unittest.TestCase):
    def test_localiza_quinzena_com_variacoes_de_ocr(self) -> None:
        caixas = (
            Caixa(400, 520, 120, 24, texto="2\u00aa Quinzena"),
            Caixa(400, 480, 120, 24, texto="1\u00b0 Quinzena"),
            Caixa(400, 600, 120, 24, texto="Quinzena"),
        )

        y = detector._localizar_titulo_secao(caixas, ["Quinzena"])

        self.assertEqual(y, 480)

    def test_titulo_ausente_nao_inventa_posicao(self) -> None:
        caixas = (Caixa(50, 100, 80, 20, texto="Entrada"),)

        self.assertIsNone(
            detector._localizar_titulo_secao(caixas, ["Quinzena"])
        )
        self.assertIsNone(detector._localizar_titulo_secao((), ["Dias"]))

    def test_amanda_exige_dias_para_identificar_o_modelo(self) -> None:
        apenas_intervalo = (Caixa(200, 239, 120, 24, texto="Intervalo"),)
        self.assertIsNone(
            detector._localizar_titulo_amanda(apenas_intervalo)
        )

        com_dias = apenas_intervalo + (
            Caixa(30, 258, 60, 24, texto="Dias"),
        )
        self.assertEqual(detector._localizar_titulo_amanda(com_dias), 239)

    def test_extrair_inicia_recorte_na_linha_do_titulo(self) -> None:
        dados = {
            "layout_det_res": {
                "boxes": [
                    {
                        "label": "table",
                        "score": 0.9,
                        "coordinate": [40, 300, 860, 1100],
                    }
                ]
            },
            "overall_ocr_res": {
                "rec_texts": ["1a Quinzena", "08:00"],
                "rec_boxes": [[50, 250, 200, 280], [160, 350, 250, 370]],
            },
        }

        deteccao = detector._extrair(dados, 900, 1200)

        assert deteccao.tabela is not None
        assert deteccao.cabecalho is not None
        self.assertEqual(deteccao.tabela.y, 250)
        self.assertEqual(deteccao.cabecalho.y2, 250)

    def test_titulo_no_cabecalho_pessoal_nao_puxa_o_recorte(self) -> None:
        # "Dias" aparece no cabecalho (y=40), muito acima da tabela. Nao pode
        # definir o inicio do recorte e reexpor nome/CPF.
        dados = {
            "layout_det_res": {
                "boxes": [
                    {
                        "label": "table",
                        "score": 0.9,
                        "coordinate": [40, 300, 860, 1100],
                    }
                ]
            },
            "overall_ocr_res": {
                "rec_texts": ["Dias", "08:00"],
                "rec_boxes": [[50, 40, 110, 70], [160, 350, 250, 370]],
            },
        }

        deteccao = detector._extrair(dados, 900, 1200)

        assert deteccao.tabela is not None
        self.assertEqual(deteccao.tabela.y, 300)


class TestCarregamentoModelo(unittest.TestCase):
    def setUp(self) -> None:
        self._modelo = detector._modelo
        self._versao = detector._versao
        detector._modelo = None
        detector._versao = None

    def tearDown(self) -> None:
        detector._modelo = self._modelo
        detector._versao = self._versao

    def test_carrega_modelo_com_configuracao_minima(self) -> None:
        capturado: dict[str, object] = {}

        class PPStructureV3Falso:
            def __init__(self, **kwargs: object) -> None:
                capturado.update(kwargs)

        modulo = types.ModuleType("paddleocr")
        modulo.PPStructureV3 = PPStructureV3Falso  # type: ignore[attr-defined]
        modulo.__version__ = "3.7.0"  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"paddleocr": modulo}):
            detector._carregar_modelo()

        self.assertFalse(capturado["use_formula_recognition"])
        self.assertFalse(capturado["use_seal_recognition"])
        self.assertFalse(capturado["use_chart_recognition"])
        self.assertFalse(capturado["use_doc_unwarping"])
        self.assertFalse(capturado["use_region_detection"])
        self.assertTrue(capturado["use_table_recognition"])
        self.assertEqual(
            capturado["text_detection_model_name"], "PP-OCRv5_mobile_det"
        )
        self.assertEqual(
            capturado["text_recognition_model_name"], "PP-OCRv5_mobile_rec"
        )


class TestDeteccaoLocal(unittest.TestCase):
    def test_detectar_normaliza_saida_do_modelo(self) -> None:
        dados = {
            "layout_det_res": {
                "boxes": [
                    {
                        "label": "table",
                        "score": 0.9,
                        "coordinate": [40, 300, 860, 1100],
                    }
                ]
            },
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_content": "Empresa Alfa",
                    "block_bbox": [50, 40, 420, 70],
                }
            ],
        }

        class ModeloFalso:
            def predict(self, _entrada):
                return [dados]

        with patch.object(detector, "_carregar_modelo", return_value=ModeloFalso()):
            deteccao = detector.detectar(criar_folha_sintetica())

        self.assertIsNotNone(deteccao.tabela)
        self.assertEqual(deteccao.largura, 900)
        self.assertEqual(deteccao.altura, 1200)


class TestSegundaTentativaLayout(unittest.TestCase):
    """Limiar de layout menor so quando a 1a passada nao acha tabela (Modelo 1)."""

    SEM_TABELA = {"layout_det_res": {"boxes": []}}
    COM_TABELA = {
        "layout_det_res": {
            "boxes": [
                {"label": "table", "score": 0.48, "coordinate": [40, 300, 860, 1100]}
            ]
        }
    }

    def _modelo(self, respostas):
        chamadas: list[dict] = []

        class ModeloFalso:
            def predict(self, _entrada, **kwargs):
                chamadas.append(kwargs)
                if isinstance(respostas, Exception):
                    if kwargs:
                        raise respostas
                    return [TestSegundaTentativaLayout.SEM_TABELA]
                return [respostas[1 if kwargs else 0]]

        return ModeloFalso(), chamadas

    def _detectar(self, modelo_falso, modelo):
        with patch.object(
            detector, "_carregar_modelo", return_value=modelo_falso
        ), patch.object(detector, "_resolver_inicio", return_value=(None, None)):
            return detector.detectar(criar_folha_sintetica(), modelo=modelo)

    def test_modelo1_sem_tabela_repete_com_limiar_menor(self) -> None:
        falso, chamadas = self._modelo((self.SEM_TABELA, self.COM_TABELA))
        deteccao = self._detectar(falso, "modelo-1")
        self.assertIsNotNone(deteccao.tabela)
        self.assertEqual(
            chamadas, [{}, {"layout_threshold": detector._LAYOUT_THRESHOLD_RETRY}]
        )

    def test_primeira_passada_com_tabela_nao_repete(self) -> None:
        falso, chamadas = self._modelo((self.COM_TABELA, self.COM_TABELA))
        deteccao = self._detectar(falso, "modelo-1")
        self.assertIsNotNone(deteccao.tabela)
        self.assertEqual(chamadas, [{}])

    def test_modelo3_nao_repete(self) -> None:
        falso, chamadas = self._modelo((self.SEM_TABELA, self.COM_TABELA))
        deteccao = self._detectar(falso, "modelo-3")
        self.assertIsNone(deteccao.tabela)
        self.assertEqual(chamadas, [{}])

    def test_sem_modelo_nao_repete(self) -> None:
        falso, chamadas = self._modelo((self.SEM_TABELA, self.COM_TABELA))
        deteccao = self._detectar(falso, None)
        self.assertIsNone(deteccao.tabela)
        self.assertEqual(chamadas, [{}])

    def test_segunda_passada_sem_tabela_mantem_bloqueio(self) -> None:
        falso, chamadas = self._modelo((self.SEM_TABELA, self.SEM_TABELA))
        deteccao = self._detectar(falso, "modelo-1")
        self.assertIsNone(deteccao.tabela)
        self.assertEqual(len(chamadas), 2)

    def test_falha_na_repeticao_preserva_resultado_original(self) -> None:
        falso, chamadas = self._modelo(TypeError("limiar nao suportado"))
        deteccao = self._detectar(falso, "modelo-1")
        self.assertIsNone(deteccao.tabela)
        self.assertEqual(len(chamadas), 2)


if __name__ == "__main__":
    unittest.main()


class TestTabelaModelo5PorRotulos(unittest.TestCase):
    """Modelo 5: sem ``table`` do layout, a regiao sai dos rotulos impressos."""

    def _caixas(self, com_rotulos: bool = True):
        caixas = [
            Caixa(254, 303, 490, 47, texto="REGISTRO DE PONTO NOME"),
            Caixa(0, 338, 34, 32, texto="RAS"),
        ]
        if com_rotulos:
            caixas += [
                Caixa(295, 346, 111, 32, texto="ENTRADA"),
                Caixa(567, 346, 78, 31, texto="SAIDA"),
                Caixa(102, 367, 74, 29, texto="DIA MES"),
                Caixa(266, 369, 90, 30, texto="ASSINATURA"),
                Caixa(413, 377, 57, 25, texto="HORAS"),
                Caixa(532, 373, 90, 28, texto="ASSINATURA"),
                Caixa(680, 366, 66, 29, texto="HORAS"),
            ]
        for i in range(12):
            y = 400 + i * 35
            caixas.append(Caixa(98, y, 110, 30, texto="020926"))
            caixas.append(Caixa(407, y, 72, 26, texto="08:06"))
        return tuple(caixas)

    def test_corte_abaixo_do_titulo_e_nome_fica_acima(self) -> None:
        resultado = detector._tabela_modelo5_por_rotulos(
            self._caixas(), 900, 1600
        )
        self.assertIsNotNone(resultado)
        tabela, cabecalho = resultado
        self.assertGreaterEqual(tabela.y, 350)
        self.assertAlmostEqual(cabecalho.y2, tabela.y)
        self.assertGreaterEqual(tabela.x, 80)  # "RAS" da pagina ao lado fica fora

    def test_sem_rotulos_nao_deriva_tabela(self) -> None:
        self.assertIsNone(
            detector._tabela_modelo5_por_rotulos(
                self._caixas(com_rotulos=False), 900, 1600
            )
        )


class TestRotulosModelo5Tortos(unittest.TestCase):
    def test_aceita_leituras_tortas_comuns(self) -> None:
        for texto, esperado in (
            ("BAIDA", "saida"),
            ("SAIDA", "saida"),
            ("ARBINATURA", "assinatura"),
            ("ABBINATURA", "assinatura"),
            ("HORAB", "horas"),
            ("ENTRADA", "entrada"),
            ("E N T R A D A", "entrada"),
        ):
            self.assertEqual(detector.rotulo_modelo5(texto), esperado, texto)

    def test_nao_aceita_texto_qualquer(self) -> None:
        for texto in ("Rafael", "08:00", "BANDA", "HORARIO"):
            self.assertIsNone(detector.rotulo_modelo5(texto), texto)

    def test_pagina_vizinha_fica_fora_da_borda_direita(self) -> None:
        caixas = [
            Caixa(242, 312, 376, 44, texto="REGISTRO DE PONTO"),
            Caixa(285, 367, 111, 24, texto="ENTRADA"),
            Caixa(586, 359, 82, 34, texto="BAIDA"),
            Caixa(252, 392, 90, 27, texto="ARBINATURA"),
            Caixa(412, 392, 60, 25, texto="HORAB"),
            Caixa(546, 392, 93, 25, texto="ABBINATURA"),
            Caixa(705, 401, 52, 37, texto="HORAS"),
            Caixa(803, 442, 69, 29, texto="DIA MEB"),  # pagina vizinha
        ]
        for i in range(10):
            y = 420 + i * 25
            caixas += [
                Caixa(70, y, 80, 22, texto="2409"),
                Caixa(405, y, 75, 22, texto="08:12"),
                Caixa(735, y, 30, 22, texto="12"),
                Caixa(805, y, 90, 22, texto="011026"),  # pagina vizinha
            ]
        resultado = detector._tabela_modelo5_por_rotulos(tuple(caixas), 900, 1600)
        self.assertIsNotNone(resultado)
        tabela, _cab = resultado
        self.assertLess(tabela.x2, 800)
        self.assertLessEqual(tabela.x, 70)
