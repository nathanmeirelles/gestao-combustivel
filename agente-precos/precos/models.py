"""Modelos de dados. Dinheiro sempre em Decimal; cada campo importante carrega seu nível de evidência."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


class Evidencia(str, Enum):
    """Níveis exigidos no briefing (seção 12)."""

    CONFIRMADO_DIRETAMENTE = "confirmado_diretamente"      # lido na página da própria oferta/loja
    CONFIRMADO_INDEPENDENTE = "confirmado_independente"    # fonte oficial/independente (Receita, Registro.br...)
    ESTIMADO = "estimado"                                  # deduzido por heurística ou texto não estruturado
    NAO_VERIFICADO = "nao_verificado"
    DIVERGENTE = "divergente"                              # fontes/campos se contradizem

    @property
    def rotulo(self) -> str:
        return {
            "confirmado_diretamente": "confirmado diretamente",
            "confirmado_independente": "confirmado por fonte independente",
            "estimado": "estimado",
            "nao_verificado": "não verificado",
            "divergente": "informação divergente",
        }[self.value]

    @property
    def confirmado(self) -> bool:
        return self in (Evidencia.CONFIRMADO_DIRETAMENTE, Evidencia.CONFIRMADO_INDEPENDENTE)


class Status(str, Enum):
    VERIFICADA = "verificada"            # página aberta e preço lido na fonte
    NAO_CONFIRMADA = "nao_confirmada"    # só apareceu em resultado de busca / informado à mão
    BLOQUEADA = "bloqueada"              # site recusou acesso automatizado (não contornamos)
    INDISPONIVEL = "indisponivel"        # página existe, produto sem estoque
    ERRO = "erro"                        # falha de rede/parse


_DEC_FIELDS = {"preco_exibido", "preco_pix", "preco_cartao_vista", "frete_valor"}


@dataclass
class Parcelamento:
    n: int
    valor: Decimal
    sem_juros: bool | None = None  # None = o texto não diz

    @property
    def total(self) -> Decimal:
        return self.valor * self.n


@dataclass
class Oferta:
    url: str
    dominio: str
    status: Status
    coletado_em: datetime
    consulta_id: int | None = None
    id: int | None = None
    loja: str | None = None
    vendedor: str | None = None
    titulo: str | None = None
    condicao: str | None = None            # novo | usado | recondicionado | danificado
    marca: str | None = None
    sku: str | None = None
    gtin: str | None = None
    preco_exibido: Decimal | None = None   # preço principal da página (JSON-LD/meta) — normalmente cartão/lista
    preco_pix: Decimal | None = None       # melhor preço À VISTA com desconto; o meio de pagamento está em `meio_vista`
    meio_vista: str | None = None          # pix | boleto | cartão | não especificado
    titulos_alt: list[str] = field(default_factory=list)   # <title>/og:title/h1 (conferência de condição)
    preco_cartao_vista: Decimal | None = None
    parcelamentos: list[Parcelamento] = field(default_factory=list)
    frete_valor: Decimal | None = None
    frete_nota: str | None = None
    disponibilidade: str | None = None     # em_estoque | sem_estoque | sob_encomenda | limitada
    garantia: str | None = None
    cnpj_pagina: str | None = None
    fonte_dados: str | None = None         # json-ld | meta | texto | manual | snippet
    snippet_origem: str | None = None      # de onde veio um preço não confirmado
    evidencias: dict[str, str] = field(default_factory=dict)   # campo -> Evidencia.value
    alertas: list[str] = field(default_factory=list)
    sinais_risco: list[str] = field(default_factory=list)      # severidade alta => exclui da recomendação
    notas: list[str] = field(default_factory=list)
    identidade_produto: str | None = None  # confere | divergente | incerto
    hash_conteudo: str | None = None

    # ---- serialização (SQLite guarda o corpo em JSON) ----
    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, Decimal):
                v = str(v)
            elif isinstance(v, datetime):
                v = v.isoformat()
            elif isinstance(v, Enum):
                v = v.value
            elif f.name == "parcelamentos":
                v = [{"n": p.n, "valor": str(p.valor), "sem_juros": p.sem_juros} for p in v]
            out[f.name] = v
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Oferta":
        d = dict(d)
        d["status"] = Status(d["status"])
        d["coletado_em"] = datetime.fromisoformat(d["coletado_em"])
        for k in _DEC_FIELDS:
            if d.get(k) is not None:
                d[k] = Decimal(d[k])
        d["parcelamentos"] = [
            Parcelamento(p["n"], Decimal(p["valor"]), p.get("sem_juros")) for p in d.get("parcelamentos") or []
        ]
        validos = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in validos})

    def evid(self, campo: str) -> Evidencia:
        return Evidencia(self.evidencias.get(campo, Evidencia.NAO_VERIFICADO.value))

    def marcar(self, campo: str, nivel: Evidencia) -> None:
        self.evidencias[campo] = nivel.value

    @property
    def risco_alto(self) -> bool:
        return any(s.startswith("[ALTO]") for s in self.sinais_risco)


@dataclass
class Consulta:
    """O que o usuário pediu. Só o `texto` é obrigatório; o resto são premissas explícitas."""

    texto: str
    id: int | None = None
    criada_em: datetime | None = None
    modelo: str | None = None                # termos que o título PRECISA conter (padrão: derivado do texto)
    excluir: list[str] = field(default_factory=list)
    condicao: str = "novo"
    gtin: str | None = None
    pagamento: str | None = None             # vista | parcelado | None (mostra os dois)
    parcelas: int | None = None
    cep: str | None = None
    prioridade: str | None = None            # preco | parcelamento | oficial | entrega | garantia | seguranca
    aceita_marketplace: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d["criada_em"] = self.criada_em.isoformat() if self.criada_em else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Consulta":
        d = dict(d)
        if d.get("criada_em"):
            d["criada_em"] = datetime.fromisoformat(d["criada_em"])
        validos = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in validos})
