"""Orquestracao da anonimizacao local de um lote.

Cada imagem e tratada de forma isolada: falha de uma nao interrompe as demais.
O lote so abre a sessao MCP se pelo menos uma imagem for aprovada, e o MCP
recebe exclusivamente ``ArtefatoAnonimizado``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import replace

from .. import config, logs
from . import detector, metadados, pdf, recorte, validacao
from .artefato import ArtefatoAnonimizado
from .schemas import (
    CodigoFalha,
    GeometriaTabela,
    ResultadoAnonimizacao,
    StatusAnonimizacao,
)

log = logging.getLogger("pontofacil")


class ErroAnon(RuntimeError):
    def __init__(
        self,
        codigo: CodigoFalha,
        mensagem: str,
        *,
        diagnostico: str | None = None,
    ) -> None:
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem
        # Apenas para log: rotulos fixos e metricas agregadas, sem texto lido.
        self.diagnostico = diagnostico


def _anonimizar_sync(
    nome_original: str,
    dados: bytes,
    id_opaco: str,
    regioes_extras: tuple[tuple[float, float, float, float], ...] = (),
    modelo: str | None = None,
) -> ResultadoAnonimizacao:
    if not dados:
        raise ErroAnon(CodigoFalha.INVALID_INPUT, "Arquivo vazio.")

    if pdf.eh_pdf(dados):
        try:
            dados = pdf.rasterizar_pdf(dados, dpi=config.ANON_PDF_DPI)
        except pdf.ErroPdf as exc:
            raise ErroAnon(exc.codigo, str(exc)) from exc

    try:
        deteccao = detector.detectar(dados, modelo=modelo)
    except detector.ErroDeteccao as exc:
        raise ErroAnon(
            exc.codigo,
            str(exc),
            diagnostico=getattr(exc, "diagnostico", None),
        ) from exc

    if deteccao.tabela is None:
        raise ErroAnon(
            CodigoFalha.TABLE_NOT_DETECTED,
            "Nenhuma tabela de ponto detectada.",
            diagnostico=f"caixas_texto={len(deteccao.caixas_texto)}",
        )
    if deteccao.cabecalho is None:
        raise ErroAnon(
            CodigoFalha.HEADER_NOT_DETECTED,
            "Nao foi possivel separar o cabecalho da tabela.",
        )

    analise = validacao.analisar_sensiveis(
        deteccao, extras=regioes_extras, modelo=modelo
    )
    if analise.conflito:
        raise ErroAnon(
            CodigoFalha.SENSITIVE_FIELD_CONFLICT,
            analise.motivo or "Campo identificavel em conflito com horarios.",
            diagnostico=analise.detalhe,
        )
    if analise.suspeitos:
        raise ErroAnon(
            CodigoFalha.SENSITIVE_CONTENT_REMAINS,
            "Texto nao classificado permanece dentro da tabela.",
        )

    try:
        imagem = recorte.abrir_imagem(dados)
        caixa_recorte = recorte.calcular_recorte(
            deteccao.tabela,
            imagem.width,
            imagem.height,
            margem=config.ANON_MARGEM_PX,
            cabecalho=deteccao.cabecalho,
        )
    except recorte.ErroRecorte as exc:
        raise ErroAnon(CodigoFalha.CROP_OUT_OF_BOUNDS, str(exc)) from exc

    caixa_recorte = validacao.ajustar_topo_recorte(
        deteccao, caixa_recorte, analise.redigir
    )

    checagem = validacao.validar_recorte(
        deteccao, caixa_recorte, analise.redigir
    )
    if not checagem.ok:
        raise ErroAnon(
            checagem.codigo or CodigoFalha.POSSIBLE_ROW_LOSS,
            checagem.mensagem or "Recorte recusado.",
        )

    recortada = recorte.recortar(imagem, caixa_recorte)
    recortada = recorte.redigir_caixas(
        recortada, analise.redigir, caixa_recorte
    )

    if analise.redigir and not validacao.redacao_efetiva(
        recortada, analise.redigir, caixa_recorte
    ):
        raise ErroAnon(
            CodigoFalha.SENSITIVE_CONTENT_REMAINS,
            "A redacao nao cobriu todo o campo sensivel.",
        )

    recortada = recorte.corrigir_inclinacao(recortada)
    saida = metadados.reencodar_sem_metadados(recortada)

    final = validacao.validar_saida(saida)
    if not final.ok:
        raise ErroAnon(
            final.codigo or CodigoFalha.OUTPUT_IMAGE_INVALID,
            final.mensagem or "Imagem anonimizada invalida.",
        )

    artefato = ArtefatoAnonimizado(saida, id_opaco=id_opaco)
    sha_protegido = hashlib.sha256(saida).hexdigest()
    return ResultadoAnonimizacao(
        status=StatusAnonimizacao.APPROVED,
        nome_original=nome_original,
        artefato=artefato,
        texto_cabecalho=validacao.texto_cabecalho(deteccao),
        geometria=GeometriaTabela.de_caixa(caixa_recorte),
        cabecalho_removido=True,
        metadados_removidos=True,
        verificacao_sensiveis="ok",
        verificacao_integridade="ok",
        modelo_versao=deteccao.modelo_versao,
        id_opaco=id_opaco,
        sha256_protegido=sha_protegido,
        versao=1,
        regioes_extras=regioes_extras,
        regioes_protegidas=tuple(
            getattr(analise, "regioes_protegidas", ()) or ()
        ),
        aviso_ancora=deteccao.aviso_ancora,
    )


async def _processar_uma(
    nome: str,
    dados: bytes,
    lote: str | None,
    semaforo: asyncio.Semaphore,
    regioes_extras: tuple[tuple[float, float, float, float], ...] = (),
    modelo: str | None = None,
) -> ResultadoAnonimizacao:
    id_opaco = uuid.uuid4().hex
    inicio = time.monotonic()
    try:
        async with semaforo:
            resultado = await asyncio.wait_for(
                asyncio.to_thread(
                    _anonimizar_sync,
                    nome,
                    dados,
                    id_opaco,
                    regioes_extras,
                    modelo,
                ),
                timeout=config.ANON_TIMEOUT_SEG,
            )
    except asyncio.TimeoutError:
        logs.warning(
            "Anonimizacao",
            f"Timeout ao anonimizar imagem {nome} (id={id_opaco})",
            lote,
        )
        return ResultadoAnonimizacao.bloqueado(
            nome,
            CodigoFalha.ANONYMIZATION_TIMEOUT,
            "Tempo esgotado na anonimizacao local.",
            id_opaco=id_opaco,
        )
    except ErroAnon as exc:
        logs.warning(
            "Anonimizacao",
            f"Imagem bloqueada: {nome} (id={id_opaco}, modelo={modelo or '-'}): "
            f"{exc.codigo.value} | {exc.mensagem}"
            + (f" | {exc.diagnostico}" if exc.diagnostico else ""),
            lote,
        )
        return ResultadoAnonimizacao.bloqueado(
            nome, exc.codigo, exc.mensagem, id_opaco=id_opaco
        )
    except pdf.ErroPdf as exc:
        return ResultadoAnonimizacao.bloqueado(
            nome, exc.codigo, str(exc), id_opaco=id_opaco
        )
    except detector.ErroDeteccao as exc:
        logs.warning(
            "Anonimizacao",
            f"Detector indisponível para {nome} (id={id_opaco}): {exc.codigo.value}; "
            "imagem aguarda tratamento manual.",
            lote,
        )
        return ResultadoAnonimizacao(
            status=StatusAnonimizacao.PENDING_TREATMENT,
            nome_original=nome,
            artefato=None,
            codigo_falha=exc.codigo,
            mensagem=str(exc),
            id_opaco=id_opaco,
            tratamento_necessario=True,
        )
    except Exception as exc:  # noqa: BLE001
        logs.error(
            "Anonimizacao",
            f"Erro inesperado em {nome} (id={id_opaco}): {type(exc).__name__}",
            lote,
        )
        return ResultadoAnonimizacao.erro(
            nome,
            CodigoFalha.INTERNAL_ERROR,
            "Falha interna na anonimizacao local.",
            id_opaco=id_opaco,
        )

    duracao = time.monotonic() - inicio
    logs.info(
        "Anonimizacao",
        f"Imagem aprovada em {duracao:.2f}s: {nome} (id={id_opaco}, "
        f"modelo={modelo or '-'}, aviso={resultado.aviso_ancora or '-'}, "
        f"{len(resultado.artefato or b'')} bytes)",
        lote,
    )
    return replace(resultado, id_opaco=id_opaco)


async def processar_lote(
    imagens: list[tuple[str, bytes]],
    lote: str | None = None,
    regioes_extras_por_imagem: dict[str, tuple[tuple[float, float, float, float], ...]] | None = None,
    modelo: str | None = None,
) -> list[ResultadoAnonimizacao]:
    """Anonimiza cada imagem do lote, preservando a ordem de entrada.

    ``modelo`` seleciona a regra de limite do recorte. ``None`` mantem o
    comportamento generico anterior (compatibilidade com chamadas antigas).
    """
    if not imagens:
        return []

    inicio = time.monotonic()
    logs.info(
        "Anonimizacao",
        f"OCR local iniciado: {len(imagens)} imagem(ns), "
        f"modelo={modelo or '-'}",
        lote,
    )

    semaforo = asyncio.Semaphore(config.ANON_CONCORRENCIA)
    tarefas = [
        asyncio.create_task(
            _processar_uma(
                nome,
                dados,
                lote,
                semaforo,
                regioes_extras_por_imagem.get(nome, ()) if regioes_extras_por_imagem else (),
                modelo,
            )
        )
        for nome, dados in imagens
    ]
    resultados = list(await asyncio.gather(*tarefas))
    _registrar_resumo(resultados, lote, time.monotonic() - inicio)
    return resultados


_LIMITE_LINHA_RESUMO = 900


def _em_linhas(titulo: str, itens: list[str]) -> list[str]:
    """Quebra a lista em linhas que cabem no limite de uma entrada de log."""
    linhas: list[str] = []
    atual: list[str] = []
    for item in itens:
        candidato = ", ".join([*atual, item])
        if atual and len(titulo) + len(candidato) > _LIMITE_LINHA_RESUMO:
            linhas.append(titulo + ", ".join(atual))
            atual = []
        atual.append(item)
    if atual:
        linhas.append(titulo + ", ".join(atual))
    return linhas


def _registrar_resumo(
    resultados: list[ResultadoAnonimizacao],
    lote: str | None,
    duracao: float,
) -> None:
    processadas = [
        r.nome_original
        for r in resultados
        if r.status == StatusAnonimizacao.APPROVED
    ]
    nao_processadas = [
        f"{r.nome_original} ({r.codigo_falha.value if r.codigo_falha else r.status.value})"
        for r in resultados
        if r.status != StatusAnonimizacao.APPROVED
    ]
    logs.info(
        "Anonimizacao",
        f"OCR local concluido em {duracao:.2f}s: "
        f"{len(processadas)} processada(s), "
        f"{len(nao_processadas)} nao processada(s)",
        lote,
    )
    for linha in _em_linhas("Processadas: ", processadas):
        logs.info("Anonimizacao", linha, lote)
    for linha in _em_linhas("Nao processadas: ", nao_processadas):
        logs.warning("Anonimizacao", linha, lote)
