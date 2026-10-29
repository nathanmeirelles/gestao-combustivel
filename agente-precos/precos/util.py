"""Funções pequenas e puras usadas em todo o projeto (dinheiro, tempo, URLs)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

BRT: tzinfo
try:
    from zoneinfo import ZoneInfo

    BRT = ZoneInfo("America/Sao_Paulo")
except Exception:  # sem tzdata no sistema: o Brasil não tem horário de verão desde 2019
    BRT = timezone(timedelta(hours=-3), "BRT")

CENT = Decimal("0.01")

# Parâmetros de rastreamento que não mudam o produto e atrapalham a deduplicação.
_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|gbraid|wbraid|msclkid|mc_[a-z]+|ref|ref_|tag|pf_rd_.*|pd_rd_.*|psc|th|_gl|srsltid|awc|aff.*|source|origem)$", re.I)
# Parâmetros cujo valor nunca deve aparecer em logs.
_SECRET_PARAM = re.compile(r"(key|token|secret|senha|password|apikey|api_key|authorization|cx)$", re.I)


def agora_brt() -> datetime:
    return datetime.now(BRT)


def fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.astimezone(BRT).strftime("%d/%m/%Y %H:%M (Brasília)")


def D(valor) -> Decimal | None:
    """Converte para Decimal sem passar por float. Retorna None se não for número válido."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor).strip())
    except (InvalidOperation, ValueError):
        return None


def money(valor: Decimal) -> Decimal:
    return valor.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_decimal_maquina(texto) -> Decimal | None:
    """Preço em dados estruturados (JSON-LD/meta): '.' é decimal ('4299.00'); aceita '4.299,00' também."""
    if texto is None:
        return None
    if isinstance(texto, (int, float, Decimal)):
        return D(texto)
    s = re.sub(r"[^\d.,-]", "", str(texto))
    if not s:
        return None
    if "," in s and "." in s:
        # o último separador é o decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    return D(s)


def parse_brl_texto(texto: str) -> Decimal | None:
    """Valor escrito em formato brasileiro no texto visível: 'R$ 4.299,00' / '4299,9' / '4.299'."""
    s = re.sub(r"[^\d.,]", "", texto or "")
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):  # '4.299' = quatro mil, duzentos e noventa e nove
        s = s.replace(".", "")
    return D(s)


def parse_valor_usuario(texto) -> Decimal | None:
    """Valor digitado por uma pessoa: '4.299' = 4299 (milhar BR), '4299,90', '4299.5', 'R$ 1.234,56'. None se inválido."""
    if isinstance(texto, Decimal):
        return texto
    s = str(texto).replace("R$", "").strip()
    if not s:
        return None
    v = parse_brl_texto(s) if re.fullmatch(r"\d{1,3}(\.\d{3})+", s) else parse_decimal_maquina(s)
    return v if v is not None and v >= 0 else None


def fmt_brl(valor: Decimal | None) -> str:
    if valor is None:
        return "—"
    v = money(valor)
    inteiro, _, cent = f"{v:.2f}".partition(".")
    sinal = "-" if inteiro.startswith("-") else ""
    inteiro = inteiro.lstrip("-")
    grupos: list[str] = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)
    return f"R$ {sinal}{'.'.join(grupos)},{cent}"


def dominio(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def normalizar_url(url: str) -> str:
    """Remove rastreadores e fragmento para deduplicar anúncios; mantém parâmetros que identificam o item."""
    p = urlsplit(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k)]
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(q), ""))


def redigir_url(url: str) -> str:
    """Para logs: mascara valores de parâmetros que parecem credenciais."""
    p = urlsplit(url)
    q = [(k, "***" if _SECRET_PARAM.search(k) else v) for k, v in parse_qsl(p.query, keep_blank_values=True)]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q, safe="*"), ""))


_RE_CNPJ = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")


def cnpj_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def cnpj_valido(texto: str) -> bool:
    d = cnpj_digitos(texto)
    if len(d) != 14 or len(set(d)) == 1:
        return False
    for tam in (12, 13):
        pesos = ([5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2] if tam == 12 else [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
        soma = sum(int(d[i]) * pesos[i] for i in range(tam))
        dv = 11 - soma % 11
        dv = 0 if dv >= 10 else dv
        if int(d[tam]) != dv:
            return False
    return True


def fmt_cnpj(texto: str) -> str:
    d = cnpj_digitos(texto)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}" if len(d) == 14 else texto


def achar_cnpjs(texto: str) -> list[str]:
    """CNPJs com dígitos verificadores válidos encontrados no texto (ordem de aparição, sem repetir)."""
    vistos: list[str] = []
    for m in _RE_CNPJ.finditer(texto or ""):
        d = cnpj_digitos(m.group(0))
        if cnpj_valido(d) and d not in vistos:
            vistos.append(d)
    return vistos


def gtin_valido(codigo: str) -> bool:
    d = re.sub(r"\D", "", codigo or "")
    if len(d) not in (8, 12, 13, 14):
        return False
    soma = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(d[:-1])))
    return (10 - soma % 10) % 10 == int(d[-1])
