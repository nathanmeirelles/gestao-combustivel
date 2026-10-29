"""Coleta HTTP educada: respeita robots.txt, limita ritmo, usa cache e NUNCA tenta burlar bloqueios."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

from .config import Config
from .robots import Robots
from .util import agora_brt, redigir_url

log = logging.getLogger("precos.http")

MAX_REDIRECTS = 5
_URL_BLOQUEIO = re.compile(r"account-verification|captcha|challenge|/blocked|access-denied|px-captcha|/sorry/", re.I)
_TEXTO_BLOQUEIO = (
    "just a moment", "attention required", "verify you are human", "verifique que você é humano",
    "unusual traffic", "access denied", "are you a robot", "não sou um robô", "pardon our interruption",
    "enable javascript and cookies to continue", "robot check", "detectamos atividade incomum",
)


@dataclass
class _Resposta:
    """Resposta já lida e decodificada (o corpo é consumido em streaming para respeitar o limite de bytes)."""

    status_code: int
    headers: dict
    texto: str


@dataclass
class Resultado:
    url: str
    url_final: str
    status_http: int | None
    texto: str
    coletado_em: datetime
    do_cache: bool = False
    bloqueio: str | None = None      # motivo, quando o site recusou acesso automatizado
    robots_ok: bool = True
    erro: str | None = None

    @property
    def ok(self) -> bool:
        return self.status_http == 200 and not self.bloqueio and self.robots_ok and not self.erro


def detectar_bloqueio(status: int | None, url_final: str, texto: str) -> str | None:
    if status in (401, 403, 407, 429, 451):
        return f"HTTP {status}: o site recusou o acesso automatizado"
    if _URL_BLOQUEIO.search(url_final):
        return "redirecionado para página de verificação/captcha"
    if status == 503 and len(texto) < 60_000:
        return "HTTP 503: provável proteção anti-robô"
    baixo = texto[:60_000].lower()
    if status == 200 and len(texto) < 40_000 and any(m in baixo for m in _TEXTO_BLOQUEIO):
        return "página de verificação anti-robô"
    return None


def _host_proibido(host: str) -> bool:
    """Proteção básica contra URLs que apontam para a rede interna."""
    if host in ("localhost", "") or host.endswith((".local", ".internal", ".localhost")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _descrever_erro(e: Exception) -> str:
    """Causa legível de uma falha de rede, sem expor URL (que pode conter segredos)."""
    if isinstance(e, requests.exceptions.SSLError):
        return "certificado/TLS do site inválido ou incompatível"
    if isinstance(e, requests.exceptions.ProxyError):
        return "host fora da política de acesso do ambiente (proxy recusou)"
    if isinstance(e, requests.exceptions.Timeout):
        return "tempo esgotado"
    if isinstance(e, requests.exceptions.ConnectionError):
        return "conexão recusada ou interrompida"
    return type(e).__name__


def _destino_invalido(url: str) -> str | None:
    """Motivo pelo qual a URL não pode ser acessada (esquema, host ausente ou rede interna); None se ok.

    Vale para a URL inicial E para cada redirecionamento: um site público não pode nos mandar à rede interna.
    Limitação conhecida: não resolve DNS, então um nome público que aponte para IP privado não é detectado.
    """
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return "URL inválida (use http/https)"
    if _host_proibido(p.hostname):
        return "host interno/privado não é permitido"
    return None


class Cache:
    """Cache em disco com expiração; guarda o horário REAL da coleta original."""

    def __init__(self, pasta: Path):
        self.pasta = pasta

    def _arq(self, chave: str) -> Path:
        return self.pasta / (hashlib.sha256(chave.encode()).hexdigest()[:40] + ".json")

    def ler(self, chave: str, ttl_s: float) -> dict | None:
        arq = self._arq(chave)
        try:
            d = json.loads(arq.read_text(encoding="utf-8"))
            if time.time() - d["gravado_epoch"] <= ttl_s:
                return d
        except (OSError, ValueError, KeyError):
            pass
        return None

    def gravar(self, chave: str, dados: dict) -> None:
        try:
            self.pasta.mkdir(parents=True, exist_ok=True)
            self._arq(chave).write_text(json.dumps({**dados, "gravado_epoch": time.time()}), encoding="utf-8")
        except OSError as e:  # cache é otimização: falha não pode derrubar a pesquisa
            log.warning("cache não gravado: %s", e)


class Fetcher:
    def __init__(self, cfg: Config, sessao=None, dormir=time.sleep, relogio=time.monotonic):
        self.cfg = cfg
        self.sessao = sessao or requests.Session()
        self.dormir, self.relogio = dormir, relogio
        self.cache = Cache(cfg.cache_dir)
        self._robots: dict[str, tuple[float, Robots]] = {}
        self._ultimo: dict[str, float] = {}
        self.ultimo_erro: str | None = None      # causa da última falha de rede de _requisitar

    # ---------- robots ----------
    def _robots_do_host(self, esquema: str, host: str) -> Robots:
        agora = self.relogio()
        hit = self._robots.get(host)
        if hit and agora - hit[0] < self.cfg.cache_robots_s:
            return hit[1]
        url, r, motivo_redirect = f"{esquema}://{host}/robots.txt", None, None
        for _ in range(MAX_REDIRECTS + 1):          # robots.txt costuma redirecionar (http→https, www, outro host)
            r = self._requisitar(url, urlsplit(url).hostname or host)
            if r is not None and r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                if _destino_invalido(url):
                    r = None
                    motivo_redirect = "o robots.txt redireciona para um destino não permitido"
                    break
                continue
            break
        if r is None:
            rob = Robots(None, indisponivel=True, motivo=motivo_redirect or f"não foi possível baixar o robots.txt ({self.ultimo_erro or 'falha de rede'})")
        elif r.status_code == 200:
            rob = Robots(r.texto)
        elif 400 <= r.status_code < 500:
            rob = Robots("")                               # sem robots.txt => sem restrições declaradas
        else:
            rob = Robots(None, indisponivel=True, motivo=f"robots.txt respondeu HTTP {r.status_code}")
        self._robots[host] = (agora, rob)
        return rob

    # ---------- núcleo ----------
    def _esperar_vez(self, host: str, atraso_robots: float | None) -> None:
        intervalo = max(self.cfg.intervalo_min_s, atraso_robots or 0.0)
        faltam = self._ultimo.get(host, -1e9) + intervalo - self.relogio()
        if faltam > 0:
            self.dormir(faltam)
        self._ultimo[host] = self.relogio()

    def _requisitar(self, url: str, host: str, atraso: float | None = None) -> _Resposta | None:
        """GET com tentativas só para falhas transitórias. Devolve a resposta já lida, ou None se a rede falhar."""
        self.ultimo_erro = None
        for tentativa in range(1, self.cfg.max_tentativas + 1):
            self._esperar_vez(host, atraso)
            try:
                resp = self.sessao.get(
                    url,
                    headers={"User-Agent": self.cfg.user_agent, "Accept-Language": "pt-BR,pt;q=0.9", "Accept": "text/html,application/xhtml+xml,application/json;q=0.8,*/*;q=0.5"},
                    timeout=(self.cfg.timeout_conexao, self.cfg.timeout_leitura),
                    allow_redirects=False,
                    stream=True,
                )
                corpo = b""
                for pedaco in resp.iter_content(chunk_size=65536):
                    corpo += pedaco
                    if len(corpo) > self.cfg.max_bytes:
                        break
                resp.close()
                corpo = corpo[: self.cfg.max_bytes]
                if resp.status_code in (500, 502, 504) and tentativa < self.cfg.max_tentativas:
                    raise requests.ConnectionError(f"HTTP {resp.status_code}")
                return _Resposta(resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, _decodificar(corpo, resp.headers))
            except requests.RequestException as e:
                self.ultimo_erro = _descrever_erro(e)
                log.warning("falha transitória (%s) em %s [%d/%d]", type(e).__name__, redigir_url(url), tentativa, self.cfg.max_tentativas)
                if tentativa < self.cfg.max_tentativas:
                    self.dormir(2 ** (tentativa - 1))
        return None

    def obter(self, url: str, fresco: bool = False) -> Resultado:
        invalido = _destino_invalido(url)
        if invalido:
            return Resultado(url, url, None, "", agora_brt(), erro=invalido)

        if not fresco:
            c = self.cache.ler(url, self.cfg.cache_pagina_s)
            if c:
                return Resultado(url, c["url_final"], c["status_http"], c["texto"], datetime.fromisoformat(c["coletado_em"]), do_cache=True)

        atual = url
        for _ in range(MAX_REDIRECTS + 1):
            pa = urlsplit(atual)
            host = pa.hostname or ""            # já validado por _destino_invalido
            rob = self._robots_do_host(pa.scheme, host)
            if not rob.permitido(atual, self.cfg.user_agent):
                if rob.indisponivel:     # problema de rede/ambiente, não uma recusa do site
                    return Resultado(url, atual, None, "", agora_brt(), robots_ok=False, erro=f"{rob.motivo} — por cautela não acessamos a página")
                return Resultado(url, atual, None, "", agora_brt(), robots_ok=False, bloqueio="robots.txt do site proíbe a coleta automatizada desta URL")
            resp = self._requisitar(atual, host, rob.atraso(self.cfg.user_agent))
            if resp is None:
                return Resultado(url, atual, None, "", agora_brt(), erro=f"falha de rede após retentativas ({self.ultimo_erro or 'causa desconhecida'})")
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                atual = urljoin(atual, resp.headers["location"])
                invalido = _destino_invalido(atual)
                if invalido:
                    return Resultado(url, atual, resp.status_code, "", agora_brt(), erro=f"redirecionamento recusado: {invalido}")
                if _URL_BLOQUEIO.search(atual):
                    return Resultado(url, atual, resp.status_code, "", agora_brt(), bloqueio="redirecionado para página de verificação/captcha")
                continue
            texto = resp.texto
            quando = agora_brt()
            bloqueio = detectar_bloqueio(resp.status_code, atual, texto)
            erro = None
            if not bloqueio and resp.status_code in (404, 410):
                erro = f"HTTP {resp.status_code}: página não encontrada"
            elif not bloqueio and resp.status_code != 200:
                erro = f"HTTP {resp.status_code}"
            res = Resultado(url, atual, resp.status_code, texto, quando, bloqueio=bloqueio, erro=erro)
            if res.ok:
                self.cache.gravar(url, {"url_final": atual, "status_http": resp.status_code, "texto": texto, "coletado_em": quando.isoformat()})
            return res
        return Resultado(url, atual, None, "", agora_brt(), erro="redirecionamentos demais")


def _decodificar(corpo: bytes, headers) -> str:
    enc = None
    ctype = headers.get("Content-Type") or headers.get("content-type") or ""
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    if m:
        enc = m.group(1)
    else:
        m2 = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", corpo[:4096], re.I)
        enc = m2.group(1).decode() if m2 else "utf-8"
    try:
        return corpo.decode(enc, errors="replace")
    except LookupError:
        return corpo.decode("utf-8", errors="replace")
