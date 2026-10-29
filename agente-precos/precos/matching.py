"""Confere se o anúncio é mesmo o produto pedido (modelo, variante, condição, acessório)."""

from __future__ import annotations

import re
import unicodedata

from .models import Consulta, Oferta

_STOP = {"de", "da", "do", "das", "dos", "com", "para", "por", "em", "a", "o", "e", "um", "uma", "novo", "nova", "novos", "novas", "original", "oficial"}
_SUFIXOS_VARIANTE = {"xl", "pro", "plus", "mini", "max", "lite", "se", "ii", "iii", "iv", "ultra", "nano", "light", "air", "x", "s", "fx", "gt"}
_USADO = {"usado", "usada", "seminovo", "seminova", "semi", "recondicionado", "recondicionada", "vitrine", "defeito", "sucata", "avariado", "avariada", "reparo", "pecas", "revisado", "revisada"}
_USADO_FRASES = ("open box", "retirada de pecas", "pronta entrega usado")
_ACESSORIO_INICIO = {"capa", "case", "bag", "suporte", "fonte", "cabo", "adaptador", "pelicula", "protetor", "carregador", "manual", "etiqueta", "adesivo"}
_COMBO = {"kit", "combo", "bundle", "pack"}


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return sem_acento.lower()


def tokens(texto: str) -> list[str]:
    """'Line6 HX-Stomp' -> ['line','6','hx','stomp']; letras e números viram tokens separados."""
    return re.findall(r"[a-z]+|\d+", normalizar(texto))


def _requeridos(c: Consulta) -> list[str]:
    base = tokens(c.modelo or c.texto)
    return [t for t in base if t not in _STOP]


_SINAL_NOVO = {"lacrado", "lacrada"}


def _sinal_novo(tt: list[str]) -> list[str]:
    achados = [t for t in tt if t in _SINAL_NOVO]
    achados += [t for i, t in enumerate(tt) if t in ("novo", "nova") and (i == 0 or tt[i - 1] not in ("semi", "quase"))]
    return achados


def _em_sequencia(sub_lista: list[str], lista: list[str]) -> bool:
    n = len(sub_lista)
    return any(lista[i:i + n] == sub_lista for i in range(len(lista) - n + 1))


def _tokens_url(url: str | None) -> list[str]:
    from urllib.parse import urlsplit
    return tokens(urlsplit(url).path) if url else []


def avaliar_identidade(titulo: str | None, c: Consulta, oferta: Oferta | None = None, url: str | None = None,
                       titulos_extra: list[str] | None = None) -> tuple[str, list[str]]:
    """Retorna ('confere'|'divergente'|'incerto', motivos). Heurística sobre título/URL: nunca é prova.

    Sinais de condição (usado/novo) são lidos de TODAS as fontes disponíveis (nome estruturado, <title>, og:title, h1 e
    a slug da URL); se elas se contradizem, o anúncio é tratado como divergente até conferência manual.
    """
    motivos: list[str] = []
    if oferta and c.gtin and oferta.gtin:
        if oferta.gtin == c.gtin:
            return "confere", ["GTIN/EAN idêntico ao informado"]
        return "divergente", [f"GTIN do anúncio ({oferta.gtin}) difere do informado ({c.gtin})"]
    if not titulo:
        return "incerto", ["anúncio sem título legível"]

    tt = tokens(titulo)
    exigidos = _requeridos(c)
    faltando = [t for t in exigidos if t not in tt]

    for t in c.excluir:
        if all(x in tt for x in tokens(t)):
            motivos.append(f"contém termo excluído pelo usuário: '{t}'")

    consulta_toks = set(tokens(c.texto)) | set(exigidos)
    if exigidos and exigidos[-1] in tt:      # vale mesmo com a marca omitida: 'HX Stomp XL' continua sendo outro produto
        ultimo = exigidos[-1]
        pos = max(i for i, t in enumerate(tt) if t == ultimo)
        seguinte = tt[pos + 1] if pos + 1 < len(tt) else None
        if seguinte in _SUFIXOS_VARIANTE and seguinte not in consulta_toks:
            motivos.append(f"possível variante diferente do modelo pedido ('{ultimo} {seguinte}')")

    if tt and tt[0] in _ACESSORIO_INICIO:
        motivos.append(f"parece um acessório ('{tt[0]}...'), não o produto")

    quer_novo = normalizar(c.condicao) == "novo"
    if quer_novo:
        fontes = {"título": tt}
        for i, extra in enumerate(titulos_extra or []):
            fontes[f"título da página ({i + 1})"] = tokens(extra)
        fontes["URL"] = _tokens_url(url)
        usados = {nome: sorted(set(tk) & _USADO) + [f for f in _USADO_FRASES if f in " ".join(tk)] for nome, tk in fontes.items()}
        usados = {n: u for n, u in usados.items() if u}
        novos = {n: _sinal_novo(tk) for n, tk in fontes.items() if _sinal_novo(tk)}
        if usados and novos:
            motivos.append("sinais CONTRADITÓRIOS de condição: " + "; ".join(f"{n} indica '{', '.join(u)}'" for n, u in usados.items())
                           + " × " + "; ".join(f"{n} indica '{', '.join(v)}'" for n, v in novos.items()) + " — confirme com o vendedor")
        elif usados:
            motivos.append("indício de produto não-novo: " + "; ".join(f"{n} ({', '.join(u)})" for n, u in usados.items()))
        if oferta and oferta.condicao in ("usado", "recondicionado", "danificado"):
            motivos.append(f"condição declarada na página: {oferta.condicao}")

    if motivos:
        return "divergente", motivos
    if exigidos and faltando:
        m = f"título não contém: {', '.join(faltando)}"
        if c.modelo:
            # modelo EXPLÍCITO: faltar termo do meio/fim é outro produto (ex.: 'HX Effects' x 'HX Stomp'); faltar só a marca
            # (prefixo) com o restante do modelo em sequência é incerto (lojas costumam omitir a marca) e pede conferência.
            k = len(faltando)
            if exigidos[:k] == faltando and len(exigidos) - k >= 2 and _em_sequencia(exigidos[k:], tt):
                return "incerto", [f"título não cita '{' '.join(faltando)}' (marca/linha), mas traz '{' '.join(exigidos[k:])}': confira o fabricante"]
            return "divergente", [m + " — modelo explícito exige todos os termos"]
        frac = len(faltando) / len(exigidos)
        return ("divergente" if frac > 0.4 else "incerto"), [m]
    avisos: list[str] = []
    if set(tt) & _COMBO:
        return "incerto", ["anúncio parece kit/combo: pode incluir itens extras ou ser outro produto"]
    return "confere", avisos or ["título contém todos os termos do modelo (checagem por título, não é prova)"]
