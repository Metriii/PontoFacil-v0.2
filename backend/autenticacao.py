"""Autenticacao em duas etapas (senha + codigo por e-mail) e sessoes.

O fluxo e propositalmente simples e sem dependencia externa:

1. ``iniciar_login`` valida e-mail/senha, aplica rate limit e cria um desafio
   com codigo de 6 digitos (hash HMAC, TTL curto, uso unico).
2. ``verificar_codigo`` consome o desafio e cria a sessao.
3. A sessao e um token opaco em cookie httpOnly; o banco guarda apenas o hash.
4. O token CSRF e derivado da sessao por HMAC e exigido nas escritas.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request

from . import banco, config, emailer, logs, seguranca, usuarios

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS sessoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    usuario_id INTEGER NOT NULL,
    criado_em TEXT NOT NULL,
    expira_em TEXT NOT NULL,
    ultimo_uso TEXT NOT NULL,
    ip TEXT,
    agente TEXT,
    revogado_em TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessoes_usuario
    ON sessoes (usuario_id, revogado_em);

CREATE TABLE IF NOT EXISTS desafios_login (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    codigo_hash TEXT NOT NULL,
    tentativas INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL,
    enviado_em TEXT NOT NULL,
    expira_em TEXT NOT NULL,
    consumido_em TEXT,
    ip TEXT
);
CREATE INDEX IF NOT EXISTS idx_desafios_usuario
    ON desafios_login (usuario_id, consumido_em);

CREATE TABLE IF NOT EXISTS eventos_login (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chave TEXT NOT NULL,
    tipo TEXT NOT NULL,
    sucesso INTEGER NOT NULL,
    criado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eventos_login
    ON eventos_login (chave, criado_em);
"""


class ErroAutenticacao(RuntimeError):
    """Falha esperada do fluxo de login, com status HTTP sugerido."""

    def __init__(self, mensagem: str, status: int = 400) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(momento: datetime) -> str:
    return momento.astimezone(timezone.utc).isoformat(timespec="seconds")


def _ler_data(texto: object) -> datetime | None:
    try:
        momento = datetime.fromisoformat(str(texto))
    except (TypeError, ValueError):
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento


def inicializar() -> None:
    """Cria as tabelas de autenticacao (idempotente)."""
    usuarios.inicializar()
    conexao = banco.conectar()
    try:
        with conexao:
            conexao.executescript(_ESQUEMA)
            conexao.execute("PRAGMA user_version=3")
    except sqlite3.Error as exc:
        logs.error("Auth", f"Falha ao inicializar autenticacao: {exc}")
        raise RuntimeError(f"Falha ao inicializar autenticacao: {exc}") from exc
    finally:
        conexao.close()


# ---------------------------------------------------------------------------
# Rate limit (tentativas de senha por e-mail e por IP)
# ---------------------------------------------------------------------------
def _registrar_evento(chave: str, tipo: str, sucesso: bool) -> None:
    conexao = banco.conectar()
    try:
        with conexao:
            conexao.execute(
                "INSERT INTO eventos_login (chave, tipo, sucesso, criado_em) "
                "VALUES (?, ?, ?, ?)",
                (chave, tipo, 1 if sucesso else 0, _iso(_agora())),
            )
            limite = _iso(_agora() - timedelta(days=2))
            conexao.execute(
                "DELETE FROM eventos_login WHERE criado_em < ?", (limite,)
            )
    except sqlite3.Error as exc:
        logs.warning("Auth", f"Nao foi possivel registrar tentativa: {exc}")
    finally:
        conexao.close()


