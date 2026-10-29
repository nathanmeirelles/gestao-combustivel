"""Extrai dados de oferta de uma página de produto: JSON-LD > meta tags > texto visível (só como estimativa)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal

from bs4 import BeautifulSoup

from .models import Parcelamento
from .util import achar_cnpjs, dominio, gtin_valido, normalizar_url, parse_brl_texto, parse_decimal_maquina

_IGNORAR_CHAVES = {"isrelatedto", "issimilarto", "isaccessoryorsparepartfor", "isconsumablefor", "review", "aggregaterating"}
_DISP = {
    "instock": "em_estoque", "onlineonly": "em_estoque", "instoreonly": "em_estoque",
    "outofstock": "sem_estoque", "soldout": "sem_estoque", "discontinued": "sem_estoque",
    "preorder": "sob_encomenda", "backorder": "sob_encomenda", "presale": "sob_encomenda",
    "limitedavailability": "limitada",
}
_COND = {"newcondition": "novo", "usedcondition": "usado", "refurbishedcondition": "recondicionado", "damagedcondition": "danificado"}

_RE_PARC = re.compile(r"(\d{1,2})\s*[xX]\s*(?:de\s*)?R\$\s*([\d\.]+,\d{2})(?=(?P<resto>.{0,45}))", re.I)
_RE_PIX_A = re.compile(r"R\$\s*([\d\.]+,\d{2})\s*[\(\-–]?\s*(?:\d{1,2}\s*%\s*(?:off|de desconto)?\s*\)?\s*)?(?:(?:à|a)\s+vista\s+)?(?:(?:com|de)\s+desconto\s+)?(?:(?:no|via|com|pelo|no\s+pagamento\s+via)\s+)?pix", re.I)
# "sem juros" escrito de várias formas; qualquer outra menção a juros = cobrança de juros
_RE_SEM_JUROS = re.compile(r"\b(?:sem|s\s*/)\s*juros?\b|sem\s+acr[eé]scimos?|juros\s*0\s*%|0\s*%\s*(?:de\s*)?juros", re.I)
_RE_COM_JUROS = re.compile(r"\b(?:com|c\s*/)\s*juros?\b|\bjuros\b", re.I)
_RE_PIX_B = re.compile(r"pix[^R\d]{0,25}R\$\s*([\d\.]+,\d{2})", re.I)
_RE_AVISTA_A = re.compile(r"R\$\s*([\d\.]+,\d{2})\s*[\(\-–:]?\s*(?:à|a)\s+vista", re.I)
_RE_AVISTA_B = re.compile(r"(?:à|a)\s+vista[^R\d]{0,20}R\$\s*([\d\.]+,\d{2})", re.I)
_RE_LISTA_PARC = re.compile(r"R\$\s*([\d\.]+,\d{2})\s*(?:em\s+|at[eé]\s+|ou\s+)*(\d{1,2})\s*[xX]\s*(?:de\s*)?R\$\s*([\d\.]+,\d{2})", re.I)
# valor sujeito a condição de quantidade/revenda não é o preço de varejo do item. Só contam sinais de CONDIÇÃO: a palavra "atacado"
# sozinha aparece em abas ("VAREJO | ATACADO") e "kit"/"combo" podem ser o próprio item vendido.
_RE_EXCLUI_ANTES = re.compile(r"revendedor|revenda", re.I)
_RE_EXCLUI_DEPOIS = re.compile(r"m[ií]nimo|unidades|a partir de \d", re.I)
_JANELA = 400      # caracteres antes/depois do preço principal onde Pix e parcelas são aceitos
_RE_GARANTIA = re.compile(r"garantia[^.\n]{0,60}?(\d{1,2})\s*(meses|mês|anos|ano|dias)|garantia\s+(?:do\s+fabricante|nacional|de\s+fábrica)", re.I)


@dataclass
class Extracao:
    titulo: str | None = None
    marca: str | None = None
    sku: str | None = None
    gtin: str | None = None
    condicao: str | None = None
    vendedor: str | None = None
    loja: str | None = None
    preco: Decimal | None = None
    fonte_preco: str | None = None          # json-ld | meta
    listagem: bool = False                  # várias fichas na página: não há um preço único
    preco_corroborado: bool = False         # o mesmo valor aparece no texto visível?
    disponibilidade: str | None = None
    preco_pix: Decimal | None = None
    meio_vista: str | None = None
    preco_lista: Decimal | None = None      # preço de lista/cartão quando o preço estruturado é, na verdade, o do Pix
    titulos: list[str] = field(default_factory=list)
    parcelamentos: list[Parcelamento] = field(default_factory=list)
    cnpjs: list[str] = field(default_factory=list)
    garantia: str | None = None
    avisos: list[str] = field(default_factory=list)


def _tipos(no: dict) -> set[str]:
    t = no.get("@type")
    t = t if isinstance(t, list) else [t]
    return {str(x).split("/")[-1].lower() for x in t if x}


def _percorrer(obj, saida: list[dict], profundidade: int = 0) -> None:
    if profundidade > 8:
        return
    if isinstance(obj, list):
        for x in obj:
            _percorrer(x, saida, profundidade + 1)
    elif isinstance(obj, dict):
        saida.append(obj)
        for k, v in obj.items():
            if k.lower() in _IGNORAR_CHAVES:
                continue
            _percorrer(v, saida, profundidade + 1)


def _nome(v) -> str | None:
    if isinstance(v, dict):
        v = v.get("name")
    return str(v).strip() if v else None


def _ofertas(produto: dict) -> list[dict]:
    brutas = produto.get("offers")
    brutas = brutas if isinstance(brutas, list) else [brutas] if brutas else []
    saida: list[dict] = []
    for o in brutas:
        if not isinstance(o, dict):
            continue
        if "offers" in o:  # AggregateOffer com ofertas aninhadas
            saida.extend(_ofertas({"offers": o["offers"]}))
        saida.append(o)
    return saida


def _preco_da_oferta(o: dict) -> tuple[Decimal | None, str | None]:
    spec = o.get("priceSpecification")
    spec = spec[0] if isinstance(spec, list) and spec else spec
    spec = spec if isinstance(spec, dict) else {}
    bruto = o.get("price")
    if bruto in (None, ""):
        bruto = spec.get("price")
    if bruto in (None, ""):
        bruto = o.get("lowPrice")
    moeda = o.get("priceCurrency") or spec.get("priceCurrency")
    return parse_decimal_maquina(bruto), moeda


def _chave_final(v: str | None) -> str:
    return (v or "").split("/")[-1].lower()


def _ancora(texto: str, preco: Decimal) -> int | None:
    """Posição da 1ª ocorrência do preço no texto visível (preferindo 'R$ 1.234,50'); None se não aparece."""
    inteiro, _, cent = f"{preco:.2f}".partition(".")
    milhar = f"{int(inteiro):,}".replace(",", ".")
    com_centavos = sorted({f"{milhar},{cent}", f"{inteiro},{cent}"}, key=len, reverse=True)
    inteiras = sorted({milhar, inteiro}, key=len, reverse=True) if cent == "00" else []
    regs = [rf"R\$\s*{re.escape(f)}(?!\d)" for f in com_centavos] + [rf"R\$\s*{re.escape(f)}(?!\d|,\d)" for f in inteiras]
    regs += [rf"(?<![\d.,]){re.escape(f)}(?!\d)" for f in com_centavos] + [rf"(?<![\d.,]){re.escape(f)}(?!\d|,\d)" for f in inteiras]
    for reg in regs[: len(regs) // 2]:                  # 1º as formas com R$
        achados = [m.start() for m in re.finditer(reg, texto)]
        if achados:
            return min(achados)
    posicoes = [m.start() for reg in regs[len(regs) // 2:] for m in re.finditer(reg, texto)]
    return min(posicoes) if posicoes else None


def _perto_de_exclusao(texto: str, pos: int, fim: int) -> bool:
    return bool(_RE_EXCLUI_ANTES.search(texto[max(0, pos - 110): pos]) or _RE_EXCLUI_DEPOIS.search(texto[fim: fim + 130]))


def _condicoes_de_pagamento(ex: Extracao, texto: str, ancora: int) -> None:
    """Pix/à vista e parcelas do ITEM PRINCIPAL: só aceita o que está colado ao preço principal.

    Páginas de loja trazem grades de produtos relacionados (cada um com seu 'R$ X no Pix') e blocos de atacado; ler a página
    inteira e pegar o menor valor atribuiria ao produto o preço de outro. Por isso: janela ao redor da âncora, exclusão de
    contexto de atacado/revenda/kit e escolha do candidato MAIS PRÓXIMO (não do menor).
    """
    assert ex.preco is not None
    preco = ex.preco
    ini, fim = max(0, ancora - _JANELA), min(len(texto), ancora + _JANELA)
    trecho = texto[ini:fim]

    # --- Pix / à vista
    cands: list[tuple[int, Decimal, bool, str]] = []
    for rx, eh_pix in ((_RE_PIX_A, True), (_RE_PIX_B, True), (_RE_AVISTA_A, False), (_RE_AVISTA_B, False)):
        for m in rx.finditer(trecho):
            pos = ini + m.start()
            valor = parse_brl_texto(m.group(1))
            if valor is None or not (preco * Decimal("0.5") <= valor <= preco) or _perto_de_exclusao(texto, pos, ini + m.end()):
                continue
            janela = texto[max(0, pos - 45): ini + m.end() + 45].lower()
            meio = "pix" if "pix" in janela else "boleto" if "boleto" in janela else "cartão" if re.search(r"cart[aã]o|cr[eé]dito", janela) else "não especificado"
            cands.append((abs(pos - ancora), valor, eh_pix, meio))

    if any(eh_pix and valor == preco for _, valor, eh_pix, _ in cands):
        # o preço dos dados estruturados JÁ É o do Pix ("R$ 325,00 em 6x ... ou R$ 292,50 via Pix"): procurar o preço de lista
        ex.preco_pix, ex.meio_vista = preco, "pix"
        listas = []
        for m in _RE_LISTA_PARC.finditer(trecho):
            pos = ini + m.start()
            lista, n, v = parse_brl_texto(m.group(1)), int(m.group(2)), parse_brl_texto(m.group(3))
            if lista is None or v is None or _perto_de_exclusao(texto, pos, ini + m.end()) or not (preco < lista <= preco * Decimal("1.6")):
                continue
            if abs(n * v - lista) <= max(Decimal("0.50"), lista * Decimal("0.005")):      # parcelas sem juros que somam o preço de lista
                listas.append((abs(pos - ancora), lista))
        if listas:
            ex.preco_lista = min(listas)[1]
        ex.avisos.append("o preço dos dados estruturados coincide com o valor do Pix da página" + ("" if ex.preco_lista else "; preço de lista/cartão não identificado"))
    else:
        descontos = sorted((dist, valor, meio) for dist, valor, _, meio in cands if valor < preco)
        if descontos:
            _, ex.preco_pix, ex.meio_vista = descontos[0]

    # --- parcelas: coleta bruta ao redor da âncora
    brutas: list[tuple[int, int, Decimal, bool | None]] = []          # (distância, n, valor, sem_juros)
    for m in _RE_PARC.finditer(trecho):
        pos = ini + m.start()
        n, valor = int(m.group(1)), parse_brl_texto(m.group(2))
        if not valor or n < 2 or n > 24 or _perto_de_exclusao(texto, pos, ini + m.end()):
            continue
        resto = m.group("resto")
        sem = True if _RE_SEM_JUROS.search(resto) else False if _RE_COM_JUROS.search(resto) else None
        brutas.append((abs(pos - ancora), n, valor, sem))

    # preço estruturado = Pix e a página não mostra o valor de lista: parcelas SEM JUROS somam, por definição, o preço de lista
    if ex.meio_vista == "pix" and ex.preco_pix == preco and ex.preco_lista is None:
        derivados = [(d, n * v) for d, n, v, sem in brutas if sem is True and preco < n * v <= preco * Decimal("1.25")]
        if derivados:
            ex.preco_lista = min(derivados)[1]
            ex.avisos.append("preço de lista/cartão derivado das parcelas sem juros (n × parcela)")

    # --- parcelas plausíveis para ESTE item (em relação ao preço de lista, se houver)
    base = ex.preco_lista or preco
    achados: dict[int, tuple[int, Parcelamento]] = {}
    for dist, n, valor, sem in brutas:
        total = valor * n
        if sem is True:      # sem juros: as parcelas têm de FECHAR com o preço de lista (±1%); senão é parcelamento de outro item
            if abs(total - base) > max(Decimal("1.00"), base * Decimal("0.01")):
                continue
        elif not (base * Decimal("0.99") <= total <= base * Decimal("1.6")):
            continue         # com juros/indefinido: só pode custar mais que o preço de lista, e não absurdamente mais
        ant = achados.get(n)
        if ant is None or dist < ant[0] or (ant[1].sem_juros is None and sem is not None and dist <= ant[0] + 80):
            achados[n] = (dist, Parcelamento(n, valor, sem))
    ex.parcelamentos = sorted((p for _, p in achados.values()), key=lambda p: p.n)


def extrair(html: str, url: str) -> Extracao:
    ex = Extracao()
    soup = BeautifulSoup(html, "lxml")

    # ---------- JSON-LD ----------
    nos: list[dict] = []
    for sc in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        bruto = (sc.string or sc.get_text() or "").strip()
        if not bruto:
            continue
        try:
            _percorrer(json.loads(bruto), nos)
        except ValueError:
            try:
                _percorrer(json.loads(re.sub(r"[\x00-\x1f]+", " ", bruto)), nos)
            except ValueError:
                ex.avisos.append("um bloco JSON-LD da página está malformado e foi ignorado")
    produtos = [n for n in nos if _tipos(n) & {"product", "productgroup"}]
    # as variantes aninhadas em `hasVariant` pertencem ao grupo (não são fichas distintas, senão toda página com variações viraria "listagem")
    variantes_dos_grupos = {id(var) for pr in produtos if "productgroup" in _tipos(pr) for var in (pr.get("hasVariant") or []) if isinstance(var, dict)}
    produtos = [pr for pr in produtos if id(pr) not in variantes_dos_grupos]
    vistos, unicos = set(), []
    for pr in produtos:                     # deduplica o mesmo produto repetido no grafo
        chave = (pr.get("sku"), pr.get("name"), json.dumps(pr.get("offers"), sort_keys=True, default=str))
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(pr)
    produto = None
    if len(unicos) == 1:
        produto = unicos[0]
    elif len(unicos) > 1:
        alvo = normalizar_url(url)
        for pr in unicos:
            u = pr.get("url") or pr.get("@id") or ""
            if isinstance(u, str) and u.startswith("http") and normalizar_url(u.split("#")[0]) == alvo:
                produto = pr
                break
        if produto is None:
            ex.listagem = True
            ex.avisos.append(f"{len(unicos)} produtos distintos no JSON-LD (provável página de listagem): preço não atribuído a nenhum")
    if produto is not None and "productgroup" in _tipos(produto):
        ex.avisos.append("página de grupo de variações: o preço pode depender da variação escolhida")
        variantes = [var for var in (produto.get("hasVariant") or []) if isinstance(var, dict)]
        ofertas_grupo = list(_ofertas(produto))
        for var in variantes:
            ofertas_grupo += _ofertas(var)                   # a escolha entre ofertas (e o aviso de preços diferentes) é feita adiante
        produto = {**produto, "offers": ofertas_grupo}
        if len(variantes) == 1:                              # um único item: seus dados descrevem o produto
            produto = {**variantes[0], **{chave: valor for chave, valor in produto.items() if chave not in variantes[0] or chave == "offers"}}

    if produto is not None:
        ex.titulo = _nome(produto)
        ex.marca = _nome(produto.get("brand"))
        ex.sku = str(produto.get("sku") or produto.get("mpn") or "").strip() or None
        for k in ("gtin", "gtin8", "gtin12", "gtin13", "gtin14"):
            if produto.get(k) and gtin_valido(str(produto[k])):
                ex.gtin = re.sub(r"\D", "", str(produto[k]))
                break
        ofs = []
        for o in _ofertas(produto):
            preco, moeda = _preco_da_oferta(o)
            if preco is None or preco <= 0:
                continue
            if moeda and str(moeda).upper() != "BRL":
                ex.avisos.append(f"oferta em moeda {moeda} ignorada")
                continue
            disp = _DISP.get(_chave_final(o.get("availability")))
            ofs.append((preco, disp, o))
        if ofs:
            em_estoque = [x for x in ofs if x[1] == "em_estoque"]
            escolha = min(em_estoque or ofs, key=lambda x: x[0])
            ex.preco, ex.disponibilidade, o = escolha
            ex.fonte_preco = "json-ld"
            ex.vendedor = _nome(o.get("seller"))
            ex.condicao = _COND.get(_chave_final(o.get("itemCondition")))
            if len({x[0] for x in ofs}) > 1:
                ex.avisos.append(f"{len(ofs)} ofertas no JSON-LD com preços diferentes (marketplace/variações): usado o menor preço em estoque")
            if "aggregateoffer" in _tipos(o):
                ex.avisos.append("AggregateOffer: o preço é o MENOR entre vários vendedores; vendedor real não identificado")

    # ---------- meta tags (fallback) ----------
    def meta(*nomes):
        for n in nomes:
            tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n}) or soup.find("meta", attrs={"itemprop": n})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return None

    if ex.preco is None and not ex.listagem:
        mp = parse_decimal_maquina(meta("product:price:amount", "og:price:amount", "price"))
        moeda = meta("product:price:currency", "og:price:currency", "priceCurrency")
        if mp and mp > 0 and (not moeda or moeda.upper() == "BRL"):
            ex.preco, ex.fonte_preco = mp, "meta"
    ex.titulo = ex.titulo or meta("og:title") or (soup.title.string.strip() if soup.title and soup.title.string else None)
    ex.loja = meta("og:site_name") or None
    if not ex.disponibilidade:
        ex.disponibilidade = _DISP.get(_chave_final(meta("product:availability", "og:availability")).replace(" ", ""))

    ex.titulos = [t for t in dict.fromkeys(
        [meta("og:title") or "", (soup.title.string or "").strip() if soup.title and soup.title.string else ""]
        + [h.get_text(" ", strip=True) for h in soup.find_all("h1")[:2]]) if t]

    # ---------- texto visível (heurísticas: sempre ESTIMADO) ----------
    for t in soup(["script", "style", "noscript", "template"]):
        t.decompose()
    texto = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:600_000]
    texto = re.sub(r"(R\$\s*[\d.]+)\s*,\s*(\d{2})\b", r"\1,\2", texto)       # algumas plataformas escrevem "R$ 220 , 41"
    ex.cnpjs = achar_cnpjs(texto)

    if ex.preco is not None:
        ancora = _ancora(texto, ex.preco)
        ex.preco_corroborado = ancora is not None
        if ancora is None:
            ex.avisos.append("o preço dos dados estruturados não aparece no texto visível da página (pode ser renderizado por JavaScript ou estar divergente); "
                             "Pix e parcelas do texto NÃO foram atribuídos a este item")
        else:
            _condicoes_de_pagamento(ex, texto, ancora)
        if ex.preco_pix or ex.parcelamentos:
            ex.avisos.append("Pix/parcelamento lidos do texto da página por heurística: confirme no checkout")
    gm = _RE_GARANTIA.search(texto)
    if gm:
        ex.garantia = texto[max(0, gm.start() - 5): gm.end() + 25].strip()
    if ex.preco is None and not ex.listagem:
        ex.avisos.append("nenhum preço encontrado no HTML estático (a página pode exigir JavaScript, login ou CEP)")
    return ex


def loja_pelo_dominio(url: str) -> str:
    base = dominio(url).split(".")
    nome = base[-3] if len(base) >= 3 and base[-2] in ("com", "net", "org") else base[-2] if len(base) >= 2 else base[0]
    return nome.replace("-", " ").title()
