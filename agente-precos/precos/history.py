"""Histórico de preços PRÓPRIO (observações que este agente coletou). Não inventa histórico de terceiros."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .matching import normalizar
from .models import Consulta
from .store import Store

MIN_OBSERVACOES = 8
MIN_DIAS = 14


@dataclass
class Serie:
    pontos: list[tuple[datetime, str, Decimal]]   # (quando, domínio, preço)

    @property
    def n(self) -> int:
        return len(self.pontos)

    @property
    def dias(self) -> float:
        return (self.pontos[-1][0] - self.pontos[0][0]).total_seconds() / 86400 if self.n >= 2 else 0.0

    @property
    def inicio(self):
        return self.pontos[0][0] if self.pontos else None

    @property
    def fim(self):
        return self.pontos[-1][0] if self.pontos else None


def chave_produto(c: Consulta) -> str:
    return normalizar(c.modelo or c.texto).strip()


def serie_do_produto(store: Store, c: Consulta) -> Serie:
    chave = chave_produto(c)
    pts = []
    for cid, o in store.todas_verificadas():
        outra = store.consulta(cid)
        if outra is None or chave_produto(outra) != chave or o.identidade_produto == "divergente" or o.preco_ref is None:
            continue
        if outra.condicao != c.condicao:
            continue
        pts.append((o.coletado_em, o.dominio, o.preco_ref))
    return Serie(sorted(pts, key=lambda p: p[0]))


def classificar(preco_atual: Decimal, s: Serie) -> tuple[str, str]:
    """(rótulo, fundamento). Só classifica com observações suficientes — senão diz que não dá."""
    if s.n < MIN_OBSERVACOES or s.dias < MIN_DIAS:
        return "Dados insuficientes para classificar", (
            f"{s.n} observações em {s.dias:.0f} dias; são necessárias ao menos {MIN_OBSERVACOES} em {MIN_DIAS}+ dias. "
            "Wayback Machine e comparadores com histórico não estão acessíveis neste ambiente, então o histórico cresce a cada verificação."
        )
    precos = sorted(p for _, _, p in s.pontos)
    q = statistics.quantiles([float(p) for p in precos], n=4)
    minimo, med = precos[0], statistics.median(precos)
    base = f"{s.n} observações próprias entre {s.inicio:%d/%m/%Y} e {s.fim:%d/%m/%Y}; mínimo {minimo}, mediana {med}"
    if preco_atual <= minimo * Decimal("1.02"):
        return "Excelente oportunidade", base
    if float(preco_atual) <= q[0]:
        return "Bom preço", base
    if float(preco_atual) <= q[2]:
        return "Preço dentro da faixa normal", base
    return "Preço elevado", base
