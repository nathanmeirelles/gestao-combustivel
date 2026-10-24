"""Consultas JSON a APIs públicas (CNPJ, CEP, RDAP, busca). Retentativas só em falha transitória; segredos nunca vão ao log."""

from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit

import requests

from .config import Config
from .util import redigir_url

log = logging.getLogger("precos.apis")


class ApiJson:
    INTERVALO_S = 1.0          # espera mínima entre chamadas ao mesmo host
    ESPERA_RECUSA_S = 6.0      # pausa antes de repetir após 403/429 em API pública (limitador de taxa)

    def __init__(self, cfg: Config, sessao=None, dormir=time.sleep, relogio=time.monotonic):
        self.cfg, self.sessao, self.dormir, self.relogio = cfg, sessao or requests.Session(), dormir, relogio
        self._ultimo: dict[str, float] = {}

    def _esperar_vez(self, host: str) -> None:
        faltam = self._ultimo.get(host, -1e9) + self.INTERVALO_S - self.relogio()
        if faltam > 0:
            self.dormir(faltam)
        self._ultimo[host] = self.relogio()

    def get(self, url: str, params: dict | None = None, headers: dict | None = None, recusa_transitoria: bool = False) -> tuple[int | None, dict | list | None, str | None]:
        """(status, json, erro). Nunca levanta exceção de rede.

        `recusa_transitoria=True`: só para APIs públicas feitas para consulta programática (ex.: RDAP), cujo 403/429
        costuma ser limitador de taxa. Espera mais e repete; não troca de identidade nem tenta burlar nada.
        """
        h = {"User-Agent": self.cfg.user_agent, "Accept": "application/json", **(headers or {})}
        host = urlsplit(url).hostname or ""
        ultimo_erro = None
        for tentativa in range(1, self.cfg.max_tentativas + 1):
            try:
                self._esperar_vez(host)
                r = self.sessao.get(url, params=params, headers=h, timeout=(self.cfg.timeout_conexao, self.cfg.timeout_leitura))
                recusa = recusa_transitoria and r.status_code in (403, 429)
                if (r.status_code in (429, 500, 502, 503, 504) or recusa) and tentativa < self.cfg.max_tentativas:
                    if recusa:
                        self.dormir(self.ESPERA_RECUSA_S)
                    raise requests.ConnectionError(f"HTTP {r.status_code}")
                try:
                    corpo = r.json()
                except ValueError:
                    corpo = None
                return r.status_code, corpo, None if r.status_code < 400 else f"HTTP {r.status_code}"
            except requests.RequestException as e:
                ultimo_erro = f"{type(e).__name__}"
                log.warning("falha transitória em %s [%d/%d]: %s", redigir_url(url), tentativa, self.cfg.max_tentativas, ultimo_erro)
                if tentativa < self.cfg.max_tentativas:
                    self.dormir(2 ** (tentativa - 1))
        return None, None, f"falha de rede ({ultimo_erro})"
