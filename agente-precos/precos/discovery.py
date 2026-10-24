"""Descoberta de URLs de produto via APIs OFICIAIS de busca (exigem chave). Resultados são só candidatos."""

from __future__ import annotations

from dataclasses import dataclass

from .apis import ApiJson
from .config import Config


@dataclass
class Candidato:
    titulo: str
    url: str
    trecho: str
    provedor: str


class SemProvedor(RuntimeError):
    pass


def provedores_configurados(cfg: Config) -> list[str]:
    nomes = []
    if cfg.brave_key:
        nomes.append("brave")
    if cfg.google_key and cfg.google_cx:
        nomes.append("google_cse")
    return nomes


def buscar(cfg: Config, api: ApiJson, consulta: str, dominios: list[str] | None = None, limite: int = 10) -> tuple[list[Candidato], list[str]]:
    """(candidatos, erros). Se nenhum provedor estiver configurado, levanta SemProvedor com instruções."""
    provs = provedores_configurados(cfg)
    if not provs:
        raise SemProvedor("Nenhum provedor de busca configurado. Defina BRAVE_API_KEY ou GOOGLE_CSE_KEY+GOOGLE_CSE_CX (veja .env.example), "
                          "ou, numa sessão do Claude Code, use a ferramenta WebSearch com allowed_domains e registre os links com `precos candidato`.")
    q = consulta + (" " + " OR ".join(f"site:{d}" for d in dominios) if dominios else "")
    achados: list[Candidato] = []
    erros: list[str] = []
    if "brave" in provs:
        st, corpo, erro = api.get("https://api.search.brave.com/res/v1/web/search",
                                  params={"q": q, "country": "br", "search_lang": "pt", "count": min(limite, 20)},
                                  headers={"X-Subscription-Token": cfg.brave_key})
        if erro or not isinstance(corpo, dict):
            erros.append(f"brave: {erro or 'resposta inesperada'}")
        else:
            for r in (corpo.get("web") or {}).get("results", [])[:limite]:
                achados.append(Candidato(r.get("title", ""), r.get("url", ""), r.get("description", ""), "brave"))
    if "google_cse" in provs and len(achados) < limite:
        st, corpo, erro = api.get("https://www.googleapis.com/customsearch/v1",
                                  params={"key": cfg.google_key, "cx": cfg.google_cx, "q": q, "gl": "br", "lr": "lang_pt", "num": min(limite, 10)})
        if erro or not isinstance(corpo, dict):
            erros.append(f"google_cse: {erro or 'resposta inesperada'}")
        else:
            for r in corpo.get("items", [])[:limite]:
                achados.append(Candidato(r.get("title", ""), r.get("link", ""), r.get("snippet", ""), "google_cse"))
    vistos, unicos = set(), []
    for c in achados:
        if c.url and c.url not in vistos:
            vistos.add(c.url)
            unicos.append(c)
    return unicos, erros
