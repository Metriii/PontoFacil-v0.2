"""Rotas de autenticacao em duas etapas (senha + codigo por e-mail)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import auditoria, autenticacao, config, logs, seguranca, usuarios

router = APIRouter(tags=["autenticacao"], prefix="/auth")


def _auditar(
    email: str | None, action: str, status: str, erro: object | None = None
) -> None:
    """Auditoria de autenticacao e best-effort: nunca bloqueia o login."""
    if not email:
        return
    auditoria.registrar(
        user_email=email,
        action=action,
        status=status,
        error_summary=erro,
    )


class LoginEntrada(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    senha: str = Field(min_length=1, max_length=256)


class CodigoEntrada(BaseModel):
    codigo: str = Field(min_length=1, max_length=12)


class SenhaEntrada(BaseModel):
    senha_atual: str = Field(min_length=1, max_length=256)
    nova_senha: str = Field(min_length=1, max_length=256)


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _definir_cookie_desafio(response: Response, token: str) -> None:
    response.set_cookie(
        config.COOKIE_DESAFIO,
        token,
        max_age=config.CODIGO_TTL_MIN * 60 + 60,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="lax",
        path="/auth",
    )


def _definir_cookie_sessao(response: Response, token: str) -> None:
    # Sem max_age: cookie de sessao, descartado ao fechar o navegador. A
    # expiracao absoluta continua sendo validada no servidor.
    response.set_cookie(
        config.COOKIE_SESSAO,
        token,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def _limpar_cookie_desafio(response: Response) -> None:
    response.delete_cookie(config.COOKIE_DESAFIO, path="/auth")


def _limpar_cookie_sessao(response: Response) -> None:
    response.delete_cookie(config.COOKIE_SESSAO, path="/")


def _erro(exc: autenticacao.ErroAutenticacao) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.mensagem)


@router.post("/login")
def login(
    dados: LoginEntrada, request: Request, response: Response
) -> dict[str, object]:
    try:
        token, expira = autenticacao.iniciar_login(
            dados.email, dados.senha, ip=_ip(request)
        )
    except autenticacao.ErroAutenticacao as exc:
        _auditar(
            usuarios.normalizar_email(dados.email),
            "auth_login",
            "failed",
            exc.mensagem,
        )
        raise _erro(exc) from exc

    _auditar(usuarios.normalizar_email(dados.email), "auth_login", "success")
    _definir_cookie_desafio(response, token)
    return {
        "status": "codigo_enviado",
        "expira_em": expira,
        "reenvio_em_segundos": config.CODIGO_REENVIO_SEG,
    }


@router.post("/verificar")
def verificar(
    dados: CodigoEntrada, request: Request, response: Response
) -> dict[str, object]:
    token = request.cookies.get(config.COOKIE_DESAFIO)
    email = autenticacao.email_do_desafio(token)
    try:
        usuario, sessao = autenticacao.verificar_codigo(
            token, dados.codigo, ip=_ip(request)
        )
    except autenticacao.ErroAutenticacao as exc:
        _auditar(email, "auth_codigo", "failed", exc.mensagem)
        raise _erro(exc) from exc

    logs.definir_usuario(str(usuario.get("nome") or usuario.get("email") or ""))
    _auditar(str(usuario["email"]), "auth_codigo", "success")
    _limpar_cookie_desafio(response)
    _definir_cookie_sessao(response, sessao)
    return {
        "usuario": {
            "email": usuario["email"],
            "nome": usuario["nome"],
            "papel": usuario["papel"],
            "senha_temporaria": bool(usuario["senha_temporaria"]),
        },
        "csrf_token": autenticacao.csrf_esperado(sessao),
    }


@router.post("/reenviar")
def reenviar(request: Request) -> dict[str, object]:
    token = request.cookies.get(config.COOKIE_DESAFIO)
    email = autenticacao.email_do_desafio(token)
    try:
        expira, cooldown = autenticacao.reenviar_codigo(token, ip=_ip(request))
    except autenticacao.ErroAutenticacao as exc:
        _auditar(email, "auth_codigo", "failed", exc.mensagem)
        raise _erro(exc) from exc
    _auditar(email, "auth_codigo", "success")
    return {
        "status": "codigo_reenviado",
        "expira_em": expira,
        "reenvio_em_segundos": cooldown,
    }


@router.post("/logout", response_model=None)
def logout(request: Request, response: Response) -> dict[str, str] | JSONResponse:
    token = request.cookies.get(config.COOKIE_SESSAO)
    usuario = autenticacao.usuario_da_sessao(token)
    if usuario is not None:
        enviado = request.headers.get("X-CSRF-Token")
        if not autenticacao.csrf_valido(token, enviado):
            _auditar(str(usuario["email"]), "auth_logout", "failed", "CSRF invalido")
            # Mesmo recusando a acao, o navegador nao deve continuar com o
            # cookie de sessao. O frontend busca o CSRF antes de sair.
            resposta = JSONResponse(
                status_code=403,
                content={"detail": "Token CSRF ausente ou invalido."},
            )
            _limpar_cookie_sessao(resposta)
            _limpar_cookie_desafio(resposta)
            return resposta
        autenticacao.revogar_sessao(token)
        _auditar(str(usuario["email"]), "auth_logout", "success")
    _limpar_cookie_sessao(response)
    _limpar_cookie_desafio(response)
    return {"status": "saiu"}


@router.get("/eu")
def eu(request: Request) -> dict[str, object]:
    token = request.cookies.get(config.COOKIE_SESSAO)
    usuario = autenticacao.usuario_da_sessao(token)
    if usuario is None or token is None:
        return {"autenticado": False}
    return {
        "autenticado": True,
        "usuario": {
            "email": usuario["email"],
            "nome": usuario["nome"],
            "papel": usuario["papel"],
            "senha_temporaria": bool(usuario["senha_temporaria"]),
        },
        "csrf_token": autenticacao.csrf_esperado(token),
    }


@router.post("/senha")
def alterar_senha(
    dados: SenhaEntrada,
    usuario: dict[str, object] = Depends(autenticacao.exigir_csrf),
) -> dict[str, object]:
    """Troca a propria senha; exige a senha atual e revoga as outras sessoes."""
    email = str(usuario["email"])
    registro = usuarios.buscar(email)
    if registro is None:
        raise HTTPException(status_code=401, detail="Sessao invalida.")

    if not seguranca.verificar_senha(
        dados.senha_atual, str(registro["senha_hash"])
    ) or seguranca.verificar_senha(
        dados.nova_senha, str(registro["senha_hash"])
    ):
        _auditar(email, "auth_senha_alterada", "failed", "Senha atual invalida")
        raise HTTPException(
            status_code=403,
            detail="Senha atual invalida (ou a nova senha e igual a atual).",
        )

    try:
        usuarios.validar_senha(dados.nova_senha)
    except usuarios.ErroUsuario as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        usuarios.definir_senha(email, dados.nova_senha, temporaria=False)
    except usuarios.ErroUsuario as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    revogadas = autenticacao.revogar_outras_sessoes(
        int(usuario["id"]), str(usuario.get("sessao_token") or "")
    )
    _auditar(email, "auth_senha_alterada", "success")
    return {"status": "senha_alterada", "sessoes_revogadas": revogadas}
