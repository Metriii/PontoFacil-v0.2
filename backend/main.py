"""API FastAPI do PontoFácil — ponte entre o frontend e o OCR (MCP PaddleOCR).

Execucao (a partir da raiz do projeto, com o venv ativo):

    uvicorn backend.main:app --reload --port 8090

A porta 8090 evita a 8000: o OpenCode (agente de desenvolvimento) procura um
servidor vLLM em http://127.0.0.1:8000/v1 e sonda /health + /v1/models
periodicamente. Como esta API responde /health com 200, as sondagens
apareciam no log do uvicorn como 404 em /v1/models.

Variaveis de ambiente relevantes:
    PADDLEOCR_MCP_AISTUDIO_ACCESS_TOKEN  (ou arquivo token.txt na raiz)
    PADDLEOCR_MCP_MODEL                  (padrao: PaddleOCR-VL-1.6)
    PADDLEOCR_MCP_PPOCR_SOURCE           (padrao: aistudio)
    PONTOFACIL_CORS_ORIGINS              (padrao: *)
    PONTOFACIL_AMBIENTE                  (dev | producao)
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import (
    auditoria,
    autenticacao,
    banco,
    config,
    excel,
    logs,
    processamentos,
    sincronizacao_excel,
)
from .anonimizacao import previa
from .rotas import auth, download, planilhas, processar, registros
from .rotas import admin_usuarios
from .rotas import anonimizacao
from .rotas import auditoria as rota_auditoria

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="PontoFácil API",
    description="OCR de folhas de ponto e geração de planilhas Excel.",
    version="0.1.0",
)

_ORIGENS = config.CORS_ORIGINS
# Com cookies, "*" precisa ser refletido como a origem real da requisicao.
_ORIGEM_REGEX = None
if "*" in _ORIGENS:
    _ORIGENS = []
    _ORIGEM_REGEX = ".*"

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ORIGENS,
    allow_origin_regex=_ORIGEM_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)

# Tudo que nao e autenticacao exige sessao valida. Contas com senha temporaria
# so podem trocar a senha (rotas /auth) ate definir uma senha definitiva, e as
# escritas exigem tambem o header X-CSRF-Token (ver exigir_csrf nas rotas).
_ROTAS_PROTEGIDAS = [Depends(autenticacao.exigir_senha_definitiva)]
app.include_router(processar.router, dependencies=_ROTAS_PROTEGIDAS)
app.include_router(anonimizacao.router, dependencies=_ROTAS_PROTEGIDAS)
app.include_router(download.router, dependencies=_ROTAS_PROTEGIDAS)
app.include_router(registros.router, dependencies=_ROTAS_PROTEGIDAS)
app.include_router(planilhas.router, dependencies=_ROTAS_PROTEGIDAS)
app.include_router(rota_auditoria.router, dependencies=_ROTAS_PROTEGIDAS)
app.include_router(admin_usuarios.router, dependencies=_ROTAS_PROTEGIDAS)


@app.middleware("http")
async def _contexto_log(request, call_next):
    """Isola o usuario dos logs por requisicao (ver ``logs.definir_usuario``)."""
    token = logs.iniciar_contexto()
    try:
        return await call_next(request)
    finally:
        logs.encerrar_contexto(token)


@app.middleware("http")
async def _cabecalhos_seguranca(request, call_next):
    """Headers basicos de endurecimento para a API."""
    resposta = await call_next(request)
    resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
    resposta.headers.setdefault("X-Frame-Options", "DENY")
    resposta.headers.setdefault("Referrer-Policy", "no-referrer")
    resposta.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    if request.url.path.startswith("/auth"):
        resposta.headers.setdefault("Cache-Control", "no-store")
    if config.PRODUCAO:
        resposta.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return resposta


@app.on_event("startup")
def _startup() -> None:
    config.validar_ambiente()
    config.garantir_diretorios()
    banco.inicializar()
    autenticacao.inicializar()
    auditoria.inicializar()
    processamentos.inicializar()
    previa.inicializar()
    autenticacao.limpar_expirados()
    excel.limpar_planilhas_individuais(config.DIR_SAIDA)
    sincronizacao_excel.iniciar()
    logs.info(
        "Startup",
        f"Aplicacao iniciada; banco={banco.caminho_banco()} logs={config.DIR_LOGS} "
        f"ambiente={config.AMBIENTE}",
    )


@app.on_event("shutdown")
def _shutdown() -> None:
    sincronizacao_excel.parar()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