def falhas_recentes(chave: str, janela_min: int | None = None) -> int:
    janela = janela_min or config.LOGIN_JANELA_MIN
    limite = _iso(_agora() - timedelta(minutes=janela))
    conexao = banco.conectar()
    try:
        linha = conexao.execute(
            "SELECT COUNT(*) AS total FROM eventos_login "
            "WHERE chave = ? AND sucesso = 0 AND criado_em >= ?",
            (chave, limite),
        ).fetchone()
    except sqlite3.Error as exc:
        logs.warning("Auth", f"Nao foi possivel consultar tentativas: {exc}")
        return 0
    finally:
        conexao.close()
    return int(linha["total"] if linha else 0)


def _limpar_falhas(chave: str) -> None:
    conexao = banco.conectar()
    try:
        with conexao:
            conexao.execute(
                "DELETE FROM eventos_login WHERE chave = ? AND sucesso = 0",
                (chave,),
            )
    except sqlite3.Error:
        pass
    finally:
        conexao.close()


def _aplicar_rate_limit(email: str, ip: str | None) -> None:
    if falhas_recentes(f"email:{email}") >= config.LOGIN_MAX_FALHAS_EMAIL:
        raise ErroAutenticacao(
            "Muitas tentativas. Aguarde alguns minutos e tente novamente.", 429
        )
    if ip and falhas_recentes(f"ip:{ip}") >= config.LOGIN_MAX_FALHAS_IP:
        raise ErroAutenticacao(
            "Muitas tentativas. Aguarde alguns minutos e tente novamente.", 429
        )


