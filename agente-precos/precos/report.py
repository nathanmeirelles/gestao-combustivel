"""Relatório em Markdown no formato A–F do briefing. Só descreve o que está no banco: nada é preenchido por suposição."""

from __future__ import annotations

from urllib.parse import urlsplit

from .models import Evidencia, Oferta, Status
from .pipeline import Comparacao
from .pricing import economia
from .scoring import Pontuacao, custo_de
from .util import fmt_brl, fmt_dt

_TAG = {
    Evidencia.CONFIRMADO_DIRETAMENTE: "confirmado",
    Evidencia.CONFIRMADO_INDEPENDENTE: "confirmado (fonte independente)",
    Evidencia.ESTIMADO: "estimado",
    Evidencia.NAO_VERIFICADO: "não verificado",
    Evidencia.DIVERGENTE: "DIVERGENTE",
}
_ORDEM_CONF = {"baixa": 0, "média": 1, "alta": 2}
_DISP = {"em_estoque": "em estoque", "sem_estoque": "sem estoque", "sob_encomenda": "sob encomenda", "limitada": "estoque limitado", None: "não informada"}
_STATUS = {"nao_confirmada": "não confirmada", "bloqueada": "bloqueada para leitura automática", "indisponivel": "sem estoque", "erro": "não foi possível verificar", "verificada": "verificada"}


def _eh_global(s: str) -> bool:
    return s.startswith("[INFO] dispersão")


def _meio_txt(meio: str | None) -> str:
    return "meio de pagamento não especificado" if not meio or meio == "não especificado" else meio


def tag(ev: Evidencia) -> str:
    return _TAG[ev]


def nome_loja(o: Oferta, lojas: dict | None = None) -> str:
    loja = (lojas or {}).get(o.dominio)
    ident = loja.identidade if loja else None
    if ident and ident.get("compativel") and ident.get("razao_social"):
        return f"{ident['razao_social'].title().replace(' Ltda', ' LTDA')} ({o.dominio})"
    base = o.vendedor or o.loja or o.dominio
    return f"{base} ({o.dominio})" if o.vendedor and o.vendedor.lower() not in o.dominio else base


def _preco_vista_txt(p: Pontuacao) -> str:
    c = p.custo
    if c is None:
        return "—"
    return f"{fmt_brl(c.produto)} {c.rotulo} ({tag(c.evidencia)})"


def _parcelas_txt(p: Pontuacao, comp: Comparacao) -> str:
    from .pricing import custos_da_oferta
    pc = custos_da_oferta(p.oferta, comp.consulta.parcelas).get("parcelado")
    if pc is None:
        return "—"
    j = "sem juros" if pc.sem_juros else f"com juros (+{fmt_brl(pc.juros_total)}, ~{pc.taxa_am}% a.m.)"
    return f"{pc.n}x {fmt_brl(pc.valor_parcela)} {j} = {fmt_brl(pc.produto)}"


def _frete_txt(o: Oferta) -> str:
    if o.frete_valor is None:
        return "não calculado"
    return "grátis" if o.frete_valor == 0 else fmt_brl(o.frete_valor)


def _conf_txt(p: Pontuacao) -> str:
    c = p.confiab
    if c.rotulo == "risco":
        return "RISCO"
    if c.rotulo == "não verificada suficientemente":
        return "não verificada suficientemente"
    return f"{c.rotulo} ({c.pontos:g}/100)"


def _dominios_elegiveis(comp: Comparacao) -> set[str]:
    return {p.oferta.dominio for r in comp.ranking.values() for p in r if p.elegivel}


def confianca_geral(comp: Comparacao, vencedor: Pontuacao | None) -> tuple[str, list[str]]:
    razoes: list[str] = []
    if vencedor is None:
        return "baixa", ["nenhuma oferta verificada e elegível"]
    nivel = vencedor.confianca
    faltando = [k for k, v in vencedor.essenciais.items() if not v.confirmado]
    if faltando:
        razoes.append("essenciais não confirmados na oferta vencedora: " + ", ".join(f"{k} ({tag(vencedor.essenciais[k])})" for k in faltando))
    n = len(_dominios_elegiveis(comp))
    if n < 2:
        nivel = "baixa"
        razoes.append("apenas UMA loja verificada e elegível: não há base de comparação")
    elif n < 3 and _ORDEM_CONF[nivel] > 1:
        nivel = "média"
        razoes.append(f"apenas {n} lojas verificadas e elegíveis: base de comparação pequena")
    if vencedor.confiab.cobertura < 0.4:
        razoes.append("confiabilidade da loja pouco verificada")
    if vencedor.desatualizada:
        razoes.append("dados com mais de algumas horas: reverifique antes de comprar")
    if not razoes:
        razoes.append("preço, produto, vendedor e condição de pagamento da oferta vencedora foram confirmados e há ≥3 lojas comparáveis")
    return nivel, razoes


