"""Configuração por variáveis de ambiente. Chaves nunca aparecem em repr/logs."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_UA = "agente-precos/0.1 (pesquisa de precos pessoal; respeita robots.txt)"
RAIZ = Path(__file__).resolve().parent.parent


def _carregar_dotenv(caminho: Path) -> None:
    """Lê KEY=VALUE de um .env local sem sobrescrever o que já está no ambiente."""
    if not caminho.is_file():
        return
    for bruta in caminho.read_text(encoding="utf-8").splitlines():
        linha = bruta.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        k, _, v = linha.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _float(nome: str, padrao: float) -> float:
    try:
        return float(os.environ.get(nome, padrao))
    except ValueError:
        return padrao


@dataclass(frozen=True)
class Config:
    data_dir: Path
    user_agent: str = DEFAULT_UA
    timeout_conexao: float = 10.0
    timeout_leitura: float = 25.0
    max_tentativas: int = 3          # 1 tentativa + 2 retentativas (só erros transitórios)
    intervalo_min_s: float = 2.0     # espera mínima entre requisições ao mesmo host
    cache_pagina_s: int = 1800       # páginas de produto: 30 min
    cache_robots_s: int = 86400
    desatualizada_apos_h: float = 6.0
    max_bytes: int = 3_000_000
    brave_key: str | None = field(default=None, repr=False)
    google_key: str | None = field(default=None, repr=False)
    google_cx: str | None = field(default=None, repr=False)
    chromium_path: str | None = None     # executável do Chromium, se o Playwright não achar o seu
    ntfy_topic: str | None = field(default=None, repr=False)     # o nome do tópico funciona como senha: use um aleatório e longo
    ntfy_server: str = "https://ntfy.sh"
    ntfy_token: str | None = field(default=None, repr=False)
    webhook_url: str | None = field(default=None, repr=False)
    telegram_token: str | None = field(default=None, repr=False)
    telegram_chat: str | None = field(default=None, repr=False)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "precos.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @classmethod
    def from_env(cls, carregar_dotenv: bool = True) -> Config:
        if carregar_dotenv:
            _carregar_dotenv(RAIZ / ".env")
        data_dir = Path(os.environ.get("PRECOS_DATA_DIR") or RAIZ / "data")
        return cls(
            data_dir=data_dir,
            user_agent=os.environ.get("PRECOS_USER_AGENT") or DEFAULT_UA,
            intervalo_min_s=_float("PRECOS_INTERVALO_S", 2.0),
            desatualizada_apos_h=_float("PRECOS_DESATUALIZADA_H", 6.0),
            brave_key=os.environ.get("BRAVE_API_KEY") or None,
            google_key=os.environ.get("GOOGLE_CSE_KEY") or None,
            google_cx=os.environ.get("GOOGLE_CSE_CX") or None,
            chromium_path=os.environ.get("PRECOS_CHROMIUM_PATH") or None,
            ntfy_topic=os.environ.get("NTFY_TOPIC") or None,
            ntfy_server=(os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/"),
            ntfy_token=os.environ.get("NTFY_TOKEN") or None,
            webhook_url=os.environ.get("ALERT_WEBHOOK_URL") or None,
            telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat=os.environ.get("TELEGRAM_CHAT_ID") or None,
        )