# ---------------------------------------------------------------------------
# Desafios de codigo
# ---------------------------------------------------------------------------
def _criar_desafio(usuario_id: int, ip: str | None) -> tuple[str, str, str]:
    token = seguranca.gerar_token()
    codigo = seguranca.gerar_codigo()
    agora = _agora()
    expira = agora + timedelta(minutes=config.CODIGO_TTL_MIN)
    codigo_hash = seguranca.hash_codigo(config.segredo_sessao(), token, codigo)

    conexao = banco.conectar()
    try:
        with conexao:
            conexao.execute(
                """
                INSERT INTO desafios_login (
                    usuario_id, token_hash, codigo_hash, tentativas,
                    criado_em, enviado_em, expira_em, ip
                ) VALUES (?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    usuario_id,
                    seguranca.hash_token(token),
                    codigo_hash,
                    _iso(agora),
                    _iso(agora),
                    _iso(expira),
                    ip,
                ),
            )
    except sqlite3.Error as exc:
        raise ErroAutenticacao("Nao foi possivel iniciar o login.", 500) from exc
    finally:
        conexao.close()

    return token, codigo, _iso(expira)


def _desafio_por_token(token: str | None) -> dict[str, object] | None:
    if not token:
        return None
    conexao = banco.conectar()
    try:
        linha = conexao.execute(
            "SELECT * FROM desafios_login WHERE token_hash = ?",
            (seguranca.hash_token(token),),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conexao.close()
    if linha is None:
        return None
    return dict(linha)


def iniciar_login(email: str, senha: str, ip: str | None = None) -> tuple[str, str]:
    """Valida a senha e envia o codigo. Devolve ``(token_desafio, expira_em)``."""
    email = usuarios.normalizar_email(email)
    _aplicar_rate_limit(email, ip)

    usuario = usuarios.buscar(email)
    if usuario and usuario["ativo"]:
        valido = seguranca.verificar_senha(senha, usuario.get("senha_hash"))
    else:
        # Gasta o mesmo tempo de scrypt para nao revelar se o e-mail existe.
        seguranca.verificar_senha(senha, seguranca.HASH_SENHA_FALSO)
        valido = False

    if not valido:
        _registrar_evento(f"email:{email}", "senha", False)
        if ip:
            _registrar_evento(f"ip:{ip}", "senha", False)
        raise ErroAutenticacao("E-mail ou senha invalidos.", 401)

    _limpar_falhas(f"email:{email}")
    _registrar_evento(f"email:{email}", "senha", True)

    assert usuario is not None
    token, codigo, expira = _criar_desafio(int(usuario["id"]), ip)
    try:
        emailer.enviar_codigo(email, codigo, config.CODIGO_TTL_MIN)
    except Exception as exc:  # noqa: BLE001
        _consumir_desafio(token)
        logs.error("Auth", f"Falha ao enviar codigo por e-mail: {exc}")
        raise ErroAutenticacao(
            "Nao foi possivel enviar o codigo agora. Tente novamente.", 503
        ) from exc

    return token, expira


def reenviar_codigo(token: str | None, ip: str | None = None) -> tuple[str, int]:
    """Reemite o codigo do desafio atual, respeitando o cooldown."""
    desafio = _desafio_por_token(token)
    if desafio is None or desafio.get("consumido_em"):
        raise ErroAutenticacao("Sessao de login expirada. Informe a senha novamente.", 401)

    agora = _agora()
    expira_atual = _ler_data(desafio.get("expira_em"))
    if expira_atual is None or expira_atual <= agora:
        raise ErroAutenticacao("Codigo expirado. Informe a senha novamente.", 401)

    enviado = _ler_data(desafio.get("enviado_em"))
    if enviado is not None:
        decorrido = int((agora - enviado).total_seconds())
        if decorrido < config.CODIGO_REENVIO_SEG:
            faltam = config.CODIGO_REENVIO_SEG - decorrido
            raise ErroAutenticacao(
                f"Aguarde {faltam}s antes de reenviar o codigo.", 429
            )

    usuario = None
    conexao = banco.conectar()
    try:
        linha = conexao.execute(
            "SELECT email, ativo FROM usuarios WHERE id = ?",
            (int(desafio["usuario_id"]),),
        ).fetchone()
        if linha is not None and bool(linha["ativo"]):
            usuario = str(linha["email"])
    except sqlite3.Error:
        usuario = None
    finally:
        conexao.close()

    if usuario is None:
        raise ErroAutenticacao("Sessao de login expirada. Informe a senha novamente.", 401)

    assert token is not None
    codigo = seguranca.gerar_codigo()
    codigo_hash = seguranca.hash_codigo(config.segredo_sessao(), token, codigo)
    novo_expira = agora + timedelta(minutes=config.CODIGO_TTL_MIN)
    conexao = banco.conectar()
    try:
        with conexao:
            conexao.execute(
                """
                UPDATE desafios_login
                   SET codigo_hash = ?, tentativas = 0, enviado_em = ?,
                       expira_em = ?, ip = ?
                 WHERE id = ?
                """,
                (
                    codigo_hash,
                    _iso(agora),
                    _iso(novo_expira),
                    ip,
                    int(desafio["id"]),
                ),
            )
    except sqlite3.Error as exc:
        raise ErroAutenticacao("Nao foi possivel reenviar o codigo.", 500) from exc
    finally:
        conexao.close()

    try:
        emailer.enviar_codigo(usuario, codigo, config.CODIGO_TTL_MIN)
    except Exception as exc:  # noqa: BLE001
        logs.error("Auth", f"Falha ao reenviar codigo por e-mail: {exc}")
        raise ErroAutenticacao(
            "Nao foi possivel enviar o codigo agora. Tente novamente.", 503
        ) from exc

    return _iso(novo_expira), config.CODIGO_REENVIO_SEG


def _consumir_desafio(token: str) -> bool:
    conexao = banco.conectar()
    try:
        with conexao:
            cursor = conexao.execute(
                "UPDATE desafios_login SET consumido_em = ? "
                "WHERE token_hash = ? AND consumido_em IS NULL",
                (_iso(_agora()), seguranca.hash_token(token)),
            )
            return bool(cursor.rowcount)
    except sqlite3.Error:
        return False
    finally:
        conexao.close()


def verificar_codigo(
    token: str | None, codigo: str, ip: str | None = None
) -> tuple[dict[str, object], str]:
    """Consome o desafio e cria a sessao. Devolve ``(usuario, token_sessao)``."""
    desafio = _desafio_por_token(token)
    if desafio is None or desafio.get("consumido_em"):
        raise ErroAutenticacao("Codigo invalido ou expirado. Tente novamente.", 401)

    agora = _agora()
    expira = _ler_data(desafio.get("expira_em"))
    tentativas = int(desafio.get("tentativas") or 0)
    if expira is None or expira <= agora:
        _consumir_desafio(str(token))
        raise ErroAutenticacao("Codigo expirado. Solicite um novo codigo.", 401)
    if tentativas >= config.CODIGO_MAX_TENTATIVAS:
        _consumir_desafio(str(token))
        raise ErroAutenticacao(
            "Muitas tentativas com este codigo. Solicite um novo.", 429
        )

    codigo_limpo = "".join(caractere for caractere in (codigo or "") if caractere.isdigit())
    esperado = seguranca.hash_codigo(
        config.segredo_sessao(), str(token), codigo_limpo
    )
    valido = len(codigo_limpo) == 6 and seguranca.comparar_segredo(
        esperado, str(desafio.get("codigo_hash") or "")
    )

    conexao = banco.conectar()
    try:
        with conexao:
            conexao.execute(
                "UPDATE desafios_login SET tentativas = tentativas + 1 WHERE id = ?",
                (int(desafio["id"]),),
            )
    except sqlite3.Error as exc:
        raise ErroAutenticacao("Nao foi possivel validar o codigo.", 500) from exc
    finally:
        conexao.close()

    if not valido:
        restantes = config.CODIGO_MAX_TENTATIVAS - (tentativas + 1)
        if restantes <= 0:
            _consumir_desafio(str(token))
            raise ErroAutenticacao(
                "Muitas tentativas com este codigo. Solicite um novo.", 429
            )
        raise ErroAutenticacao(
            f"Codigo invalido. Tentativas restantes: {restantes}.", 401
        )

    usuario = _usuario_por_id(int(desafio["usuario_id"]))
    if usuario is None or not usuario["ativo"]:
        raise ErroAutenticacao("Conta indisponivel. Procure o administrador.", 401)

    if not _consumir_desafio(str(token)):
        raise ErroAutenticacao("Codigo invalido ou expirado. Tente novamente.", 401)
    sessao = criar_sessao(int(usuario["id"]), ip=ip)
    usuarios.registrar_login(str(usuario["email"]), ip)
    _registrar_evento(f"email:{usuario['email']}", "codigo", True)
    return usuario, sessao


def email_do_desafio(token: str | None) -> str | None:
    """E-mail do desafio, para auditoria de tentativas de codigo."""
    if not token:
        return None
    conexao = banco.conectar()
    try:
        linha = conexao.execute(
            """
            SELECT u.email
              FROM desafios_login d
              JOIN usuarios u ON u.id = d.usuario_id
             WHERE d.token_hash = ?
            """,
            (seguranca.hash_token(token),),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conexao.close()
    return str(linha["email"]) if linha else None


def _usuario_por_id(usuario_id: int) -> dict[str, object] | None:
    conexao = banco.conectar()
    try:
        linha = conexao.execute(
            "SELECT * FROM usuarios WHERE id = ?", (usuario_id,)
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conexao.close()
    if linha is None:
        return None
    return {
        "id": int(linha["id"]),
        "email": str(linha["email"]),
        "nome": linha["nome"],
        "senha_hash": str(linha["senha_hash"]),
        "ativo": bool(linha["ativo"]),
        "criado_em": str(linha["criado_em"]),
        "atualizado_em": str(linha["atualizado_em"]),
        "senha_alterada_em": linha["senha_alterada_em"],
        "papel": str(linha["papel"] or usuarios.PAPEL_OPERADOR),
        "senha_temporaria": bool(linha["senha_temporaria"]),
    }


# ---------------------------------------------------------------------------
# Sessoes e CSRF
# ---------------------------------------------------------------------------
def criar_sessao(
    usuario_id: int, ip: str | None = None, agente: str | None = None
) -> str:
    token = seguranca.gerar_token()
    agora = _agora()
    expira = agora + timedelta(days=config.SESSAO_ABSOLUTA_DIAS)
    conexao = banco.conectar()
    try:
        with conexao:
            conexao.execute(
                """
                INSERT INTO sessoes (
                    token_hash, usuario_id, criado_em, expira_em,
                    ultimo_uso, ip, agente
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    seguranca.hash_token(token),
                    usuario_id,
                    _iso(agora),
                    _iso(expira),
                    _iso(agora),
                    ip,
                    (agente or "")[:300] or None,
                ),
            )
    except sqlite3.Error as exc:
        raise ErroAutenticacao("Nao foi possivel criar a sessao.", 500) from exc
    finally:
        conexao.close()
    return token


