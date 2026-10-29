"""Renderização opcional com Chromium (Playwright) para páginas que só exibem o preço via JavaScript.

Mesmas regras do fetcher: robots.txt, ritmo por host, bloqueio de hosts internos, User-Agent honesto. Sem camuflagem,
sem resolver captcha: se o site pedir verificação, o resultado é 'bloqueada'.
Requer `pip install playwright` e um Chromium (PRECOS_CHROMIUM_PATH se o Playwright não encontrar o seu).
"""

from __future__ import annotations

import contextlib
from urllib.parse import urlsplit

from .config import Config
from .http import Fetcher, Resultado, _destino_invalido, detectar_bloqueio
from .util import agora_brt


def _navegar(url: str, ua: str, chromium_path: str | None, timeout_ms: int, bloquear_internos: bool = True) -> tuple[str, str, int | None]:
    """Só a parte do navegador: devolve (html, url_final, status). Levanta ImportError/PwError.

    `bloquear_internos`: aborta qualquer requisição (inclusive redirecionamentos e sub-recursos) a hosts internos.
    Só desligue em testes locais.
    """
    from playwright.sync_api import Error as PwError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        nav = pw.chromium.launch(executable_path=chromium_path) if chromium_path else pw.chromium.launch()
        try:
            ctx = nav.new_context(user_agent=ua, locale="pt-BR", timezone_id="America/Sao_Paulo")
            if bloquear_internos:
                ctx.route("**/*", lambda rota: rota.abort() if _destino_invalido(rota.request.url) else rota.continue_())
            pag = ctx.new_page()
            resp = pag.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            with contextlib.suppress(PwError):
                pag.wait_for_load_state("networkidle", timeout=8_000)   # melhor esforço: scripts que preenchem o preço
            return pag.content(), pag.url, (resp.status if resp else None)
        finally:
            nav.close()


def renderizar_pagina(url: str, cfg: Config, fetcher: Fetcher, timeout_ms: int = 30_000) -> Resultado:
    p = urlsplit(url)
    invalido = _destino_invalido(url)
    if invalido:
        return Resultado(url, url, None, "", agora_brt(), erro=invalido)
    rob = fetcher._robots_do_host(p.scheme, p.hostname or "")
    if not rob.permitido(url, cfg.user_agent):
        if rob.indisponivel:
            return Resultado(url, url, None, "", agora_brt(), robots_ok=False, erro=f"{rob.motivo} — por cautela não acessamos a página")
        return Resultado(url, url, None, "", agora_brt(), robots_ok=False, bloqueio="robots.txt do site proíbe a coleta automatizada desta URL")
    fetcher._esperar_vez(p.hostname or "", rob.atraso(cfg.user_agent))
    try:
        html, final, status = _navegar(url, cfg.user_agent, cfg.chromium_path, timeout_ms)
    except ImportError:
        return Resultado(url, url, None, "", agora_brt(), erro="playwright não instalado (pip install playwright)")
    except Exception as e:  # PwError e falhas ao iniciar o navegador
        return Resultado(url, url, None, "", agora_brt(), erro=f"falha na renderização: {str(e).splitlines()[0][:140]}")
    bloqueio = detectar_bloqueio(status, final, html)
    return Resultado(url, final, status, html, agora_brt(), bloqueio=bloqueio, erro=None if status == 200 or bloqueio else f"HTTP {status}")
