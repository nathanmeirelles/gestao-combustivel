"""Notificações opt-in (ntfy, webhook genérico, Telegram). Nada é enviado sem `--notificar` e canal configurado.

Segurança: o token do Telegram vai no CAMINHO da URL e o webhook pode conter segredo; por isso este módulo NUNCA
registra URLs nem mensagens de exceção do `requests` (que as incluem) — só o nome do canal e o tipo do erro.
"""

from __future__ import annotations

import logging

import requests

from .config import Config

log = logging.getLogger("precos.notify")
TIMEOUT = (10.0, 20.0)


def canais_configurados(cfg: Config) -> list[str]:
    nomes = []
    if cfg.ntfy_topic:
        nomes.append("ntfy")
    if cfg.webhook_url:
        nomes.append("webhook")
    if cfg.telegram_token and cfg.telegram_chat:
        nomes.append("telegram")
    return nomes


def notificar(cfg: Config, titulo: str, mensagem: str, sessao=None) -> list[tuple[str, bool, str | None]]:
    """Envia a todos os canais configurados. Retorna [(canal, ok, erro)]; nunca levanta exceção de rede."""
    s = sessao or requests
    ua = {"User-Agent": cfg.user_agent}
    resultados: list[tuple[str, bool, str | None]] = []

    def tentar(canal: str, enviar) -> None:
        try:
            r = enviar()
            ok = 200 <= r.status_code < 300
            resultados.append((canal, ok, None if ok else f"HTTP {r.status_code}"))
        except requests.RequestException as e:
            log.warning("falha ao notificar via %s (%s)", canal, type(e).__name__)
            resultados.append((canal, False, type(e).__name__))

    if cfg.ntfy_topic:
        headers = dict(ua)
        if cfg.ntfy_token:
            headers["Authorization"] = f"Bearer {cfg.ntfy_token}"
        # publicação por JSON: aceita UTF-8 (acentos) e mantém o tópico fora da URL
        tentar("ntfy", lambda: s.post(cfg.ntfy_server, json={"topic": cfg.ntfy_topic, "title": titulo, "message": mensagem}, headers=headers, timeout=TIMEOUT))
    if cfg.webhook_url:
        tentar("webhook", lambda: s.post(cfg.webhook_url, json={"origem": "agente-precos", "titulo": titulo, "mensagem": mensagem}, headers=ua, timeout=TIMEOUT))
    if cfg.telegram_token and cfg.telegram_chat:
        tentar("telegram", lambda: s.post(f"https://api.telegram.org/bot{cfg.telegram_token}/sendMessage",
                                          json={"chat_id": cfg.telegram_chat, "text": f"{titulo}\n{mensagem}", "disable_web_page_preview": False},
                                          headers=ua, timeout=TIMEOUT))
    return resultados
