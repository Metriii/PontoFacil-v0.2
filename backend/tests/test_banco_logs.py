"""Testes do banco SQLite, logs diarios e endpoints de historico."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from backend import banco, config, logs, servico  # noqa: E402
from backend.modelos import FolhaProcessada, Registro  # noqa: E402
from backend.tests.apoio import autenticar  # noqa: E402


def _registro(
    funcionario: str = "ANA",
    data: str = "01/10/2026",
    entrada: str | None = "08:00",
    saida_almoco: str | None = "12:00",
    volta_almoco: str | None = "13:00",
    saida: str | None = "17:00",
) -> Registro:
    return Registro(
        id="ana-01-0",
        funcionario=funcionario,
        data=data,
        entrada=entrada,
        saida_almoco=saida_almoco,
        volta_almoco=volta_almoco,
        saida=saida,
    )


def _folha(
    registros: list[Registro] | None = None,
    funcionario: str = "ANA",
    origem_hash: str = "hash-origem",
) -> FolhaProcessada:
    registros = registros or [_registro(funcionario=funcionario)]
    return FolhaProcessada(
        nome=funcionario,
        funcionario=funcionario,
        referencia="01/10/2026 a 31/10/2026",
        arquivo_origem=f"{funcionario.lower()}.jpg",
        registros=registros,
        linhas_excel=[["01 - quinta-feira", "08:00", "12:00", "13:00", "17:00", "08:00"]],
        origem_hash=origem_hash,
    )


class _ConexaoComFalha:
    def __init__(self, real: sqlite3.Connection) -> None:
        self.real = real

    def __enter__(self):
        self.real.__enter__()
        return self

    def __exit__(self, *args):
        return self.real.__exit__(*args)

    def close(self) -> None:
        self.real.close()

    def execute(self, sql: str, parametros=()):
        if "INSERT" in sql.upper():
            raise sqlite3.OperationalError("falha simulada")
        return self.real.execute(sql, parametros)


class BaseTemporaria(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        raiz = Path(self._tmp.name)
        self.patches = [
            patch.object(config, "DIR_DADOS", raiz / "data"),
            patch.object(config, "ARQUIVO_BANCO", raiz / "data" / "teste.db"),
            patch.object(config, "DIR_LOGS", raiz / "logs"),
            patch.object(config, "DIR_SAIDA", raiz / "out"),
            patch.object(config, "SYNC_EXCEL_ATIVO", False),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        config.garantir_diretorios()
        banco.inicializar()


class TestBancoSQLite(BaseTemporaria):
    def test_cria_banco_tabela_e_persiste_campos(self) -> None:
        resultado = banco.inserir_folhas([_folha()], "modelo-1")

        self.assertEqual(
            resultado,
            {"inseridos": 1, "duplicados": 0, "ignorados": 0, "vazios": 0},
        )
        self.assertTrue(config.ARQUIVO_BANCO.exists())
        registros = banco.listar_registros()
        self.assertEqual(len(registros), 1)
        registro = registros[0]
        self.assertEqual(registro["nome_funcionario"], "ANA")
        self.assertEqual(registro["data_pontos"], "2026-10-01")
        self.assertEqual(registro["entrada"], "08:00")
        self.assertEqual(registro["saida_intervalo"], "12:00")
        self.assertEqual(registro["volta_intervalo"], "13:00")
        self.assertEqual(registro["saida_final"], "17:00")
        self.assertEqual(registro["modelo"], "modelo-1")
        self.assertRegex(registro["data_processado"], r"^\d{4}-\d{2}-\d{2}$")

    def test_dia_sem_horarios_nao_e_persistido(self) -> None:
        folha = _folha(
            [
                _registro(
                    data="06/10/2026",
                    entrada=None,
                    saida_almoco=None,
                    volta_almoco=None,
                    saida=None,
                )
            ]
        )

        resultado = banco.inserir_folhas([folha], "modelo-1")

        self.assertEqual(
            resultado,
            {"inseridos": 0, "duplicados": 0, "ignorados": 0, "vazios": 1},
        )
        self.assertEqual(banco.listar_registros(), [])

    def test_dia_com_apenas_um_horario_e_persistido(self) -> None:
        folha = _folha(
            [
                _registro(
                    data="06/10/2026",
                    entrada="08:00",
                    saida_almoco=None,
                    volta_almoco=None,
                    saida=None,
                )
            ]
        )

        resultado = banco.inserir_folhas([folha], "modelo-1")

        self.assertEqual(resultado["inseridos"], 1)
        self.assertEqual(resultado["vazios"], 0)
        registro = banco.listar_registros()[0]
        self.assertEqual(registro["entrada"], "08:00")
        self.assertIsNone(registro["saida_intervalo"])
        self.assertIsNone(registro["volta_intervalo"])
        self.assertIsNone(registro["saida_final"])

    def test_trigger_ignora_insert_direto_sem_horarios(self) -> None:
        conexao = sqlite3.connect(config.ARQUIVO_BANCO)
        try:
            vazio = conexao.execute(
                """
                INSERT INTO registros_ponto (
                    nome_funcionario, data_pontos, entrada, saida_intervalo,
                    volta_intervalo, saida_final, data_processado, modelo,
                    chave_unica
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "TRIGGER",
                    "06",
                    "",
                    None,
                    "   ",
                    None,
                    "2026-10-22",
                    "modelo-1",
                    "trigger-vazio",
                ),
            )
            com_um_horario = conexao.execute(
                """
                INSERT INTO registros_ponto (
                    nome_funcionario, data_pontos, entrada, saida_intervalo,
                    volta_intervalo, saida_final, data_processado, modelo,
                    chave_unica
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "TRIGGER",
                    "07",
                    "08:00",
                    None,
                    None,
                    None,
                    "2026-10-22",
                    "modelo-1",
                    "trigger-com-horario",
                ),
            )
            conexao.commit()
        finally:
            conexao.close()

        self.assertEqual(vazio.rowcount, 0)
        self.assertEqual(com_um_horario.rowcount, 1)
        self.assertEqual(len(banco.listar_registros(funcionario="TRIGGER")), 1)

    def test_deduplica_e_preserva_registros_anteriores(self) -> None:
        banco.inserir_folhas([_folha()], "modelo-1")
        repetido = banco.inserir_folhas([_folha()], "modelo-1")
        self.assertEqual(
            repetido,
            {"inseridos": 0, "duplicados": 1, "ignorados": 0, "vazios": 0},
        )

        nova_data = banco.inserir_folhas(
            [_folha([_registro(data="02/10/2026")])], "modelo-1"
        )
        self.assertEqual(nova_data["inseridos"], 1)
        self.assertEqual(len(banco.listar_registros()), 2)

    def test_registro_sem_data_valida_e_ignorado(self) -> None:
        resultado = banco.inserir_folhas(
            [_folha([_registro(data="sem data")])], "modelo-1"
        )
        self.assertEqual(
            resultado,
            {"inseridos": 0, "duplicados": 0, "ignorados": 1, "vazios": 0},
        )
        self.assertEqual(banco.listar_registros(), [])

    def test_falha_na_transacao_faz_rollback(self) -> None:
        real = banco._conectar()
        try:
            with patch.object(banco, "_conectar", lambda: _ConexaoComFalha(real)):
                with self.assertRaises(banco.ErroBanco):
                    banco.inserir_folhas([_folha()], "modelo-1")
        finally:
            real.close()
        self.assertEqual(banco.listar_registros(), [])

    def test_dados_continuam_apos_reabrir_conexao(self) -> None:
        banco.inserir_folhas([_folha()], "modelo-1")
        self.assertEqual(len(banco.listar_registros()), 1)
        self.assertEqual(len(banco.listar_registros(funcionario="ANA")), 1)
        self.assertEqual(len(banco.listar_registros(data="2026-10-01")), 1)
        self.assertEqual(len(banco.listar_registros(modelo="modelo-2")), 0)

    def test_modelo_invalido_e_rejeitado(self) -> None:
        with self.assertRaises(ValueError):
            banco.inserir_folhas([_folha()], "modelo-9")
        with self.assertRaises(ValueError):
            banco.listar_registros(modelo="modelo-9")


class TestResumoRegistros(BaseTemporaria):
    def _inserir(
        self,
        funcionario: str,
        data_processado: str,
        registros: list[Registro],
        modelo: str = "modelo-1",
        origem_hash: str | None = None,
    ):
        with patch.object(
            banco.logs,
            "agora",
            return_value=datetime.fromisoformat(f"{data_processado}T12:00:00"),
        ):
            return banco.inserir_folhas(
                [
                    _folha(
                        registros,
                        funcionario=funcionario,
                        origem_hash=(
                            origem_hash
                            or f"hash-{funcionario}-{data_processado}"
                        ),
                    )
                ],
                modelo,
            )

    def test_uma_linha_por_funcionario_com_ultimo_processamento(self) -> None:
        self._inserir(
            "ANA",
            "2026-10-20",
            [
                _registro(data="01/10/2026", funcionario="ANA"),
                _registro(data="02/10/2026", funcionario="ANA"),
            ],
        )
        self._inserir(
            "ANA",
            "2026-10-21",
            [
                _registro(data="01/11/2026", funcionario="ANA"),
                _registro(data="02/11/2026", funcionario="ANA"),
            ],
            modelo="modelo-2",
        )

        resumo = banco.listar_resumo_registros()

        self.assertEqual(len(resumo), 1)
        self.assertEqual(resumo[0]["nome_funcionario"], "ANA")
        self.assertEqual(resumo[0]["data_inicio"], "2026-11-01")
        self.assertEqual(resumo[0]["data_fim"], "2026-11-02")
        self.assertEqual(resumo[0]["data_processado"], "2026-10-21")
        self.assertEqual(resumo[0]["modelo"], "modelo-2")

    def test_limita_a_quatro_funcionarios_mais_recentes(self) -> None:
        for indice, nome in enumerate(
            ["ANA", "BRUNO", "CARLA", "DANIEL", "ELISA"]
        ):
            self._inserir(
                nome,
                f"2026-10-{20 + indice:02d}",
                [_registro(data="01/10/2026", funcionario=nome)],
            )

        resumo = banco.listar_resumo_registros(limite=10)

        self.assertEqual(
            [item["nome_funcionario"] for item in resumo],
            ["ELISA", "DANIEL", "CARLA", "BRUNO"],
        )

    def test_faixa_usa_primeiro_e_ultimo_id_do_lote(self) -> None:
        self._inserir(
            "ANA",
            "2026-10-21",
            [
                _registro(data="02/10/2026", funcionario="ANA"),
                _registro(data="01/10/2026", funcionario="ANA"),
            ],
        )

        resumo = banco.listar_resumo_registros()

        self.assertEqual(resumo[0]["data_inicio"], "2026-10-02")
        self.assertEqual(resumo[0]["data_fim"], "2026-10-01")


class TestLogsDiarios(BaseTemporaria):
    def setUp(self) -> None:
        super().setUp()
        for arquivo in config.DIR_LOGS.glob("*.txt"):
            arquivo.unlink()

    def _arquivo_log(self) -> Path:
        return next(config.DIR_LOGS.glob("*.txt"))

    def test_cria_pasta_arquivo_e_acrescenta_entradas(self) -> None:
        self.assertTrue(logs.info("Teste", "primeira entrada", "lote-1"))
        self.assertTrue(logs.warning("Teste", "segunda entrada"))
        linhas = self._arquivo_log().read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(linhas), 2)
        self.assertIn(
            " | Sistema | INFO | Teste | primeira entrada | lote=lote-1", linhas[0]
        )
        self.assertIn(" | Sistema | WARNING | Teste | segunda entrada", linhas[1])
        self.assertFalse(linhas[1].endswith("|"))

    def test_sanitiza_quebras_e_separadores(self) -> None:
        logs.error("Componente|Falso", "linha1\nlinha2 | INFO | injetado")
        linha = self._arquivo_log().read_text(encoding="utf-8")
        self.assertNotIn("\n", linha.strip())
        self.assertEqual(linha.count(" | "), 4)
        self.assertIn("linha1 linha2 / INFO / injetado", linha)

    def test_usuario_vem_logo_depois_do_horario(self) -> None:
        token = logs.iniciar_contexto()
        try:
            logs.definir_usuario("Vinicius")
            logs.info("OCR", "com usuario", modelo="modelo-1")
        finally:
            logs.encerrar_contexto(token)
        logs.info("Startup", "sem usuario")
        linhas = self._arquivo_log().read_text(encoding="utf-8").splitlines()

        self.assertRegex(
            linhas[0],
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| Vinicius \| INFO \| OCR "
            r"\| Modelo: modelo-1 \| com usuario$",
        )
        self.assertIn(" | Sistema | INFO | Startup | sem usuario", linhas[1])

    def test_usuario_do_contexto_chega_em_threads_e_tarefas(self) -> None:
        import asyncio

        async def fluxo() -> None:
            token = logs.iniciar_contexto()
            try:
                # Como na dependencia do FastAPI: definido dentro de uma thread.
                await asyncio.to_thread(logs.definir_usuario, "Ana")
                await asyncio.to_thread(logs.info, "Anon", "na thread")
                await asyncio.create_task(asyncio.sleep(0))
                logs.info("Anon", "no loop")
            finally:
                logs.encerrar_contexto(token)

        asyncio.run(fluxo())
        texto = self._arquivo_log().read_text(encoding="utf-8")
        self.assertIn(" | Ana | INFO | Anon | na thread", texto)
        self.assertIn(" | Ana | INFO | Anon | no loop", texto)

    def test_falha_de_escrita_nao_propaga(self) -> None:
        arquivo = Path(self._tmp.name) / "logs-como-arquivo"
        arquivo.write_text("x", encoding="utf-8")
        with patch.object(config, "DIR_LOGS", arquivo):
            self.assertFalse(logs.info("Teste", "nao deve quebrar"))


class TestEndpointsSQLite(BaseTemporaria):
    @contextmanager
    def _cliente(self):
        from fastapi.testclient import TestClient

        from backend.main import app

        with TestClient(app) as cliente:
            autenticar(cliente)
            yield cliente

    def test_processar_persiste_e_registros_consulta(self) -> None:
        folha = _folha()

        async def processar_falso(imagens, lote=None, estatisticas=None):
            return [folha], []

        with (
            patch.object(servico, "processar_folhas", processar_falso),
            self._cliente() as cliente,
        ):
            resposta = cliente.post(
                "/processar",
                files={"arquivos": ("ANA.jpg", b"imagem", "image/jpeg")},
                data={"modelo": "modelo-1", "aceite_ocr": "true"},
                headers={"Idempotency-Key": "teste-banco-modelo-1"},
            )
            self.assertEqual(resposta.status_code, 200, resposta.text)

            historico = cliente.get("/registros")
            self.assertEqual(historico.status_code, 200)
            dados = historico.json()
            self.assertEqual(len(dados), 1)
            self.assertEqual(dados[0]["nome_funcionario"], "ANA")
            self.assertEqual(dados[0]["data_pontos"], "2026-10-01")
            self.assertEqual(
                list(dados[0].keys()),
                [
                    "id",
                    "nome_funcionario",
                    "data_pontos",
                    "entrada",
                    "saida_intervalo",
                    "volta_intervalo",
                    "saida_final",
                    "data_processado",
                    "modelo",
                ],
            )

    def test_processar_modelo_2_despacha_para_folha_diaria(self) -> None:
        async def processar_modelo2_falso(imagens, lote=None, estatisticas=None):
            return [_folha()], []

        async def processar_modelo1_nao_deveria(imagens, lote=None, estatisticas=None):
            raise AssertionError("processar_folhas do Modelo 1 nao deveria ser chamado")

        with (
            patch.object(servico, "processar_folhas", processar_modelo1_nao_deveria),
            patch.object(servico, "processar_folhas_modelo2", processar_modelo2_falso),
            self._cliente() as cliente,
        ):
            resposta = cliente.post(
                "/processar",
                files={"arquivos": ("amanda.jpg", b"imagem", "image/jpeg")},
                data={"modelo": "modelo-2", "aceite_ocr": "true"},
                headers={"Idempotency-Key": "teste-banco-modelo-2"},
            )
            self.assertEqual(resposta.status_code, 200, resposta.text)

            historico = cliente.get("/registros", params={"modelo": "modelo-2"})
            self.assertEqual(historico.status_code, 200)
            dados = historico.json()
            self.assertEqual(len(dados), 1)
            self.assertEqual(dados[0]["modelo"], "modelo-2")
            self.assertEqual(dados[0]["data_pontos"], "2026-10-01")

    def test_falha_de_persistencia_retorna_erro(self) -> None:
        async def processar_falso(imagens, lote=None, estatisticas=None):
            return [_folha()], []

        with (
            patch.object(servico, "processar_folhas", processar_falso),
            patch.object(
                banco,
                "inserir_folhas",
                side_effect=banco.ErroBanco("falha simulada"),
            ),
            self._cliente() as cliente,
        ):
            resposta = cliente.post(
                "/processar",
                files={"arquivos": ("ANA.jpg", b"imagem", "image/jpeg")},
                data={"aceite_ocr": "true"},
                headers={"Idempotency-Key": "teste-banco-falha"},
            )
            self.assertEqual(resposta.status_code, 500)

    def test_resumo_retorna_uma_linha_por_funcionario(self) -> None:
        with patch.object(
            banco.logs,
            "agora",
            return_value=datetime(2026, 10, 21, 12, 0, 0),
        ):
            banco.inserir_folhas(
                [
                    _folha(
                        [
                            _registro(data="01/10/2026", funcionario="ANA"),
                            _registro(data="31/10/2026", funcionario="ANA"),
                        ],
                        funcionario="ANA",
                        origem_hash="hash-ana",
                    )
                ],
                "modelo-1",
            )
            banco.inserir_folhas(
                [
                    _folha(
                        [_registro(data="01/11/2026", funcionario="BRUNO")],
                        funcionario="BRUNO",
                        origem_hash="hash-bruno",
                    )
                ],
                "modelo-2",
            )

        with self._cliente() as cliente:
            resposta = cliente.get("/registros/resumo")
            self.assertEqual(resposta.status_code, 200, resposta.text)
            dados = resposta.json()
            self.assertEqual(len(dados), 2)
            self.assertEqual(
                list(dados[0].keys()),
                [
                    "id",
                    "nome_funcionario",
                    "data_inicio",
                    "data_fim",
                    "data_processado",
                    "modelo",
                ],
            )
            por_nome = {
                item["nome_funcionario"]: item for item in dados
            }
            self.assertEqual(por_nome["ANA"]["data_inicio"], "2026-10-01")
            self.assertEqual(por_nome["ANA"]["data_fim"], "2026-10-31")
            self.assertEqual(por_nome["ANA"]["modelo"], "modelo-1")

    def test_historico_vazio(self) -> None:
        with self._cliente() as cliente:
            resposta = cliente.get("/registros")
            self.assertEqual(resposta.status_code, 200)
            self.assertEqual(resposta.json(), [])


if __name__ == "__main__":
    unittest.main()