def usuario_da_sessao(token: str | None) -> dict[str, object] | None:
    if not token:
        return None
    agora = _agora()
    conexao = banco.conectar()
    try:
        linha = conexao.execute(
            """
            SELECT s.id AS sessao_id, s.ultimo_uso, s.expira_em, s.revogado_em,
                   u.id AS usuario_id, u.email, u.nome, u.ativo,
                   u.papel, u.senha_temporaria
              FROM sessoes s
              JOIN usuarios u ON u.id = s.usuario_id
             WHERE s.token_hash = ?
            """,
            (seguranca.hash_token(token),),
        ).fetchone()
        if linha is None:
            return None

        expira = _ler_data(linha["expira_em"])
        ultimo_uso = _ler_data(linha["ultimo_uso"])
        if (
            linha["revogado_em"]
            or not linha["ativo"]
            or expira is None
            or expira <= agora
            or ultimo_uso is None
            or agora - ultimo_uso > timedelta(minutes=config.SESSAO_INATIVIDADE_MIN)
        ):
            return None

        if (agora - ultimo_uso).total_seconds() > 60:
            with conexao:
                conexao.execute(
                    "UPDATE sessoes SET ultimo_uso = ? WHERE id = ?",
                    (_iso(agora), int(linha["sessao_id"])),
                )

        return {
            "id": int(linha["usuario_id"]),
            "email": str(linha["email"]),
            "nome": linha["nome"],
            "papel": str(linha["papel"] or usuarios.PAPEL_OPERADOR),
            "senha_temporaria": bool(linha["senha_temporaria"]),
        }
    except sqlite3.Error as exc:
        logs.warning("Auth", f"Falha ao consultar sessao: {exc}")
        return None
    finally:
        conexao.close()