def melhor_elegivel(ranking: list[Pontuacao]) -> Pontuacao | None:
    return next((p for p in ranking if p.elegivel), None)


def relatorio(comp: Comparacao, topo: int = 5) -> str:
    c = comp.consulta
    L: list[str] = []
    add = L.append
    nome = lambda o: nome_loja(o, comp.lojas)       # noqa: E731
    vista = melhor_elegivel(comp.ranking.get("vista", []))
    parc = melhor_elegivel(comp.ranking.get("parcelado", []))
    principal = vista if c.pagamento != "parcelado" else parc
    nivel, razoes = confianca_geral(comp, principal)

    add(f"# Pesquisa de preço #{c.id} — {c.texto}")
    add(f"_Gerado em {fmt_dt(comp.geradas_em)}. Condição: **{c.condicao}**"
        f"{', pagamento: ' + c.pagamento if c.pagamento else ''}{f', até {c.parcelas}x' if c.parcelas else ''}"
        f"{', CEP ' + c.cep if c.cep else ', sem CEP (frete não calculado)'}"
        f"{', prioridade: ' + c.prioridade if c.prioridade else ''}"
        f"{'' if c.aceita_marketplace else ', sem marketplace'}._")
    add("")

    # ---------------- A. veredito
    add("## A. Veredito")
    if principal is None:
        add("**Não há oferta verificada suficiente para recomendar uma compra.** "
            "Nenhuma oferta passou simultaneamente por: página aberta com preço lido, produto conferido, sem sinal de risco alto e com estoque.")
    else:
        o = principal.oferta
        unica = len(_dominios_elegiveis(comp)) < 2
        if unica:
            add(f"**Única oferta verificada e elegível: {nome(o)}** — {o.titulo or c.texto}")
            add("> Isto **não** significa que seja a melhor compra do mercado: foi a única que consegui ler e validar. As demais ofertas encontradas não puderam ser verificadas (veja o fim do relatório).")
        else:
            add(f"**Melhor compra: {nome(o)}** — {o.titulo or c.texto}")
        if c.pagamento != "parcelado" and vista:
            cv = custo_de(vista)
            add(f"- **À vista:** {fmt_brl(cv.produto)} — {cv.rotulo}; {tag(cv.evidencia)}"
                f" · custo total {fmt_brl(cv.total)}{'' if cv.frete_incluido else ' **sem frete** (não calculado)'}")
        if c.pagamento != "vista" and parc:
            cp = custo_de(parc)
            j = "sem juros" if cp.sem_juros else f"**com juros**: +{fmt_brl(cp.juros_total)} (~{cp.taxa_am}% a.m.)"
            add(f"- **Parcelado:** {cp.n}x de {fmt_brl(cp.valor_parcela)} = {fmt_brl(cp.produto)}, {j} — {tag(cp.evidencia)} "
                f"(loja: {nome(parc.oferta)})")
        add(f"- **{'Pontuação' if unica else 'Por que venceu'}:** {principal.pontos:.1f}/100 pontos (conservador: critério não verificado vale 0). "
            + "; ".join(f"{k} {v.pontos:.1f}/{v.peso}" for k, v in principal.criterios.items()) + ".")
    add(f"- **Confiança da pesquisa: {nivel.upper()}.** " + "; ".join(razoes) + ".")
    if principal is not None and principal.confiab.cobertura < 0.4:
        add("- ⚠ **Confiabilidade da loja vencedora NÃO verificada suficientemente** (identidade, reputação e políticas não conferidas). "
            "Esta oferta é a melhor *em preço verificado*, não uma loja comprovadamente segura: confira CNPJ, reclamações e política de troca antes de pagar.")
    n_nv = len(comp.nao_verificadas)
    lim = ([f"{n_nv} oferta(s) encontrada(s) NÃO verificada(s) (bloqueio, sem estoque, sem preço legível ou falha) — o veredito não as considera"] if n_nv else [])
    lim += ["frete não calculado" if not c.cep else "frete depende de confirmação no carrinho (cálculo automático indisponível)",
           "nenhum cupom ou cashback foi verificado (não presumi nenhum desconto)"]
    if comp.avisos:
        lim.append("avisos: " + " | ".join(comp.avisos[:3]))
    add("- **Limitações:** " + "; ".join(lim) + ".")
    add("")

    # ---------------- B. melhores ofertas
    add("## B. Melhores ofertas")
    ranking_tab = comp.ranking.get("vista") or next(iter(comp.ranking.values()), [])
    elegiveis = [p for p in ranking_tab if p.elegivel][:topo]
    if not elegiveis:
        add("_Nenhuma oferta elegível._")
    else:
        add("| # | Loja/vendedor | À vista | Parcelamento | Frete | Total | Confiabilidade |")
        add("|---|---|---|---|---|---|---|")
        for i, p in enumerate(elegiveis, 1):
            tot = fmt_brl(p.custo.total) + ("" if p.custo.frete_incluido else " (sem frete)") if p.custo else "—"
            add(f"| {i} | {nome(p.oferta)} | {_preco_vista_txt(p)} | {_parcelas_txt(p, comp)} | {_frete_txt(p.oferta)} | {tot} | {_conf_txt(p)} |")
    gerais = sorted({s for p in ranking_tab for s in p.sinais if _eh_global(s)})
    for g in gerais:
        add(f"> {g}")
    add("")

    # ---------------- C. links
    add("## C. Links diretos")
    if not elegiveis:
        add("_Sem ofertas recomendáveis._")
    for i, p in enumerate(elegiveis, 1):
        o = p.oferta
        add(f"**{i}. {nome(o)}** — [{o.titulo or o.url}]({o.url})")
        add(f"- Preço: {fmt_brl(o.preco_exibido)} ({tag(o.evid('preco_exibido'))}); disponibilidade: {_DISP.get(o.disponibilidade, o.disponibilidade)}")
        add(f"- Consultado em {fmt_dt(o.coletado_em)}{' · **dados antigos, reverifique**' if p.desatualizada else ''}")
        obs = [a for a in o.alertas if "cache" not in a][:4] + [s for s in p.sinais if s.startswith(("[MÉDIO]", "[INFO]")) and not _eh_global(s)][:2]
        if obs:
            add("- Observações: " + " · ".join(obs))
    add("")

    # ---------------- D. pagamento
    add("## D. Comparação de pagamento")
    if vista:
        cvv = custo_de(vista)
        add(f"- **À vista — melhor:** {nome(vista.oferta)}: {fmt_brl(cvv.produto)} — {cvv.rotulo}; total {fmt_brl(cvv.total)}.")
        base = vista.oferta.preco_cartao_vista or vista.oferta.preco_exibido
        if vista.oferta.preco_pix and base:
            nom, pct = economia(base, vista.oferta.preco_pix)
            add(f"  - O preço à vista com desconto ({_meio_txt(vista.oferta.meio_vista)}) economiza {fmt_brl(nom)} ({pct}%) sobre o cartão nesta loja — lido do texto da página ({tag(vista.oferta.evid('preco_pix'))}); confirme no checkout.")
    else:
        add("- **À vista:** nenhuma oferta elegível com preço à vista.")
    if parc:
        cp = custo_de(parc)
        add(f"- **Parcelado — melhor:** {nome(parc.oferta)}: {cp.n}x de {fmt_brl(cp.valor_parcela)} = {fmt_brl(cp.produto)} "
            f"({'sem juros' if cp.sem_juros else 'COM juros: +' + fmt_brl(cp.juros_total)}).")
        if vista:
            nom, pct = economia(cp.produto, custo_de(vista).produto)
            add(f"  - Pagar à vista na melhor oferta custa {fmt_brl(nom)} ({pct}%) a menos que parcelar nesta.")
        com_juros = [p for p in comp.ranking.get("parcelado", []) if p.elegivel and p.custo and not p.custo.sem_juros]
        for p in com_juros[:3]:
            cj = custo_de(p)
            add(f"  - ⚠ {nome(p.oferta)}: parcelamento com juros embutidos de {fmt_brl(cj.juros_total)} (~{cj.taxa_am}% a.m.); não confunda parcela menor com melhor negócio.")
    else:
        add("- **Parcelado:** nenhuma oferta elegível com parcelamento legível.")
    add("- **Cupons e cashback:** não verificados nesta pesquisa; nenhum foi somado a qualquer total.")
    add("")

    # ---------------- E. confiabilidade
    add("## E. Análise de confiabilidade")
    vistos: set[str] = set()
    for p in ranking_tab:
        if p.oferta.dominio in vistos:
            continue
        vistos.add(p.oferta.dominio)
        cf = p.confiab
        add(f"**{p.oferta.dominio}** — {_conf_txt(p)}; cobertura verificada {int(cf.cobertura * 100)}%")
        for k in cf.componentes:
            fonte = f" · [fonte]({k.fonte})" if k.fonte else ""
            marca = "✔" if k.verificado else "✘ não verificado"
            add(f"- {k.nome}: {k.pontos:g}/{k.maximo} — {marca} — {k.nota}{fonte}")
        for s in p.sinais:
            if not _eh_global(s):
                add(f"- {s}")
    add("_As notas são heurísticas internas e transparentes; não são certificação. Reclame Aqui e grandes marketplaces bloqueiam consulta automatizada e não foram contornados._")
    add("")

    # ---------------- F. conclusão
    add("## F. Conclusão")
    if principal is None:
        add("Minha recomendação é **não comprar ainda**: reverifique as ofertas listadas abaixo no navegador ou amplie a pesquisa.")
    else:
        partes = [("Minha recomendação, condicional, é comprar na" if len(_dominios_elegiveis(comp)) < 2 else "Minha recomendação é comprar na") + f" **{nome(principal.oferta)}**"]
        if vista and c.pagamento != "parcelado":
            partes.append(f"por {fmt_brl(custo_de(vista).produto)} ({custo_de(vista).rotulo}).")
        add(" ".join(partes) + f" Confiança da pesquisa: {nivel}.")
        if principal.confiab.cobertura < 0.4:
            add("Atenção: a loja não pôde ser verificada o bastante; trate esta recomendação como *condicional* à sua conferência de CNPJ, reputação e política de troca.")
        if parc and c.pagamento != "vista":
            cp = custo_de(parc)
            add(f"Se preferir parcelar, a melhor condição verificada é {nome(parc.oferta)}: {cp.n}x de {fmt_brl(cp.valor_parcela)} (total {fmt_brl(cp.produto)}, {'sem juros' if cp.sem_juros else 'com juros'}).")
        add("**Antes de pagar:** abra o link, confira vendedor, estoque, frete para o seu CEP e as condições de pagamento no carrinho. Nenhuma compra foi feita.")
    add("")

    # ---------------- histórico
    add("## Momento da compra")
    if comp.classificacao:
        add(f"- **{comp.classificacao[0]}** — {comp.classificacao[1]}")
    else:
        add("- Dados insuficientes para classificar o preço.")
    add("- Não há garantia de queda de preço; sem urgência, o monitoramento (`precos monitor`) acompanha o preço-alvo com observações próprias.")
    add("")

    # ---------------- não elegíveis / não verificadas
    por_url: dict[str, list[Pontuacao]] = {}
    for r in comp.ranking.values():
        for p in r:
            por_url.setdefault(p.oferta.url, []).append(p)
    fora = [ps for ps in por_url.values() if not any(p.elegivel for p in ps)]
    if fora:
        add("## Verificadas, mas fora da recomendação")
        for ps in fora:
            o = ps[0].oferta
            motivos = list(dict.fromkeys(m for p in ps for m in p.motivos_exclusao))
            add(f"- [{o.titulo or o.url}]({o.url}) — {nome(o)}: " + "; ".join(motivos))
        add("")
    if comp.nao_verificadas:
        add("## Encontradas, porém fora do veredito")
        for o in comp.nao_verificadas:
            curto = o.dominio + urlsplit(o.url).path[:45]
            rot = _STATUS[o.status.value]
            if o.status == Status.INDISPONIVEL:
                det = f"preço na página {fmt_brl(o.preco_exibido)} ({tag(o.evid('preco_exibido'))}), porém **sem estoque**"
                det += "".join(f"; {n}" for n in o.notas if "CONTRADIT" in n or "indício" in n or "variante" in n)[:260]
            elif o.preco_exibido and o.snippet_origem:
                det = f"preço visto em busca: {fmt_brl(o.preco_exibido)} **(NÃO confirmado; origem: {o.snippet_origem})**; " + (o.alertas[-1] if o.alertas else "")
            else:
                det = o.alertas[-1] if o.alertas else ""
            add(f"- [{o.titulo or curto}]({o.url}) — {o.dominio}: **{rot}** — {det}")
        add("")

    add("---")
    add("_Níveis de evidência: confirmado (lido na página da oferta) · confirmado (fonte independente: Receita/Registro.br) · estimado (heurística sobre texto) · não verificado · divergente._")
    return "\n".join(L)
