"""Logs diarios em arquivos TXT.

Todas as entradas sao gravadas em ``logs/YYYY-MM-DD.txt`` no fuso configurado.
Falhas de escrita nunca interrompem o processamento principal.

Formato: ``data hora | usuario | NIVEL | componente | [Modelo: x |] mensagem``.
O usuario vem do contexto da requisicao (ver ``definir_usuario``); entradas
fora de uma requisicao autenticada saem como ``Sistema``.
"""

from __future__ import annotations

import contextvars
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config

NIVEIS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_LIMITE_MENSAGEM = 1000
_LOCK = threading.Lock()
_LOG = logging.getLogger("pontofacil.logs")
_TZ_FALLBACK = timezone(timedelta(hours=-3), "America/Sao_Paulo")

USUARIO_SISTEMA = "Sistema"

# Cada requisicao recebe um dicionario proprio (``iniciar_contexto``). Como as
# copias de contexto (threadpool do FastAPI, ``asyncio.to_thread``,
# ``create_task``) compartilham a mesma referencia, o nome definido pela
# dependencia de autenticacao fica visivel em todo o processamento da
# requisicao, inclusive em tarefas de fundo disparadas por ela.
_contexto: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "pontofacil_log_usuario", default=None
)


def iniciar_contexto() -> contextvars.Token:
    """Abre o contexto de log de uma requisicao (chamado pelo middleware)."""
    return _contexto.set({})


def encerrar_contexto(token: contextvars.Token) -> None:
    _contexto.reset(token)


def definir_usuario(nome: str | None) -> None:
    """Registra o usuario autenticado para os logs da requisicao atual."""
    nome = (nome or "").strip()
    if not nome:
        return
    atual = _contexto.get()
    if atual is None:
        _contexto.set({"nome": nome})
    else:
        atual["nome"] = nome


def usuario_atual() -> str:
    atual = _contexto.get()
    return (atual or {}).get("nome") or USUARIO_SISTEMA


def _fuso():
    try:
        return ZoneInfo(config.FUSO_HORARIO)
    except (ZoneInfoNotFoundError, ValueError):
        return _TZ_FALLBACK


def agora() -> datetime:
    """Data/hora atual no fuso da aplicacao."""
    return datetime.now(_fuso())


def sanitizar(valor: object, limite: int = _LIMITE_MENSAGEM) -> str:
    """Remove quebras de linha e separadores que poderiam forjar entradas."""
    texto = " ".join(str(valor or "").split())
    texto = texto.replace("|", "/")
    return texto[:limite]


def registrar(
    nivel: str,
    componente: str,
    mensagem: str,
    lote: str | None = None,
    usuario: str | None = None,
    modelo: str | None = None,
) -> bool:
    """Acrescenta uma entrada ao log do dia. Retorna False em caso de falha."""
    nivel = (nivel or "INFO").upper()
    if nivel not in NIVEIS:
        nivel = "INFO"

    momento = agora()

    partes = [
        f"{momento:%Y-%m-%d %H:%M:%S}",
        sanitizar(usuario or usuario_atual(), 120) or USUARIO_SISTEMA,
        nivel,
        sanitizar(componente, 80) or "-",
    ]

    if modelo:
        partes.append(f"Modelo: {sanitizar(modelo, 80)}")

    partes.append(sanitizar(mensagem) or "-")

    linha = " | ".join(partes)

    if lote:
        linha = f"{linha} | lote={sanitizar(lote, 80)}"

    caminho = config.DIR_LOGS / f"{momento:%Y-%m-%d}.txt"

    try:
        config.DIR_LOGS.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with caminho.open("a", encoding="utf-8", newline="\n") as arquivo:
                arquivo.write(f"{linha}\n")
        return True
    except OSError as exc:
        _LOG.warning("Falha ao gravar log em %s: %s", caminho, exc)
        return False

def debug(
    componente: str,
    mensagem: str,
    lote: str | None = None,
    usuario: str | None = None,
    modelo: str | None = None,
) -> bool:
    return registrar("DEBUG", componente, mensagem, lote, usuario, modelo)


def info(
    componente: str,
    mensagem: str,
    lote: str | None = None,
    usuario: str | None = None,
    modelo: str | None = None,
) -> bool:
    return registrar("INFO", componente, mensagem, lote, usuario, modelo)


def warning(
    componente: str,
    mensagem: str,
    lote: str | None = None,
    usuario: str | None = None,
    modelo: str | None = None,
) -> bool:
    return registrar("WARNING", componente, mensagem, lote, usuario, modelo)


def error(
    componente: str,
    mensagem: str,
    lote: str | None = None,
    usuario: str | None = None,
    modelo: str | None = None,
) -> bool:
    return registrar("ERROR", componente, mensagem, lote, usuario, modelo)