def revogar_sessao(token: str | None) -> None:
    if not token:
        return
    conexao = banco.conectar()
    try:
        with conexao:
            conexao.execute(
                "UPDATE sessoes SET revogado_em = ? "
                "WHERE token_hash = ? AND revogado_em IS NULL",
                (_iso(_agora()), seguranca.hash_token(token)),
            )
    except sqlite3.Error as exc:
        logs.warning("Auth", f"Falha ao revogar sessao: {exc}")
    finally:
        conexao.close()


def revogar_sessoes_usuario(usuario_id: int) -> int:
    """Revoga todas as sessoes abertas do usuario. Devolve quantas cairam."""
    conexao = banco.conectar()
    try:
        with conexao:
            cursor = conexao.execute(
                "UPDATE sessoes SET revogado_em = ? "
                "WHERE usuario_id = ? AND revogado_em IS NULL",
                (_iso(_agora()), usuario_id),
            )
            return int(cursor.rowcount or 0)
    except sqlite3.Error as exc:
        logs.warning("Auth", f"Falha ao revogar sessoes do usuario: {exc}")
        return 0
    finally:
        conexao.close()


def revogar_outras_sessoes(usuario_id: int, token_atual: str | None) -> int:
    """Revoga as sessoes do usuario exceto a sessao usada na troca de senha."""
    atual = seguranca.hash_token(token_atual or "")
    conexao = banco.conectar()
    try:
        with conexao:
            cursor = conexao.execute(
                "UPDATE sessoes SET revogado_em = ? "
                "WHERE usuario_id = ? AND revogado_em IS NULL "
                "AND token_hash <> ?",
                (_iso(_agora()), usuario_id, atual),
            )
            return int(cursor.rowcount or 0)
    except sqlite3.Error as exc:
        logs.warning("Auth", f"Falha ao revogar outras sessoes: {exc}")
        return 0
    finally:
        conexao.close()


