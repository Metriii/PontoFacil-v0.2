"""Mapeamento do Modelo 5: layout de duas linhas por data.

Reaproveita o mapper do Modelo 4 forçando o layout ``duas-linhas``, o que
mantém a planilha listando apenas os dias preenchidos (o cartão mensal não
lista os dias em branco da quinzena).
"""

from __future__ import annotations

from dataclasses import replace

from ..modelo4 import mapper as _base
from ..modelo4.parser import LAYOUT_DUAS_LINHAS
from ..modelos import Registro
from . import parser as _parser

map_registro = _base.map_registro
registro_tem_conteudo = _base.registro_tem_conteudo


def mapear_folha(
    linhas: list[list[str]],
    referencia: str | None,
    funcionario: str | None,
    jornada: object | None = None,
) -> tuple[list[Registro], list[list[str]]]:
    """Duas linhas por data: 1a = manha (entrada/saida intervalo), 2a = tarde.

    As linhas sao pareadas pela ordem e pelo turno (``parser.parear_turnos``),
    nao so pela data lida, que o OCR erra com frequencia no caderno.
    """
    del jornada
    mes, ano = _base._periodo_referencia(referencia)
    campos = [
        replace(campo, layout=LAYOUT_DUAS_LINHAS)
        for linha in linhas
        if (campo := _parser.separar_linha(linha, mes, ano)) is not None
    ]
    campos = _parser.normalizar_anos(campos)
    campos = _parser.parear_turnos(campos)

    registros: list[Registro] = []
    linhas_excel: list[list[str]] = []
    for indice, grupo in enumerate(_base._grupos(campos).values()):
        registro, linha_excel = _base._mapear_grupo(
            grupo, indice, referencia, funcionario
        )
        linhas_excel.append(linha_excel)
        if registro_tem_conteudo(registro):
            registros.append(registro)
    return registros, linhas_excel
