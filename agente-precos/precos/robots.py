"""Verificador de robots.txt com curingas (`*`) e fim de linha (`$`), como o Google interpreta.

O `urllib.robotparser` da biblioteca padrão NÃO entende curingas e liberaria por engano regras como
`Disallow: *?q=` (usada por Zoom/Buscapé para bloquear a busca). Por isso este módulo existe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class _Regra:
    permitir: bool
    padrao: str
    regex: re.Pattern

    @property
    def tamanho(self) -> int:
        return len(self.padrao)


@dataclass
class _Grupo:
    agentes: list[str] = field(default_factory=list)
    regras: list[_Regra] = field(default_factory=list)
    atraso: float | None = None


def _compilar(padrao: str) -> re.Pattern:
    termina = padrao.endswith("$")
    corpo = padrao[:-1] if termina else padrao
    partes = [re.escape(p) for p in corpo.split("*")]
    return re.compile(".*".join(partes) + ("$" if termina else ""))


class Robots:
    def __init__(self, texto: str | None, *, indisponivel: bool = False, motivo: str | None = None):
        """`texto=None` + `indisponivel=True` (erro de rede/5xx): política conservadora — nega tudo."""
        self.indisponivel = indisponivel
        self.motivo = motivo
        self.grupos: list[_Grupo] = []
        if texto:
            self._parse(texto)

    def _parse(self, texto: str) -> None:
        atual: _Grupo | None = None
        ultimo_foi_agente = False
        for bruta in texto.splitlines():
            linha = bruta.split("#", 1)[0].strip()
            if ":" not in linha:
                continue
            chave, _, valor = linha.partition(":")
            chave, valor = chave.strip().lower(), valor.strip()
            if chave == "user-agent":
                if not ultimo_foi_agente or atual is None:
                    atual = _Grupo()
                    self.grupos.append(atual)
                atual.agentes.append(valor.lower())
                ultimo_foi_agente = True
                continue
            ultimo_foi_agente = False
            if atual is None:
                continue
            if chave in ("allow", "disallow"):
                if valor == "":
                    continue  # "Disallow:" vazio = tudo liberado
                atual.regras.append(_Regra(chave == "allow", valor, _compilar(valor)))
            elif chave == "crawl-delay":
                try:
                    atual.atraso = float(valor)
                except ValueError:
                    pass

    def _grupo(self, agente: str) -> _Grupo | None:
        agente = agente.lower()
        melhor: tuple[int, _Grupo] | None = None
        for g in self.grupos:
            for a in g.agentes:
                if a != "*" and a and a in agente and (melhor is None or len(a) > melhor[0]):
                    melhor = (len(a), g)
        if melhor:
            return melhor[1]
        return next((g for g in self.grupos if "*" in g.agentes), None)

    def permitido(self, url: str, agente: str) -> bool:
        if self.indisponivel:
            return False
        g = self._grupo(agente)
        if g is None:
            return True
        p = urlsplit(url)
        alvo = (p.path or "/") + (f"?{p.query}" if p.query else "")
        vencedora: _Regra | None = None
        for r in g.regras:
            if r.regex.match(alvo):
                if (
                    vencedora is None
                    or r.tamanho > vencedora.tamanho
                    or (r.tamanho == vencedora.tamanho and r.permitir and not vencedora.permitir)
                ):
                    vencedora = r
        return True if vencedora is None else vencedora.permitir

    def atraso(self, agente: str) -> float | None:
        g = self._grupo(agente)
        return g.atraso if g else None