def csrf_esperado(token_sessao: str) -> str:
    return seguranca.csrf_token(config.segredo_sessao(), token_sessao)


def csrf_valido(token_sessao: str | None, enviado: str | None) -> bool:
    if not token_sessao:
        return False
    return seguranca.comparar_segredo(csrf_esperado(token_sessao), enviado)


def limpar_expirados(dias: int = 7) -> None:
    """Remove sessoes/desafios antigos (chamado no startup)."""
    limite = _iso(_agora() - timedelta(days=dias))
    conexao = banco.conectar()
    try:
        with conexao:
            conexao.execute(
                "DELETE FROM sessoes WHERE expira_em < ? OR revogado_em < ?",
                (limite, limite),
            )
            conexao.execute(
                "DELETE FROM desafios_login WHERE expira_em < ?", (limite,)
            )
            conexao.execute("DELETE FROM eventos_login WHERE criado_em < ?", (limite,))
    except sqlite3.Error:
        pass
    finally:
        conexao.close()


# ---------------------------------------------------------------------------
# Dependencias FastAPI
# ---------------------------------------------------------------------------
def usuario_atual(request: Request) -> dict[str, object]:
    """Exige sessao valida; devolve o usuario da sessao (nunca dados do cliente)."""
    token = request.cookies.get(config.COOKIE_SESSAO)
    usuario = usuario_da_sessao(token)
    if usuario is None:
        raise HTTPException(
            status_code=401, detail="Sessao invalida ou expirada. Entre novamente."
        )
    logs.definir_usuario(str(usuario.get("nome") or usuario.get("email") or ""))
    return {**usuario, "sessao_token": token}


def exigir_csrf(
    request: Request, usuario: dict[str, object] = Depends(usuario_atual)
) -> dict[str, object]:
    """Exige o header X-CSRF-Token coerente com a sessao em escritas."""
    enviado = request.headers.get("X-CSRF-Token")
    if not csrf_valido(
        usuario.get("sessao_token"), enviado  # type: ignore[arg-type]
    ):
        raise HTTPException(
            status_code=403, detail="Token CSRF ausente ou invalido."
        )
    return usuario


def exigir_senha_definitiva(
    usuario: dict[str, object] = Depends(usuario_atual),
) -> dict[str, object]:
    """Bloqueia o uso do sistema enquanto a senha for temporaria."""
    if usuario.get("senha_temporaria"):
        raise HTTPException(
            status_code=403,
            detail=(
                "Troca de senha obrigatoria antes de usar o sistema. "
                "Defina uma nova senha em 'Alterar minha senha'."
            ),
        )
    return usuario


def exigir_admin(
    usuario: dict[str, object] = Depends(usuario_atual),
) -> dict[str, object]:
    """Exige papel de administrador (checado no servidor)."""
    if usuario.get("papel") != usuarios.PAPEL_ADMIN:
        raise HTTPException(
            status_code=403, detail="Acesso restrito a administradores."
        )
    return usuario


def exigir_admin_csrf(
    usuario: dict[str, object] = Depends(exigir_csrf),
) -> dict[str, object]:
    """Administrador + CSRF valido (para escritas administrativas)."""
    if usuario.get("papel") != usuarios.PAPEL_ADMIN:
        raise HTTPException(
            status_code=403, detail="Acesso restrito a administradores."
        )
    return usuario
