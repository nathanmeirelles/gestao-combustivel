"""Cálculo de custo real. Tudo em Decimal; nada é presumido (frete só entra se foi informado/lido)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .models import Evidencia, Oferta, Parcelamento
from .util import money

_ORDEM = [Evidencia.DIVERGENTE, Evidencia.NAO_VERIFICADO, Evidencia.ESTIMADO, Evidencia.CONFIRMADO_INDEPENDENTE, Evidencia.CONFIRMADO_DIRETAMENTE]


def pior(*niveis: Evidencia) -> Evidencia:
    """Elo mais fraco: um total só é tão confiável quanto o menos confiável dos seus componentes."""
    return min(niveis, key=_ORDEM.index) if niveis else Evidencia.NAO_VERIFICADO


@dataclass
class Custo:
    modalidade: str                      # pix | cartao_vista | parcelado
    produto: Decimal                     # preço à vista OU soma das parcelas
    frete: Decimal | None
    taxas: Decimal | None
    total: Decimal
    frete_incluido: bool
    evidencia: Evidencia
    n: int | None = None
    valor_parcela: Decimal | None = None
    sem_juros: bool | None = None        # calculado: n×parcela ≈ preço no cartão
    juros_total: Decimal | None = None   # n×parcela − preço no cartão à vista
    taxa_am: Decimal | None = None       # % ao mês implícita (estimativa a partir dos valores lidos)
    notas: list[str] = field(default_factory=list)
    rotulo: str = ""                     # texto para o usuário (ex.: "Pix", "à vista (meio não especificado)")


_ROTULO_MEIO = {"pix": "Pix", "boleto": "boleto", "cartão": "cartão à vista", "não especificado": "à vista, meio de pagamento não especificado"}


def custo_vista(preco: Decimal, frete: Decimal | None = None, taxas: Decimal | None = None) -> Decimal:
    """Custo à vista = preço efetivamente pago + frete + taxas obrigatórias."""
    return money(preco + (frete or 0) + (taxas or 0))


def custo_parcelado(n: int, valor_parcela: Decimal, frete: Decimal | None = None, taxas: Decimal | None = None) -> Decimal:
    """Custo parcelado = soma das parcelas + frete + taxas obrigatórias."""
    return money(valor_parcela * n + (frete or 0) + (taxas or 0))


def economia(referencia: Decimal, alternativa: Decimal) -> tuple[Decimal, Decimal]:
    """(economia nominal, % de economia) de `alternativa` em relação a `referencia`."""
    nominal = money(referencia - alternativa)
    pct = (nominal / referencia * 100).quantize(Decimal("0.1")) if referencia else Decimal("0.0")
    return nominal, pct


def taxa_mensal_implicita(preco_vista: Decimal, n: int, parcela: Decimal) -> Decimal | None:
    """Taxa mensal (%) que iguala o preço à vista ao fluxo de n parcelas postecipadas. None se não calculável."""
    if n < 1 or parcela <= 0 or preco_vista <= 0:
        return None
    if parcela * n <= preco_vista * Decimal("1.0005"):
        return Decimal("0.00")
    pv, p = float(preco_vista), float(parcela)
    lo, hi = 0.0, 1.0
    for _ in range(200):
        i = (lo + hi) / 2
        valor_presente = p * (1 - (1 + i) ** -n) / i if i > 1e-12 else p * n
        lo, hi = (i, hi) if valor_presente > pv else (lo, i)
    return Decimal(str(round((lo + hi) / 2 * 100, 2)))


def escolher_parcelamento(parcelas: list[Parcelamento], max_parcelas: int | None) -> Parcelamento | None:
    """Menor total dentro do limite pedido; no empate, mais parcelas (parcela menor)."""
    cand = [p for p in parcelas if max_parcelas is None or p.n <= max_parcelas]
    return min(cand, key=lambda p: (money(p.total), -p.n)) if cand else None


def custos_da_oferta(o: Oferta, max_parcelas: int | None = None) -> dict[str, Custo]:
    """Custos por forma de pagamento, apenas com o que foi realmente lido/informado."""
    saida: dict[str, Custo] = {}
    frete = o.frete_valor
    ev_frete = o.evid("frete_valor") if frete is not None else None
    nota_frete = [] if frete is not None else ["frete não calculado: total é do produto, sem entrega"]

    base_cartao = o.preco_cartao_vista or o.preco_exibido
    ev_cartao = o.evid("preco_cartao_vista") if o.preco_cartao_vista else o.evid("preco_exibido")

    if o.preco_pix is not None:
        ev = pior(o.evid("preco_pix"), *( [ev_frete] if ev_frete else []))
        saida["pix"] = Custo("pix", o.preco_pix, frete, None, custo_vista(o.preco_pix, frete), frete is not None, ev, notas=list(nota_frete),
                             rotulo=_ROTULO_MEIO.get(o.meio_vista or "não especificado", "à vista"))
    if base_cartao is not None:
        ev = pior(ev_cartao, *([ev_frete] if ev_frete else []))
        saida["cartao_vista"] = Custo("cartao_vista", base_cartao, frete, None, custo_vista(base_cartao, frete), frete is not None, ev, notas=list(nota_frete), rotulo="cartão à vista")
        pc = escolher_parcelamento(o.parcelamentos, max_parcelas)
        if pc is not None:
            soma = money(pc.total)
            juros = money(soma - base_cartao)
            sem = soma <= base_cartao * Decimal("1.0005")
            ev = pior(o.evid("parcelamentos"), ev_cartao, *([ev_frete] if ev_frete else []))
            notas = list(nota_frete)
            if pc.sem_juros is True and not sem:
                notas.append("a página diz 'sem juros', mas n×parcela difere do preço à vista: divergência")
                ev = Evidencia.DIVERGENTE
            saida["parcelado"] = Custo(
                "parcelado", soma, frete, None, custo_parcelado(pc.n, pc.valor, frete), frete is not None, ev,
                n=pc.n, valor_parcela=pc.valor, sem_juros=sem, juros_total=max(juros, Decimal("0.00")),
                taxa_am=taxa_mensal_implicita(base_cartao, pc.n, pc.valor), notas=notas, rotulo="cartão parcelado",
            )
    return saida


def melhor_vista(custos: dict[str, Custo]) -> Custo | None:
    cand = [c for k, c in custos.items() if k in ("pix", "cartao_vista")]
    return min(cand, key=lambda c: c.total) if cand else None
