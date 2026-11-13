"""Testes do Modelo 5: registro com duas linhas por data (layout A)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from backend.modelo4.parser import CamposLinha, separar_linha  # noqa: E402
from backend.modelo5 import imagem, mapper, parser  # noqa: E402
from backend.tests.apoio import processar_lote_aprovado  # noqa: E402

REFERENCIA = "02/09/2026 a 17/09/2026"
DIAS_PT = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)

CABECALHO = [
    ["DIA", "MÊS", "Nº", "ASSINATURA", "HORAS", "ASSINATURA", "HORAS"],
]

LINHAS = [
    ["02", "09", "26", "Crusting", "08/06", "Crusting", "12/07"],
    ["02", "09", "26", "Crusting", "13/07", "Crusting", "17/05"],
    ["05", "09", "26", "Crusting", "08/00", "Crusting", "13/08"],
    ["07", "09", "26", "Fructo", "", "fructo", ""],
    ["07", "09", "26", "Crusting", "08/08", "Crusting", "12/06"],
    ["08", "09", "26", "Crusting", "13/05", "Crusting", "17/02"],
    ["16", "09", "26", "Feriddo", "", "feriddo", ""],
    ["16", "09", "26", "Crusting", "08/00", "Crusting", "12/05"],
    ["17", "09", "26", "Crusting", "13/05", "Crusting", "17/02"],
]


def _tabela(linhas: list[list[str]]) -> str:
    corpo = "".join(
        "<tr>" + "".join(f"<td>{celula}</td>" for celula in linha) + "</tr>"
        for linha in linhas
    )
    return f"<table>{corpo}</table>"


MARKDOWN = (
    "<p>REGISTRO DE PONTO</p>"
    "<p>Setembro 80</p>"
    "<p>Cristiano</p>"
    + _tabela(CABECALHO)
    + _tabela(LINHAS)
)

# Cartao quinzenal do Modelo 4: uma linha por dia, dia isolado na primeira
# coluna. Serve para conferir que o Modelo 5 recusa a imagem do outro modelo.
QUINZENAL_CABECALHO = [
    ["MÊS", "SE FENBRE", "ANO 2026"],
    ["H. Nor. DIAS", "MANHÃ", "TARDE", "EXTRA", "Horas"],
]
QUINZENAL_DIAS = [["1", "13:30", "18:00-18:15", "22:10", "", "", "", ""]]
QUINZENAL_MARKDOWN = _tabela(QUINZENAL_CABECALHO + QUINZENAL_DIAS)

# Cartao do Robert (OCR real): uma coluna extra antes de DIA e a coluna Nº
# trazendo a matricula (16, 10, 20), nao o ano.
DESLOCADO = [
    ["", "DIA", "MÊS", "Nº", "ENTRADA", "SAÍDA"],
    ["03", "01", "09", "16", "Cristiano", "08:00", "Cristiano", "12:00"],
    ["00", "01", "09", "10", "Cristiano", "13:00", "Cristiano", "17:00"],
    ["00", "02", "09", "20", "Cristiano", "08:05", "Cristiano", "12:01"],
    ["00", "02", "09", "20", "Cristiano", "13:02", "Cristiano", "17:03"],
]
DESLOCADO_MARKDOWN = (
    "<p>REGISTRO DE PONTO</p>"
    "<p>01/09/2026 a 30/09/2026</p>"
    "<p>Cristiano</p>"
    + _tabela(DESLOCADO)
)

# Batidas que o OCR devolveu como um numero unico de cinco digitos.
ILEGIVEL = [
    ["DIA", "MÊS", "Nº", "ASSINATURA", "HORAS", "ASSINATURA", "HORAS"],
    ["01", "09", "26", "Cristiano", "08900", "Cristiano", "11203"],
    ["01", "09", "26", "Cristiano", "32801", "Cristiano", "37805"],
]
ILEGIVEL_MARKDOWN = (
    "<p>REGISTRO DE PONTO</p>"
    "<p>01/09/2026 a 30/09/2026</p>"
    "<p>Cristiano</p>"
    + _tabela(ILEGIVEL)
)

SEM_DATA_MARKDOWN = _tabela(
    [["REGISTRO", "DE", "PONTO"], ["", "", ""]]
)


def _map(linha: list[str], indice: int = 0, referencia: str | None = REFERENCIA):
    return mapper.map_registro(linha, indice, referencia, "Cristiano")


class TestParserModelo5(unittest.TestCase):
    def test_seleciona_tabela_e_conta_linhas_com_data(self) -> None:
        grid = parser.selecionar_grid(MARKDOWN)
        self.assertIsNotNone(grid)
        assert grid is not None
        self.assertEqual(len(parser.linhas_dia(grid)), len(LINHAS))

    def test_identifica_nome_e_periodo(self) -> None:
        grid = parser.selecionar_grid(MARKDOWN)
        assert grid is not None
        self.assertEqual(parser.nome_colaborador(grid, MARKDOWN), "Cristiano")
        self.assertEqual(parser.referencia(grid, MARKDOWN), REFERENCIA)

    def test_linha_com_data_e_horarios_separados_por_barra(self) -> None:
        campos = separar_linha(LINHAS[0])
        assert campos is not None
        self.assertEqual((campos.dia, campos.mes, campos.ano), (2, 9, 2026))
        self.assertEqual(campos.horas, ("08:06", "12:07"))

    def test_feriado_e_reconhecido_sem_confundir_assinatura(self) -> None:
        feriado = separar_linha(["07", "09", "26", "Fructo", "", "fructo", ""])
        assert feriado is not None
        self.assertEqual(feriado.observacao, "Feriado")
        self.assertEqual(feriado.horas, ())

        normal = separar_linha(
            ["02", "09", "26", "Estrutivo", "08/06", "Estrutivo", "12/07"]
        )
        assert normal is not None
        self.assertEqual(normal.observacao, "")
        self.assertEqual(normal.horas, ("08:06", "12:07"))

    def test_corrige_data_repetida_apos_feriado(self) -> None:
        feriado = CamposLinha(7, 9, 2026, (), observacao="Feriado")
        trabalho = CamposLinha(7, 9, 2026, ("08:08", "12:06"))
        corrigidos = parser.corrigir_datas_apos_feriado([feriado, trabalho])
        self.assertEqual(corrigidos[1].dia, 8)
        self.assertTrue(corrigidos[1].forcar_revisao)
        self.assertIn("dia seguinte ao feriado", corrigidos[1].ajuste)

    def test_herda_data_de_linha_de_continuacao(self) -> None:
        primeira = CamposLinha(2, 9, 2026, ("08:06", "12:07"))
        segunda = CamposLinha(None, None, None, ("13:07", "17:05"))
        preenchidas = parser.preencher_datas([primeira, segunda])
        self.assertEqual((preenchidas[1].dia, preenchidas[1].mes), (2, 9))
        self.assertTrue(preenchidas[1].forcar_revisao)

    def test_layout_do_proprio_modelo_e_reconhecido(self) -> None:
        grid = parser.selecionar_grid(MARKDOWN)
        assert grid is not None
        self.assertEqual(parser.layout_evidente(grid, MARKDOWN), parser.LAYOUT)

    def test_layout_quinzenal_e_apontado_como_do_modelo_4(self) -> None:
        grid = parser.selecionar_grid(QUINZENAL_MARKDOWN)
        assert grid is not None
        self.assertEqual(
            parser.layout_evidente(grid, QUINZENAL_MARKDOWN), "linha-unica"
        )

    def test_validar_layout_recusa_cartao_quinzenal(self) -> None:
        grid = parser.selecionar_grid(QUINZENAL_MARKDOWN)
        assert grid is not None
        motivo = parser.validar_layout(grid, QUINZENAL_MARKDOWN)
        assert motivo is not None
        self.assertIn("Modelo 4", motivo)

    def test_validar_layout_aceita_cartao_do_modelo_5(self) -> None:
        grid = parser.selecionar_grid(MARKDOWN)
        assert grid is not None
        self.assertIsNone(parser.validar_layout(grid, MARKDOWN))

    def test_validar_layout_nao_recusa_cabecalho_ambiguo(self) -> None:
        ambiguo = _tabela(
            [
                ["REGISTRO", "DE", "PONTO"],
                ["02", "09", "26", "Crusting", "08:06", "Crusting", "12:07"],
            ]
        )
        grid = parser.selecionar_grid(ambiguo)
        assert grid is not None
        self.assertIsNone(parser.layout_evidente(grid, ambiguo))
        self.assertIsNone(parser.validar_layout(grid, ambiguo))

    def test_coluna_extra_a_esquerda_do_dia_e_removida(self) -> None:
        # Sem a normalizacao o parser leria dia=3, mes=1, ano=9 (invalido) e
        # descartaria o cartao inteiro, como aconteceu no lote real.
        original = parser.selecionar_grid(DESLOCADO_MARKDOWN)
        assert original is not None
        self.assertEqual(len(parser.linhas_dia(original)), len(DESLOCADO) - 1)
        primeiro = parser.separar_linha(parser.linhas_dia(original)[0])
        assert primeiro is not None
        self.assertEqual(
            (primeiro.dia, primeiro.mes, primeiro.ano), (1, 9, 2026)
        )

    def test_numero_do_colaborador_nao_vira_ano(self) -> None:
        grid = parser.selecionar_grid(DESLOCADO_MARKDOWN)
        assert grid is not None
        numeros = {linha[2] for linha in grid[1:] if len(linha) > 2}
        self.assertEqual(numeros, {"26"})

    def test_grid_ja_alinhado_nao_e_alterado(self) -> None:
        grid = parser.selecionar_grid(MARKDOWN)
        assert grid is not None
        self.assertEqual(parser.normalizar_grid(grid, MARKDOWN), grid)

    def test_grid_sem_data_nao_e_inventado(self) -> None:
        self.assertIsNone(parser.selecionar_grid(SEM_DATA_MARKDOWN))

    def test_matricula_inconsistente_sem_ano_e_neutralizada(self) -> None:
        # Sem periodo legivel, uma coluna Nº que muda a cada linha e matricula,
        # nao ano; o dia e o mes continuam validos.
        grid = parser.selecionar_grid(_tabela(DESLOCADO))
        assert grid is not None
        campos = parser.separar_linha(parser.linhas_dia(grid)[0])
        assert campos is not None
        self.assertEqual((campos.dia, campos.mes, campos.ano), (1, 9, None))

    def test_batida_compacta_preserva_o_dia_para_revisao(self) -> None:
        campos = parser.separar_linha(ILEGIVEL[1])
        assert campos is not None
        self.assertEqual((campos.dia, campos.mes, campos.ano), (1, 9, 2026))
        self.assertEqual(campos.horas, ())
        self.assertTrue(campos.forcar_revisao)
        self.assertIn("ilegível", campos.observacao)


class TestMapperModelo5(unittest.TestCase):
    def _mapear(self):
        grid = parser.selecionar_grid(MARKDOWN)
        assert grid is not None
        linhas = parser.linhas_dia(grid)
        return mapper.mapear_folha(linhas, REFERENCIA, "Cristiano")

    def test_agrupa_duas_linhas_por_data_e_usa_somente_o_dia(self) -> None:
        registros, _ = self._mapear()
        self.assertEqual(
            [registro.data for registro in registros],
            ["2", "5", "7", "8", "16", "17"],
        )
        self.assertEqual(registros[0].dia_semana, DIAS_PT[2])
        self.assertEqual(
            (
                registros[0].entrada,
                registros[0].saida_almoco,
                registros[0].volta_almoco,
                registros[0].saida,
            ),
            ("08:06", "12:07", "13:07", "17:05"),
        )
        self.assertEqual(registros[0].confianca, 1.0)
        self.assertFalse(registros[0].revisar)

    def test_dia_curto_tem_duas_batidas_e_revisao(self) -> None:
        registros, linhas_excel = self._mapear()
        dia_5 = registros[1]
        self.assertEqual(
            (dia_5.entrada, dia_5.saida_almoco, dia_5.volta_almoco, dia_5.saida),
            ("08:00", "13:08", None, None),
        )
        self.assertEqual(dia_5.confianca, 0.5)
        self.assertTrue(dia_5.revisar)
        self.assertEqual(linhas_excel[1][5], "05:08")

    def test_feriado_aparece_no_excel_sem_horarios(self) -> None:
        registros, linhas_excel = self._mapear()
        feriado = registros[2]
        self.assertEqual(feriado.observacao, "Feriado")
        self.assertTrue(feriado.revisar)
        self.assertEqual(feriado.entrada, None)
        self.assertEqual(
            linhas_excel[2],
            ["07 - segunda-feira (Feriado)", "", "", "", "", ""],
        )

    def test_data_do_dia_seguinte_ao_feriado_e_corrigida(self) -> None:
        registros, _ = self._mapear()
        dia_8 = registros[3]
        self.assertEqual(dia_8.data, "8")
        self.assertEqual(dia_8.dia_semana, DIAS_PT[1])
        self.assertTrue(dia_8.revisar)
        self.assertIn("Data ajustada", dia_8.observacao or "")
        self.assertEqual(
            (dia_8.entrada, dia_8.saida_almoco, dia_8.volta_almoco, dia_8.saida),
            ("08:08", "12:06", "13:05", "17:02"),
        )

    def test_excel_tem_seis_colunas_e_hora_total(self) -> None:
        _, linhas_excel = self._mapear()
        self.assertEqual(len(linhas_excel), 6)
        self.assertTrue(all(len(linha) == 6 for linha in linhas_excel))
        self.assertEqual(linhas_excel[0][0], "02 - quarta-feira")
        self.assertEqual(linhas_excel[0][5], "07:59")

    def test_lista_somente_os_dias_preenchidos(self) -> None:
        grid = parser.selecionar_grid(MARKDOWN)
        assert grid is not None
        _, linhas = mapper.mapear_folha(
            parser.linhas_dia(grid), REFERENCIA, "Cristiano"
        )
        self.assertEqual(len(linhas), 6)
        self.assertEqual(linhas[0][0], "02 - quarta-feira")

    def test_mapear_folha_forca_o_layout_de_duas_linhas(self) -> None:
        # Mesmo que uma linha isolada apareca, o Modelo 5 nao lista a quinzena
        # inteira: o layout e forçado no mapper.
        _, linhas = mapper.mapear_folha(
            [["05", "09", "26", "Crusting", "08:00", "Crusting", "13:08"]],
            REFERENCIA,
            "Cristiano",
        )
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0][0], "05 - sábado")

    def test_map_registro_de_uma_linha_nao_quebra(self) -> None:
        registro, linha_excel = _map(LINHAS[0])
        self.assertEqual(registro.data, "2")
        self.assertEqual(registro.entrada, "08:06")
        self.assertEqual(len(linha_excel), 6)

    def test_grid_deslocado_usa_o_ano_do_periodo(self) -> None:
        grid = parser.selecionar_grid(DESLOCADO_MARKDOWN)
        assert grid is not None
        registros, _ = mapper.mapear_folha(
            parser.linhas_dia(grid), "01/09/2026 a 30/09/2026", "Cristiano"
        )
        self.assertEqual([registro.data for registro in registros], ["1", "2"])
        self.assertEqual(registros[0].dia_semana, DIAS_PT[1])
        self.assertFalse(registros[0].revisar)
        self.assertEqual(
            (
                registros[0].entrada,
                registros[0].saida_almoco,
                registros[0].volta_almoco,
                registros[0].saida,
            ),
            ("08:00", "12:00", "13:00", "17:00"),
        )

    def test_batida_ilegivel_aparece_para_digitacao(self) -> None:
        grid = parser.selecionar_grid(ILEGIVEL_MARKDOWN)
        assert grid is not None
        registros, linhas = mapper.mapear_folha(
            parser.linhas_dia(grid), "01/09/2026 a 30/09/2026", "Cristiano"
        )
        self.assertEqual(len(registros), 1)
        self.assertIsNone(registros[0].entrada)
        self.assertTrue(registros[0].revisar)
        self.assertIn("ilegível", registros[0].observacao or "")
        self.assertTrue(linhas[0][0].startswith("01 - "))


class TestImagemModelo5(unittest.TestCase):
    def test_mantem_cor_e_amplia_a_foto(self) -> None:
        buffer = BytesIO()
        foto = Image.new("RGB", (400, 600), (250, 250, 250))
        foto.save(buffer, format="JPEG")

        preparada = imagem.preparar_imagem(buffer.getvalue())

        with Image.open(BytesIO(preparada)) as saida:
            self.assertIn(saida.mode, ("RGB", "L"))
            self.assertGreaterEqual(saida.width, 1200)

    def test_pdf_permanece_intacto(self) -> None:
        pdf = b"%PDF-1.7\nconteudo"
        self.assertEqual(imagem.preparar_imagem(pdf), pdf)


class TestPipelineModelo5(unittest.IsolatedAsyncioTestCase):
    async def test_processa_cartao_de_duas_linhas_com_ocr_simulado(self) -> None:
        from backend import config, servico  # noqa: PLC0415

        @asynccontextmanager
        async def abrir_falso():
            yield object()

        preparadores: list[object] = []

        async def ocr_falso(_sessao, _dados, preparar=None):
            preparadores.append(preparar)
            return MARKDOWN

        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            with (
                patch.object(config, "DIR_LOGS", raiz / "logs"),
                patch.object(servico.mcp_client, "abrir_mcp", abrir_falso),
                patch.object(servico.mcp_client, "ocr_imagem", ocr_falso),
                patch.object(
                    servico.anonimizacao_service,
                    "processar_lote",
                    processar_lote_aprovado,
                ),
            ):
                folhas, avisos = await servico.processar_folhas_modelo5(
                    [("cristiano.jpg", b"imagem")]
                )

        self.assertEqual(avisos, [])
        self.assertEqual(len(folhas), 1)
        folha = folhas[0]
        self.assertEqual(folha.nome, "Cristiano")
        self.assertEqual(folha.referencia, REFERENCIA)
        self.assertEqual(len(folha.registros), 6)
        self.assertEqual(len(folha.linhas_excel), 6)
        self.assertEqual(len(folha.origem_hash or ""), 64)
        self.assertEqual(preparadores, [imagem.preparar_imagem])

    async def test_pipeline_processa_cartao_com_coluna_extra(self) -> None:
        from backend import config, servico  # noqa: PLC0415

        @asynccontextmanager
        async def abrir_falso():
            yield object()

        async def ocr_falso(_sessao, _dados, preparar=None):
            return DESLOCADO_MARKDOWN

        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            with (
                patch.object(config, "DIR_LOGS", raiz / "logs"),
                patch.object(servico.mcp_client, "abrir_mcp", abrir_falso),
                patch.object(servico.mcp_client, "ocr_imagem", ocr_falso),
                patch.object(
                    servico.anonimizacao_service,
                    "processar_lote",
                    processar_lote_aprovado,
                ),
            ):
                folhas, avisos = await servico.processar_folhas_modelo5(
                    [("robert.jpg", b"imagem")]
                )

        self.assertEqual(avisos, [])
        self.assertEqual(len(folhas), 1)
        self.assertEqual(
            [registro.data for registro in folhas[0].registros], ["1", "2"]
        )

    async def test_pipeline_recusa_cartao_do_modelo_4(self) -> None:
        from backend import config, servico  # noqa: PLC0415

        @asynccontextmanager
        async def abrir_falso():
            yield object()

        async def ocr_falso(_sessao, _dados, preparar=None):
            return QUINZENAL_MARKDOWN

        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            with (
                patch.object(config, "DIR_LOGS", raiz / "logs"),
                patch.object(servico.mcp_client, "abrir_mcp", abrir_falso),
                patch.object(servico.mcp_client, "ocr_imagem", ocr_falso),
                patch.object(
                    servico.anonimizacao_service,
                    "processar_lote",
                    processar_lote_aprovado,
                ),
            ):
                folhas, avisos = await servico.processar_folhas_modelo5(
                    [("alexandre.jpg", b"imagem")]
                )

        self.assertEqual(folhas, [])
        self.assertEqual(len(avisos), 1)
        self.assertIn("Modelo 4", avisos[0])


class TestEndpointModelo5(unittest.TestCase):
    def test_endpoint_despacha_modelo_5_e_persiste_com_o_modelo_correto(
        self,
    ) -> None:
        import sqlite3

        try:
            from fastapi.testclient import TestClient

            from backend import config, servico
            from backend.main import app
            from backend.modelos import FolhaProcessada, Registro
            from backend.tests.apoio import autenticar
        except ModuleNotFoundError as exc:
            if exc.name != "mcp":
                self.skipTest("dependencia 'mcp' nao instalada neste interpretador")

        registro = Registro(
            id="cristiano-20260902-0",
            funcionario="Cristiano",
            data="2",
            dia_semana="quarta-feira",
            entrada="08:06",
            saida_almoco="12:07",
            volta_almoco="13:07",
            saida="17:05",
            confianca=1.0,
            revisar=False,
        )
        folha = FolhaProcessada(
            nome="Cristiano",
            funcionario="Cristiano",
            referencia=REFERENCIA,
            arquivo_origem="cristiano.jpg",
            registros=[registro],
            linhas_excel=[
                ["02 - quarta-feira", "08:06", "12:07", "13:07", "17:05", "07:59"]
            ],
        )
        chamadas: list[str] = []

        async def processar_falso(imagens, lote=None, estatisticas=None):
            chamadas.append("modelo-5")
            return [folha], []

        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            banco = raiz / "data" / "teste.db"
            with (
                patch.object(servico, "processar_folhas_modelo5", processar_falso),
                patch.object(config, "DIR_SAIDA", raiz),
                patch.object(config, "DIR_DADOS", raiz / "data"),
                patch.object(config, "ARQUIVO_BANCO", banco),
                patch.object(config, "DIR_LOGS", raiz / "logs"),
                patch.object(config, "SYNC_EXCEL_ATIVO", False),
            ):
                with TestClient(app) as cliente:
                    autenticar(cliente)
                    resposta = cliente.post(
                        "/processar",
                        data={"modelo": "modelo-5", "aceite_ocr": "true"},
                        headers={"Idempotency-Key": "teste-modelo-5"},
                        files={"arquivos": ("cristiano.jpg", b"imagem", "image/jpeg")},
                    )

            self.assertEqual(resposta.status_code, 200)
            self.assertEqual(chamadas, ["modelo-5"])
            conexao = sqlite3.connect(banco)
            try:
                modelos = conexao.execute(
                    "SELECT DISTINCT modelo FROM registros_ponto"
                ).fetchall()
            finally:
                conexao.close()
            self.assertEqual(modelos, [("modelo-5",)])


class TestLeiturasTortasCaderno(unittest.TestCase):
    """Correcoes do Modelo 5 para leituras tortas do OCR no caderno."""

    def test_letra_no_lugar_do_zero_na_data(self) -> None:
        from backend.modelo5 import parser as p5

        campos = p5.separar_linha(["D2", "09", "26", "", "13:07", "", "17:05"])
        self.assertEqual(campos.dia, 2)
        self.assertEqual(campos.horas, ("13:07", "17:05"))

    def test_horario_partido_pelo_traco_da_coluna(self) -> None:
        from backend.modelo5 import parser as p5

        linha, suspeita = p5.corrigir_linha(["01", "09", "26", "08%", "00", "11%", "03"])
        self.assertEqual(linha[3], "08:00")
        self.assertEqual(linha[5], "11:03")
        self.assertEqual(len(linha), 7)
        self.assertFalse(suspeita)

    def test_dois_pontos_lido_como_oito_marca_revisao(self) -> None:
        from backend.modelo5 import parser as p5

        campos = p5.separar_linha(["04", "09", "26", "", "08800", "", "12802"])
        self.assertEqual(campos.horas, ("08:00", "12:02"))
        self.assertTrue(campos.forcar_revisao)

    def test_nao_inventa_horario_invalido_nem_junta_datas(self) -> None:
        from backend.modelo5 import parser as p5

        linha, suspeita = p5.corrigir_linha(["01", "09", "26", "", "38899", "", ""])
        self.assertEqual(linha, ["01", "09", "26", "", "38899", "", ""])
        self.assertFalse(suspeita)


class TestPareamentoTurnosCaderno(unittest.TestCase):
    """Modelo 5: 1a linha = manha, 2a = tarde, mesmo com a data deslocada."""

    def _mapear(self, linhas):
        registros, _excel = mapper.mapear_folha(linhas, "09/2026", "teste")
        return {
            r.data: (r.entrada, r.saida_almoco, r.volta_almoco, r.saida)
            for r in registros
        }

    def test_coluna_de_datas_deslocada_pelo_ocr(self) -> None:
        # O OCR fundiu o cabecalho com a 1a linha e deslocou as datas:
        # DIA, D2, D2, D3, D3, D4 em vez de 02, 02, 03, 03, 04, 04.
        linhas = [
            ["DIA", "MÉS", "", "08:06", "", "12:07"],
            ["D2", "09", "26", "13:07", "", "17:05"],
            ["D2", "09", "26", "08:08", "", "12:00"],
            ["D3", "09", "26", "13:03", "", "17:03"],
            ["D3", "09", "26", "08:07", "", "12:06"],
            ["D4", "09", "26", "13:07", "", "17:00"],
            ["D5", "09", "26", "08:00", "", "13:08"],
            ["D7", "09", "26", "", "", ""],
            ["D7", "09", "26", "", "", ""],
            ["D8", "09", "26", "08:08", "", "12:06"],
            ["D8", "09", "26", "13:05", "", "17:02"],
            ["D10", "09", "26", "08:03", "", "12:05"],
            ["D10", "09", "26", "13:02", "", "17:03"],
        ]
        dias = self._mapear(linhas)
        self.assertEqual(dias["2"], ("08:06", "12:07", "13:07", "17:05"))
        self.assertEqual(dias["3"], ("08:08", "12:00", "13:03", "17:03"))
        self.assertEqual(dias["4"], ("08:07", "12:06", "13:07", "17:00"))
        self.assertEqual(dias["5"][:2], ("08:00", "13:08"))
        self.assertEqual(dias["8"], ("08:08", "12:06", "13:05", "17:02"))
        self.assertEqual(dias["10"], ("08:03", "12:05", "13:02", "17:03"))

    def test_ano_torto_nao_separa_a_mesma_data(self) -> None:
        linhas = [
            ["01", "09", "26", "08%", "00", "11%", "03"],
            ["01", "09", "10", "12%", "04", "17%", "00"],
        ]
        dias = self._mapear(linhas)
        self.assertEqual(list(dias), ["1"])
        self.assertEqual(dias["1"], ("08:00", "11:03", "12:04", "17:00"))

    def test_data_ajustada_fica_para_revisao(self) -> None:
        registros, _ = mapper.mapear_folha(
            [
                ["DIA", "MÉS", "", "08:06", "", "12:07"],
                ["D2", "09", "26", "13:07", "", "17:05"],
                ["D2", "09", "26", "08:08", "", "12:00"],
                ["D3", "09", "26", "13:03", "", "17:03"],
            ],
            "09/2026",
            "teste",
        )
        terceiro = next(r for r in registros if r.data == "3")
        self.assertTrue(terceiro.revisar)
