"""Extrai dados de oferta de uma página de produto: JSON-LD > meta tags > texto visível (só como estimativa)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .models import Parcelamento
from .util import achar_cnpjs, D, dominio, fmt_brl, gtin_valido, normalizar_url, parse_brl_texto, parse_decimal_maquina

_IGNORAR_CHAVES = {"isrelatedto", "issimilarto", "isaccessoryorsparepartfor", "isconsumablefor", "review", "aggregaterating"}
_DISP = {
    "instock": "em_estoque", "onlineonly": "em_estoque", "instoreonly": "em_estoque",
    "outofstock": "sem_estoque", "soldout": "sem_estoque", "discontinued": "sem_estoque",
    "preorder": "sob_encomenda", "backorder": "sob_encomenda", "presale": "sob_encomenda",
    "limitedavailability": "limitada",
}
_COND = {"newcondition": "novo", "usedcondition": "usado", "refurbishedcondition": "recondicionado", "damagedcondition": "danificado"}

_RE_PARC = re.compile(r"(\d{1,2})\s*[xX]\s*(?:de\s*)?R\$\s*([\d\.]+,\d{2})(?=(?P<resto>.{0,45}))", re.I)
_RE_PIX_A = re.compile(r"R\$\s*([\d\.]+,\d{2})\s*[\(\-–]?\s*(?:à vista\s+)?(?:no|via|com|pelo|no\s+pagamento\s+via)?\s*pix", re.I)
_RE_PIX_B = re.compile(r"pix[^R\d]{0,25}R\$\s*([\d\.]+,\d{2})", re.I)
_RE_AVISTA_A = re.compile(r"R\$\s*([\d\.]+,\d{2})\s*[\(\-–:]?\s*(?:à|a)\s+vista", re.I)
_RE_AVISTA_B = re.compile(r"(?:à|a)\s+vista[^R\d]{0,20}R\$\s*([\d\.]+,\d{2})", re.I)
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
        variantes = [v for v in (produto.get("hasVariant") or []) if isinstance(v, dict)]
        if len(variantes) == 1:
            produto = {**produto, **variantes[0]}

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
    ex.cnpjs = achar_cnpjs(texto)

    if ex.preco is not None:
        formas = {f"{ex.preco:.2f}".replace(".", ","), fmt_brl(ex.preco).replace("R$ ", "")}
        if ex.preco == ex.preco.to_integral_value():
            formas.add(f"{int(ex.preco):,}".replace(",", "."))
        ex.preco_corroborado = any(f in texto for f in formas)
        if not ex.preco_corroborado:
            ex.avisos.append("o preço dos dados estruturados não aparece no texto visível da página (pode ser renderizado por JavaScript ou estar divergente)")

        for rx in (_RE_PIX_A, _RE_PIX_B, _RE_AVISTA_A, _RE_AVISTA_B):
            for m in rx.finditer(texto):
                v = parse_brl_texto(m.group(1))
                if v and ex.preco * Decimal("0.5") <= v < ex.preco and (ex.preco_pix is None or v < ex.preco_pix):
                    ex.preco_pix = v
                    janela = texto[max(0, m.start() - 45): m.end() + 45].lower()
                    ex.meio_vista = "pix" if "pix" in janela else "boleto" if "boleto" in janela else "cartão" if re.search(r"cart[aã]o|cr[eé]dito", janela) else "não especificado"
        achados: dict[int, Parcelamento] = {}
        for m in _RE_PARC.finditer(texto):
            n, valor = int(m.group(1)), parse_brl_texto(m.group(2))
            if not valor or n < 2 or n > 24:
                continue
            total = valor * n
            if not (ex.preco * Decimal("0.97") <= total <= ex.preco * Decimal("2.0")):
                continue  # não parece ser o parcelamento DESTE produto
            resto = m.group("resto").lower()
            sem = True if "sem juros" in resto else False if "juros" in resto else None
            ant = achados.get(n)
            if ant is None or (ant.sem_juros is None and sem is not None):
                achados[n] = Parcelamento(n, valor, sem)
        ex.parcelamentos = sorted(achados.values(), key=lambda p: p.n)
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
