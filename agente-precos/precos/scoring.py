"""Confiabilidade da loja, pontuação 0–100 das ofertas e regras de exclusão.

As notas aqui são HEURÍSTICAS INTERNAS e transparentes (cada ponto tem origem listada). Não são certificação.
Critério sem dado verificável vale ZERO pontos e reduz a confiança — nunca recebe nota "neutra" ou máxima.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from .models import Consulta, Evidencia, Oferta, Status
from .pricing import Custo, custos_da_oferta, melhor_vista
from .seller import Loja, eh_classificado, eh_marketplace
from .util import agora_brt

PESOS_PADRAO = {"custo": 45, "confiabilidade": 25, "pagamento": 15, "entrega": 10, "garantia": 5}
PESOS_PRIORIDADE = {
    "preco":        {"custo": 65, "confiabilidade": 15, "pagamento": 10, "entrega": 5, "garantia": 5},
    "parcelamento": {"custo": 30, "confiabilidade": 20, "pagamento": 35, "entrega": 10, "garantia": 5},
    "oficial":      {"custo": 30, "confiabilidade": 45, "pagamento": 10, "entrega": 5, "garantia": 10},
    "entrega":      {"custo": 35, "confiabilidade": 20, "pagamento": 10, "entrega": 30, "garantia": 5},
    "garantia":     {"custo": 35, "confiabilidade": 25, "pagamento": 10, "entrega": 5, "garantia": 25},
    "seguranca":    {"custo": 25, "confiabilidade": 50, "pagamento": 10, "entrega": 5, "garantia": 10},
}


def pesos_para(prioridade: str | None) -> dict[str, int]:
    return PESOS_PRIORIDADE.get(prioridade or "", PESOS_PADRAO)


# ------------------------------------------------------------------ confiabilidade
@dataclass
class Componente:
    nome: str
    pontos: float
    maximo: int
    verificado: bool
    evidencia: Evidencia
    nota: str
    fonte: str | None = None


@dataclass
class Confiabilidade:
    pontos: float
    componentes: list[Componente]
    cobertura: float                    # fração dos 100 pontos cujos componentes foram verificados
    rotulo: str                         # alta | média | baixa | não verificada suficientemente | risco
    sinais_risco: list[str]
    tier: str | None = None
    identidade_ok: bool = False         # CNPJ ativo e compatível com a loja


def avaliar_confiabilidade(o: Oferta, loja: Loja | None, agora: datetime | None = None) -> Confiabilidade:
    agora = agora or agora_brt()
    comps: list[Componente] = []
    sinais: list[str] = []
    ident = (loja.identidade if loja else None) or None
    identidade_ok = False

    # 1) identidade empresarial (25)
    if ident is None:
        nota = f"CNPJ {o.cnpj_pagina} declarado na página, mas não consultado na Receita" if o.cnpj_pagina else "CNPJ não localizado na página"
        comps.append(Componente("identidade (CNPJ)", 0, 25, False, Evidencia.NAO_VERIFICADO, nota))
    elif (ident.get("situacao") or "").upper() != "ATIVA":
        comps.append(Componente("identidade (CNPJ)", 0, 25, True, Evidencia.CONFIRMADO_INDEPENDENTE, f"CNPJ declarado consta como {ident.get('situacao')}", ident.get("fonte")))
        sinais.append(f"[ALTO] CNPJ declarado consta como {ident.get('situacao')} na Receita")
    elif ident.get("compativel"):
        identidade_ok = True
        comps.append(Componente("identidade (CNPJ)", 25, 25, True, Evidencia.CONFIRMADO_INDEPENDENTE, f"CNPJ ativo ({ident.get('razao_social')}); nome compatível com a loja/domínio", ident.get("fonte")))
    else:
        comps.append(Componente("identidade (CNPJ)", 15, 25, True, Evidencia.ESTIMADO, f"CNPJ ativo ({ident.get('razao_social')}), mas vínculo com a loja NÃO comprovado", ident.get("fonte")))

    # 2) tempo de operação (10) — só conta se o CNPJ é compatível com a loja
    anos = None
    if ident and ident.get("abertura"):
        try:
            anos = (agora.date() - datetime.fromisoformat(ident["abertura"]).date()).days / 365.25
        except ValueError:
            anos = None
    if anos is not None and identidade_ok:
        pts_tempo = 10 if anos >= 3 else 5 if anos >= 1 else 0
        comps.append(Componente("tempo de operação", pts_tempo, 10, True, Evidencia.CONFIRMADO_INDEPENDENTE, f"CNPJ aberto há {anos:.1f} anos", (ident or {}).get("fonte")))
        if anos < 1:
            sinais.append("[MÉDIO] empresa aberta há menos de 1 ano")
    else:
        comps.append(Componente("tempo de operação", 0, 10, False, Evidencia.NAO_VERIFICADO, "depende de CNPJ ativo e compatível com a loja"))

    # 3) idade do domínio (5)
    rdap = loja.rdap if loja else None
    if rdap and rdap.get("idade_dias") is not None:
        d = rdap["idade_dias"]
        pts = 5 if d >= 730 else 2 if d >= 180 else 0
        comps.append(Componente("idade do domínio", pts, 5, True, Evidencia.CONFIRMADO_INDEPENDENTE, f"domínio registrado em {rdap.get('registrado_em')} ({d // 365} anos)" if d >= 365 else f"domínio registrado há {d} dias", rdap.get("fonte")))
        if d < 180:
            sinais.append(f"[MÉDIO] domínio registrado há apenas {d} dias")
    else:
        comps.append(Componente("idade do domínio", 0, 5, False, Evidencia.NAO_VERIFICADO, "só verificável para domínios .br"))

    # 4) canal de venda (20) — exige evidência registrada
    base = {"oficial": 20, "autorizado": 20, "varejista": 15, "marketplace_verificavel": 10, "insuficiente": 0, "risco": 0}
    tier = loja.tier if loja and loja.tier in base else None      # tier desconhecido (cadastro editado à mão) = não registrado
    if loja and tier:
        comps.append(Componente("canal de venda", base[tier], 20, True, Evidencia.CONFIRMADO_INDEPENDENTE if loja.tier_fonte else Evidencia.ESTIMADO, f"classificação: {tier.replace('_', ' ')}", loja.tier_fonte))
        if tier == "risco":
            sinais.append("[ALTO] loja classificada como de risco")
    else:
        extra = " (marketplace: o vendedor real precisa ser identificado)" if eh_marketplace(o.dominio) else ""
        comps.append(Componente("canal de venda", 0, 20, False, Evidencia.NAO_VERIFICADO, "classificação da loja não registrada" + extra))

    # 5) reputação independente (30)
    if loja and loja.reputacao_nota is not None and loja.reputacao_escala and loja.reputacao_escala > 0 and loja.reputacao_fonte:
        pts_rep = min(max(loja.reputacao_nota, 0.0), loja.reputacao_escala) / loja.reputacao_escala * 30
        nota = f"nota {loja.reputacao_nota:g}/{loja.reputacao_escala:g} em {loja.reputacao_data}"
        try:
            if agora.date() - datetime.fromisoformat(str(loja.reputacao_data)).date() > timedelta(days=365):
                pts_rep *= 0.5
                nota += " (dado com mais de 12 meses: pontuação reduzida à metade)"
        except ValueError:
            nota += " (data ilegível: tratada sem desconto por antiguidade)"
        comps.append(Componente("reputação independente", round(pts_rep, 1), 30, True, Evidencia.CONFIRMADO_INDEPENDENTE, nota, loja.reputacao_fonte))
    else:
        comps.append(Componente("reputação independente", 0, 30, False, Evidencia.NAO_VERIFICADO, "nenhuma fonte independente de reputação registrada"))

    # 6) políticas de troca/garantia/atendimento (10)
    if loja and loja.politicas_ok is not None and loja.politicas_fonte:
        comps.append(Componente("políticas e atendimento", 10 if loja.politicas_ok else 0, 10, True, Evidencia.CONFIRMADO_DIRETAMENTE, "políticas conferidas" if loja.politicas_ok else "políticas ausentes ou insuficientes", loja.politicas_fonte))
    else:
        comps.append(Componente("políticas e atendimento", 0, 10, False, Evidencia.NAO_VERIFICADO, "não conferidas"))

    if eh_classificado(o.dominio):
        sinais.append("[INFO] classificado entre pessoas: sem loja por trás; proteção ao comprador limitada")

    pontos = round(sum(c.pontos for c in comps), 1)
    cobertura = sum(c.maximo for c in comps if c.verificado) / 100
    if any(s.startswith("[ALTO]") for s in sinais):
        rotulo = "risco"
    elif cobertura < 0.4:
        rotulo = "não verificada suficientemente"
    else:
        rotulo = "alta" if pontos >= 75 else "média" if pontos >= 50 else "baixa"
    return Confiabilidade(pontos, comps, cobertura, rotulo, sinais, tier, identidade_ok)


# ------------------------------------------------------------------ sinais de preço entre ofertas
def sinais_de_preco(ofertas: list[Oferta]) -> dict[str, list[str]]:
    """Compara com a mediana das ofertas verificadas e compatíveis. Exige ≥3 domínios distintos para opinar."""
    ref = [(o, o.preco_ref) for o in ofertas if o.status == Status.VERIFICADA and o.preco_ref and o.identidade_produto != "divergente"]
    if len({o.dominio for o, _ in ref}) < 3:
        return {}
    mediana = statistics.median(preco for _, preco in ref)
    out: dict[str, list[str]] = {}
    for o, preco in ref:
        ratio = preco / mediana
        if ratio < Decimal("0.5"):
            out.setdefault(o.url, []).append(f"[ALTO] preço {int((1 - ratio) * 100)}% abaixo da mediana das ofertas verificadas: possível golpe, anúncio enganoso ou produto diferente")
        elif ratio > 2:
            out.setdefault(o.url, []).append(f"[INFO] preço {ratio:.1f}× a mediana das ofertas verificadas")
    mx, mn = max(p for _, p in ref), min(p for _, p in ref)
    if mn and mx / mn > Decimal("1.8"):
        out.setdefault("*", []).append(f"[INFO] dispersão alta entre ofertas verificadas (de {mn} a {mx}): confira variações de modelo, condição ou origem")
    return out


# ------------------------------------------------------------------ pontuação
@dataclass
class Criterio:
    nota: float | None            # 0–100, ou None quando não verificado
    peso: int
    evidencia: Evidencia
    texto: str

    @property
    def pontos(self) -> float:
        return 0.0 if self.nota is None else self.nota * self.peso / 100


@dataclass
class Pontuacao:
    oferta: Oferta
    modo: str
    custo: Custo | None
    confiab: Confiabilidade
    criterios: dict[str, Criterio]
    pontos: float
    cobertura: float
    confianca: str
    elegivel: bool
    motivos_exclusao: list[str] = field(default_factory=list)
    essenciais: dict[str, Evidencia] = field(default_factory=dict)
    desatualizada: bool = False
    sinais: list[str] = field(default_factory=list)     # riscos e avisos (loja + preço + oferta)


def custo_de(p: Pontuacao) -> Custo:
    """Custo de uma pontuação elegível. Elegível implica custo calculável; se isso for violado, falha alto e claro."""
    if p.custo is None:
        raise RuntimeError(f"invariante violada: oferta sem custo calculável ({p.oferta.url})")
    return p.custo


def _nota_pagamento(o: Oferta, c: Consulta, modo: str, custos: dict[str, Custo]) -> tuple[float | None, Evidencia, str]:
    if modo == "vista":
        cart, pix = custos.get("cartao_vista"), custos.get("pix")
        if cart is None and pix is None:
            return None, Evidencia.NAO_VERIFICADO, "sem preço à vista legível"
        if pix and cart and cart.produto > 0:
            desc = float((cart.produto - pix.produto) / cart.produto * 100)
            return round(70 + min(30.0, max(desc, 0) * 3), 1), pix.evidencia, f"Pix com {desc:.1f}% de desconto sobre o cartão"
        unico = pix or cart
        if unico is None:
            return None, Evidencia.NAO_VERIFICADO, "sem preço à vista legível"
        return 70.0, unico.evidencia, "apenas um preço à vista encontrado"
    p = custos.get("parcelado")
    if p is None:
        return None, Evidencia.NAO_VERIFICADO, "parcelamento não encontrado"
    pedido, n = c.parcelas, p.n or 0
    if p.sem_juros:
        if pedido and n < pedido:
            return round(60 + 40 * n / pedido, 1), p.evidencia, f"sem juros, mas só em {n}x (pedido: {pedido}x)"
        return 100.0, p.evidencia, f"{n}x sem juros"
    base = o.preco_cartao_vista or o.preco_exibido
    pct = float((p.juros_total or Decimal(0)) / base * 100) if base else 100.0
    return round(max(0.0, 100 - 2 * pct), 1), p.evidencia, f"{n}x com juros embutidos de {pct:.1f}% (~{p.taxa_am}% a.m.)"


def _nota_entrega(o: Oferta, custos: dict[str, Custo]) -> tuple[float | None, Evidencia, str]:
    if o.frete_valor is None:
        return None, Evidencia.NAO_VERIFICADO, "frete não calculado (informe o CEP e confirme no carrinho)"
    base = o.preco_ref or Decimal("0")
    if o.frete_valor == 0:
        return 100.0, o.evid("frete_valor"), "frete grátis"
    pct = float(o.frete_valor / base * 100) if base else 100.0
    return round(max(0.0, 100 - 4 * pct), 1), o.evid("frete_valor"), f"frete de {o.frete_valor} ({pct:.1f}% do preço)"


def _nota_garantia(o: Oferta) -> tuple[float | None, Evidencia, str]:
    if not o.garantia:
        return None, Evidencia.NAO_VERIFICADO, "garantia não localizada na página"
    m = re.search(r"(\d{1,2})\s*(meses|mês|anos|ano)", o.garantia, re.I)
    if m:
        meses = int(m.group(1)) * (12 if m.group(2).lower().startswith("an") else 1)
        return (100.0 if meses >= 12 else 60.0), o.evid("garantia"), f"garantia de {meses} meses (lida do texto)"
    return 40.0, o.evid("garantia"), "garantia mencionada sem prazo legível"


def pontuar(ofertas: list[Oferta], c: Consulta, lojas: dict[str, Loja], modo: str, desatualizada_apos_h: float = 6.0, agora: datetime | None = None) -> list[Pontuacao]:
    agora = agora or agora_brt()
    pesos = pesos_para(c.prioridade)
    sinais_cj = sinais_de_preco(ofertas)
    verificadas = [o for o in ofertas if o.status == Status.VERIFICADA]
    prontos = []
    for o in verificadas:
        custos = custos_da_oferta(o, c.parcelas)
        custo = melhor_vista(custos) if modo == "vista" else custos.get("parcelado")
        conf = avaliar_confiabilidade(o, lojas.get(o.dominio), agora)
        extra = list(sinais_cj.get(o.url, [])) + list(sinais_cj.get("*", []))
        riscos = list(dict.fromkeys(o.sinais_risco + conf.sinais_risco + [s for s in extra if not s.startswith("[INFO]")]))
        infos = [s for s in conf.sinais_risco + extra if s.startswith("[INFO]")]
        idade_h = (agora - o.coletado_em).total_seconds() / 3600
        desat = idade_h > desatualizada_apos_h

        motivos = []
        if o.identidade_produto == "divergente":
            motivos.append("produto não confere com o pedido (modelo/variante/condição)")
        if any(s.startswith("[ALTO]") for s in riscos):
            motivos.append("sinal de risco alto: " + "; ".join(s for s in riscos if s.startswith("[ALTO]")))
        if o.disponibilidade == "sem_estoque":
            motivos.append("sem estoque na página")
        if not c.aceita_marketplace and eh_marketplace(o.dominio):
            motivos.append("marketplace não aceito nas premissas")
        if c.prioridade == "oficial" and conf.tier not in ("oficial", "autorizado"):
            motivos.append("prioridade 'oficial': loja não comprovada como oficial/autorizada")
        if custo is None:
            motivos.append("sem custo calculável para a forma de pagamento escolhida")

        nota_pg, ev_pg, txt_pg = _nota_pagamento(o, c, modo, custos)
        nota_en, ev_en, txt_en = _nota_entrega(o, custos)
        nota_ga, ev_ga, txt_ga = _nota_garantia(o)
        prontos.append((o, custo, conf, riscos + infos, motivos, desat, (nota_pg, ev_pg, txt_pg), (nota_en, ev_en, txt_en), (nota_ga, ev_ga, txt_ga)))

    elegiveis_custo = [p[1].total for p in prontos if p[1] is not None and not p[4]]
    menor = min(elegiveis_custo) if elegiveis_custo else None
    saida: list[Pontuacao] = []
    for o, custo, conf, riscos, motivos, desat, pg, en, ga in prontos:
        if custo is not None and menor:
            nota_custo = round(float(menor / custo.total) * 100, 1)
            crit_custo = Criterio(nota_custo, pesos["custo"], custo.evidencia, f"total {custo.total} ({custo.modalidade}{'' if custo.frete_incluido else ', sem frete'})")
        else:
            crit_custo = Criterio(None, pesos["custo"], Evidencia.NAO_VERIFICADO, "custo não calculável")
        crit = {
            "custo": crit_custo,
            "confiabilidade": Criterio(conf.pontos, pesos["confiabilidade"], Evidencia.CONFIRMADO_INDEPENDENTE if conf.cobertura >= 0.4 else Evidencia.NAO_VERIFICADO, f"{conf.pontos:g}/100 pts, {int(conf.cobertura * 100)}% verificado"),
            "pagamento": Criterio(pg[0], pesos["pagamento"], pg[1], pg[2]),
            "entrega": Criterio(en[0], pesos["entrega"], en[1], en[2]),
            "garantia": Criterio(ga[0], pesos["garantia"], ga[1], ga[2]),
        }
        pontos = round(sum(k.pontos for k in crit.values()), 1)
        cobertura = sum(
            k.peso * (conf.cobertura if nome == "confiabilidade" else 1)
            for nome, k in crit.items() if k.nota is not None and k.evidencia.confirmado
        ) / 100

        ess = {
            "preço": o.evid("preco_exibido"),
            "produto": o.evid("produto"),
            "vendedor": o.evid("vendedor") if o.evid("vendedor").confirmado else (Evidencia.CONFIRMADO_INDEPENDENTE if conf.identidade_ok and not eh_marketplace(o.dominio) else Evidencia.NAO_VERIFICADO),
            "pagamento": custo.evidencia if custo else Evidencia.NAO_VERIFICADO,
        }
        loja_sem_verificacao = conf.cobertura < 0.4        # nada (ou quase nada) conferido sobre quem vende
        if Evidencia.DIVERGENTE in ess.values() or loja_sem_verificacao:
            confianca = "baixa"
        elif all(v.confirmado for v in ess.values()) and not desat:
            confianca = "alta"
        elif ess["preço"].confirmado and ess["produto"] != Evidencia.NAO_VERIFICADO:
            confianca = "média"
        else:
            confianca = "baixa"
        if desat and confianca == "alta":
            confianca = "média"
        saida.append(Pontuacao(o, modo, custo, conf, crit, pontos, cobertura, confianca, not motivos, motivos, ess, desat, riscos))
    saida.sort(key=lambda p: (not p.elegivel, -p.pontos, p.custo.total if p.custo else Decimal("Infinity")))
    return saida
