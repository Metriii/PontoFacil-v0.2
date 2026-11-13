"""Contratos da camada de anonimizacao local.

Nada aqui deve conhecer regras de negocio dos modelos de folha. O objetivo e
transportar geometria, status e codigos de falha sem carregar dados pessoais em
representacoes textuais.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StatusAnonimizacao(str, Enum):
    APPROVED = "approved"
    BLOCKED = "blocked"
    ERROR = "error"
    PENDING_TREATMENT = "pending_treatment"


class CodigoFalha(str, Enum):
    HEADER_NOT_DETECTED = "HEADER_NOT_DETECTED"
    TABLE_NOT_DETECTED = "TABLE_NOT_DETECTED"
    TABLE_REGION_NOT_CONFIDENT = "TABLE_REGION_NOT_CONFIDENT"
    ANCHOR_NOT_FOUND = "ANCHOR_NOT_FOUND"
    ANCHOR_NOT_CONFIDENT = "ANCHOR_NOT_CONFIDENT"
    CROP_OUT_OF_BOUNDS = "CROP_OUT_OF_BOUNDS"
    POSSIBLE_ROW_LOSS = "POSSIBLE_ROW_LOSS"
    SENSITIVE_CONTENT_REMAINS = "SENSITIVE_CONTENT_REMAINS"
    OUTPUT_IMAGE_INVALID = "OUTPUT_IMAGE_INVALID"
    STRUCTURE_MODEL_ERROR = "STRUCTURE_MODEL_ERROR"
    ANONYMIZATION_TIMEOUT = "ANONYMIZATION_TIMEOUT"
    PDF_RASTERIZATION_ERROR = "PDF_RASTERIZATION_ERROR"
    UNSUPPORTED_MULTIPAGE_PDF = "UNSUPPORTED_MULTIPAGE_PDF"
    SENSITIVE_FIELD_CONFLICT = "SENSITIVE_FIELD_CONFLICT"
    HEADER_TEXT_UNAVAILABLE = "HEADER_TEXT_UNAVAILABLE"
    INVALID_INPUT = "INVALID_INPUT"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class Caixa:
    """Retangulo em coordenadas de pixel da imagem original."""

    x: float
    y: float
    largura: float
    altura: float
    texto: str | None = field(default=None, repr=False)
    tipo: str | None = None

    @property
    def x2(self) -> float:
        return self.x + self.largura

    @property
    def y2(self) -> float:
        return self.y + self.altura

    @property
    def area(self) -> float:
        return max(0.0, self.largura) * max(0.0, self.altura)

    def intersecta(self, outra: "Caixa") -> bool:
        return not (
            self.x2 <= outra.x
            or outra.x2 <= self.x
            or self.y2 <= outra.y
            or outra.y2 <= self.y
        )

    def contem(self, outra: "Caixa", tolerancia: float = 1.0) -> bool:
        return (
            outra.x >= self.x - tolerancia
            and outra.y >= self.y - tolerancia
            and outra.x2 <= self.x2 + tolerancia
            and outra.y2 <= self.y2 + tolerancia
        )

    def interseccao(self, outra: "Caixa") -> float:
        largura = min(self.x2, outra.x2) - max(self.x, outra.x)
        altura = min(self.y2, outra.y2) - max(self.y, outra.y)
        if largura <= 0 or altura <= 0:
            return 0.0
        return largura * altura

    def expandir(self, margem: float, largura_max: float, altura_max: float) -> "Caixa":
        x = max(0.0, self.x - margem)
        y = max(0.0, self.y - margem)
        x2 = min(largura_max, self.x2 + margem)
        y2 = min(altura_max, self.y2 + margem)
        return Caixa(x=x, y=y, largura=max(0.0, x2 - x), altura=max(0.0, y2 - y))


@dataclass(frozen=True)
class CelulaTabela:
    caixa: Caixa
    linha: int | None = None
    coluna: int | None = None


@dataclass(frozen=True)
class DeteccaoEstrutural:
    """Resultado do detector local (PP-StructureV3 ou substituto de teste)."""

    largura: int
    altura: int
    tabela: Caixa | None
    cabecalho: Caixa | None
    caixas_texto: tuple[Caixa, ...] = ()
    celulas: tuple[CelulaTabela, ...] = ()
    linhas_horizontais: tuple[float, ...] = ()
    confianca: float | None = None
    modelo_versao: str | None = None
    aviso_ancora: str | None = None
    # True quando o inicio do recorte foi decidido por uma ancora do modelo
    # (quinzena, faixa do mes, Saida, rotulos ou geometria). Tudo abaixo do
    # corte ja e a tabela de ponto.
    ancorada: bool = False


@dataclass(frozen=True)
class GeometriaTabela:
    x: int
    y: int
    largura: int
    altura: int

    @classmethod
    def de_caixa(cls, caixa: Caixa) -> "GeometriaTabela":
        return cls(
            x=int(round(caixa.x)),
            y=int(round(caixa.y)),
            largura=int(round(caixa.largura)),
            altura=int(round(caixa.altura)),
        )


@dataclass(frozen=True)
class AnaliseSensiveis:
    redigir: tuple[Caixa, ...] = ()
    suspeitos: tuple[Caixa, ...] = ()
    conflito: bool = False
    motivo: str | None = None
    # Diagnostico apenas para log: rotulos fixos (regra, tipo de conflito).
    # Nunca contem texto lido da imagem.
    detalhe: str | None = None


@dataclass(frozen=True)
class ResultadoValidacao:
    ok: bool
    codigo: CodigoFalha | None = None
    mensagem: str | None = None
    verificacao_sensiveis: str = "not_verified"
    verificacao_integridade: str = "not_verified"


@dataclass(frozen=True)
class ResultadoAnonimizacao:
    status: StatusAnonimizacao
    nome_original: str = field(default="", repr=False)
    artefato: object | None = field(default=None, repr=False)
    texto_cabecalho: str | None = field(default=None, repr=False)
    geometria: GeometriaTabela | None = None
    cabecalho_removido: bool = False
    metadados_removidos: bool = False
    verificacao_sensiveis: str = "not_verified"
    verificacao_integridade: str = "not_verified"
    codigo_falha: CodigoFalha | None = None
    mensagem: str | None = None
    modelo_versao: str | None = None
    id_opaco: str | None = None
    sha256_protegido: str | None = None
    versao: int = 1
    regioes_extras: tuple[tuple[float, float, float, float], ...] = ()
    regioes_protegidas: tuple[tuple[float, float, float, float], ...] = ()
    tratamento_necessario: bool = False
    aviso_ancora: str | None = None

    @property
    def aprovado(self) -> bool:
        return self.status is StatusAnonimizacao.APPROVED and self.artefato is not None

    @classmethod
    def bloqueado(
        cls,
        nome_original: str,
        codigo: CodigoFalha,
        mensagem: str | None = None,
        *,
        id_opaco: str | None = None,
        modelo_versao: str | None = None,
    ) -> "ResultadoAnonimizacao":
        return cls(
            status=StatusAnonimizacao.BLOCKED,
            nome_original=nome_original,
            codigo_falha=codigo,
            mensagem=mensagem,
            id_opaco=id_opaco,
            modelo_versao=modelo_versao,
        )

    @classmethod
    def erro(
        cls,
        nome_original: str,
        codigo: CodigoFalha,
        mensagem: str | None = None,
        *,
        id_opaco: str | None = None,
        modelo_versao: str | None = None,
    ) -> "ResultadoAnonimizacao":
        return cls(
            status=StatusAnonimizacao.ERROR,
            nome_original=nome_original,
            codigo_falha=codigo,
            mensagem=mensagem,
            id_opaco=id_opaco,
            modelo_versao=modelo_versao,
        )
