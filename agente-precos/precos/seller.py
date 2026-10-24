"""Sinais verificáveis sobre a loja: CNPJ (Receita via BrasilAPI), idade do domínio .br (RDAP), CEP (ViaCEP)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from .apis import ApiJson
from .matching import tokens
from .util import agora_brt, cnpj_digitos, cnpj_valido, dominio as dominio_de, fmt_cnpj

URL_CNPJ = "https://brasilapi.com.br/api/cnpj/v1/{}"
URL_CEP = "https://viacep.com.br/ws/{}/json/"
URL_RDAP = "https://rdap.registro.br/domain/{}"

TIERS_COM_EVIDENCIA = {"oficial", "autorizado", "varejista", "marketplace_verificavel"}
TIERS = TIERS_COM_EVIDENCIA | {"insuficiente", "risco"}
_SLD_BR = {"com", "net", "org", "ind", "eco", "art", "mus", "tv", "blog", "app", "dev", "log", "shop", "loja", "emp", "eng", "adv", "med"}
# Domínios que são MARKETPLACES/classificados: o vendedor real precisa ser identificado em cada anúncio.
MARKETPLACES = {
    "mercadolivre.com.br", "produto.mercadolivre.com.br", "amazon.com.br", "magazineluiza.com.br", "americanas.com.br",
    "submarino.com.br", "shoptime.com.br", "casasbahia.com.br", "shopee.com.br", "olx.com.br", "aliexpress.com",
    "kabum.com.br", "carrefour.com.br", "pontofrio.com.br", "extra.com.br", "enjoei.com.br",
}
CLASSIFICADOS = {"olx.com.br", "enjoei.com.br"}   # negociação entre pessoas: sem loja por trás


def eh_marketplace(dom: str) -> bool:
    return any(dom == m or dom.endswith("." + m) for m in MARKETPLACES)


def eh_classificado(dom: str) -> bool:
    return any(dom == m or dom.endswith("." + m) for m in CLASSIFICADOS)


@dataclass
class Identidade:
    cnpj: str
    razao_social: str | None
    nome_fantasia: str | None
    situacao: str | None
    abertura: str | None            # ISO yyyy-mm-dd
    cnae: str | None
    municipio: str | None
    uf: str | None
    fonte: str
    consultado_em: str

    @classmethod
    def from_dict(cls, d: dict) -> "Identidade":
        """Tolerante a cadastros antigos/incompletos: campo ausente vira None em vez de derrubar a comparação."""
        campos = cls.__dataclass_fields__.keys()
        return cls(**{k: d.get(k) for k in campos})

    @property
    def ativa(self) -> bool:
        return (self.situacao or "").upper() == "ATIVA"

    @property
    def anos_atividade(self) -> float | None:
        try:
            return (date.today() - date.fromisoformat(self.abertura)).days / 365.25 if self.abertura else None
        except ValueError:
            return None

    def compativel_com(self, *nomes: str | None) -> bool:
        """Heurística (ESTIMADO): o nome da loja/domínio lembra a razão social ou o nome fantasia?

        Não prova que o CNPJ pertence ao site; só indica que não há contradição óbvia.
        """
        ruido = {"ltda", "sa", "me", "epp", "eireli", "comercio", "comercial", "de", "do", "da", "e", "com", "br", "www",
                 "loja", "online", "store", "shop", "digital", "servicos", "industria", "importacao", "exportacao"}

        def distintivos(n: str | None) -> list[str]:
            return [t for t in tokens(n or "") if t not in ruido and len(t) > 2]

        legais = [distintivos(n) for n in (self.razao_social, self.nome_fantasia) if n]
        meus = {t for ts in legais for t in ts}
        colados = ["".join(ts) for ts in legais if ts]
        deles = {t for n in nomes for t in distintivos(n)}
        if meus & deles:
            return True
        if any(len(m) >= 5 and m in d for m in meus for d in deles):       # 'mundo' dentro de 'mundomusical'
            return True
        return any(len(d) >= 6 and d in c for d in deles for c in colados)  # 'mundomusical' dentro de 'mundomusicalinstrumentos'

    def to_dict(self) -> dict:
        return asdict(self)


def consultar_cnpj(cnpj: str, api: ApiJson) -> tuple[Identidade | None, str | None]:
    d = cnpj_digitos(cnpj)
    if not cnpj_valido(d):
        return None, "CNPJ inválido (dígitos verificadores não conferem)"
    url = URL_CNPJ.format(d)
    status, corpo, erro = api.get(url)
    if erro or not isinstance(corpo, dict):
        return None, f"consulta de CNPJ indisponível ({erro or 'resposta inesperada'})"
    return Identidade(
        cnpj=d, razao_social=corpo.get("razao_social"), nome_fantasia=corpo.get("nome_fantasia") or None,
        situacao=corpo.get("descricao_situacao_cadastral"), abertura=corpo.get("data_inicio_atividade"),
        cnae=corpo.get("cnae_fiscal_descricao"), municipio=corpo.get("municipio"), uf=corpo.get("uf"),
        fonte=url, consultado_em=agora_brt().isoformat(),
    ), None


def consultar_cep(cep: str, api: ApiJson) -> tuple[dict | None, str | None]:
    d = "".join(c for c in cep if c.isdigit())
    if len(d) != 8:
        return None, "CEP deve ter 8 dígitos"
    status, corpo, erro = api.get(URL_CEP.format(d))
    if erro or not isinstance(corpo, dict):
        return None, f"consulta de CEP indisponível ({erro or 'resposta inesperada'})"
    if corpo.get("erro"):
        return None, "CEP não encontrado"
    return corpo, None


def dominio_registravel_br(host: str) -> str | None:
    p = host.lower().split(".")
    if p[-1] != "br" or len(p) < 2:
        return None
    return ".".join(p[-3:]) if len(p) >= 3 and p[-2] in _SLD_BR else ".".join(p[-2:])


def idade_dominio(host: str, api: ApiJson) -> tuple[dict | None, str | None]:
    """Data de registro do domínio .br via RDAP do Registro.br. Outros TLDs: não verificado."""
    reg = dominio_registravel_br(host)
    if reg is None:
        return None, "idade do domínio só é verificada para .br"
    url = URL_RDAP.format(reg)
    status, corpo, erro = api.get(url, recusa_transitoria=True)
    if erro or not isinstance(corpo, dict):
        return None, f"RDAP indisponível ({erro or 'resposta inesperada'})"
    registro = next((e.get("eventDate") for e in corpo.get("events", []) if e.get("eventAction") == "registration"), None)
    if not registro:
        return None, "RDAP sem data de registro"
    try:
        dt = datetime.fromisoformat(registro.replace("Z", "+00:00"))
    except ValueError:
        return None, "data de registro ilegível"
    dias = (datetime.now(dt.tzinfo) - dt).days
    return {"dominio": reg, "registrado_em": dt.date().isoformat(), "idade_dias": dias, "fonte": url, "consultado_em": agora_brt().isoformat()}, None


@dataclass
class Loja:
    """Registro de uma loja. Classificações subjetivas EXIGEM URL de evidência."""

    dominio: str
    nome: str | None = None
    tier: str | None = None
    tier_fonte: str | None = None
    cnpj: str | None = None
    cnpj_fonte: str | None = None
    reputacao_nota: float | None = None     # escala informada em `reputacao_escala`
    reputacao_escala: float | None = None
    reputacao_fonte: str | None = None
    reputacao_data: str | None = None
    politicas_ok: bool | None = None
    politicas_fonte: str | None = None
    identidade: dict | None = None          # cache da consulta de CNPJ
    rdap: dict | None = None                # cache da consulta de domínio
    notas: list[str] = field(default_factory=list)

    def definir_tier(self, tier: str, fonte: str | None) -> None:
        if tier not in TIERS:
            raise ValueError(f"tier inválido: {tier}. Use: {', '.join(sorted(TIERS))}")
        if tier in TIERS_COM_EVIDENCIA and not (fonte and fonte.startswith(("http://", "https://"))):
            raise ValueError(f"'{tier}' exige --fonte com a URL que comprova (página oficial de revendedores, perfil verificado etc.)")
        self.tier, self.tier_fonte = tier, fonte

    def definir_reputacao(self, nota: float, escala: float, fonte: str, data: str | None = None) -> None:
        if not (fonte and fonte.startswith(("http://", "https://"))):
            raise ValueError("reputação exige a URL da fonte independente")
        if not (0 <= nota <= escala and escala > 0):
            raise ValueError("nota fora da escala")
        self.reputacao_nota, self.reputacao_escala, self.reputacao_fonte = nota, escala, fonte
        self.reputacao_data = data or agora_brt().date().isoformat()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Loja":
        validos = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in validos})
