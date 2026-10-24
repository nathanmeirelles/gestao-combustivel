"""Orquestra: consulta -> candidatos -> verificação na fonte -> enriquecimento da loja -> pontuação."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from .apis import ApiJson
from .config import Config
from .extract import extrair, loja_pelo_dominio
from .history import Serie, classificar, serie_do_produto
from .http import Fetcher
from .matching import avaliar_identidade
from .models import Consulta, Evidencia, Oferta, Status
from .scoring import Pontuacao, pontuar
from .seller import Identidade, Loja, consultar_cnpj, eh_marketplace, idade_dominio
from .store import Store
from .util import agora_brt, dominio as dominio_de, parse_valor_usuario

log = logging.getLogger("precos.pipeline")
VALIDADE_CADASTRO = timedelta(days=30)


@dataclass
class Comparacao:
    consulta: Consulta
    geradas_em: datetime
    ofertas: list[Oferta]
    lojas: dict[str, Loja]
    ranking: dict[str, list[Pontuacao]]            # 'vista' | 'parcelado'
    nao_verificadas: list[Oferta]
    avisos: list[str] = field(default_factory=list)
    serie: Serie | None = None
    classificacao: tuple[str, str] | None = None


class Pipeline:
    def __init__(self, cfg: Config, store: Store, fetcher: Fetcher | None = None, api: ApiJson | None = None):
        self.cfg, self.store = cfg, store
        self.fetcher = fetcher or Fetcher(cfg)
        self.api = api or ApiJson(cfg)

    # ---------------------------------------------------------------- candidatos (não confirmados)
    def registrar_candidato(self, consulta_id: int, url: str, preco: Decimal | str | None = None, origem: str = "informado manualmente", titulo: str | None = None) -> Oferta:
        """Registra um anúncio visto em resultado de busca. NUNCA é tratado como confirmado."""
        o = Oferta(url=url, dominio=dominio_de(url), status=Status.NAO_CONFIRMADA, coletado_em=agora_brt(), consulta_id=consulta_id,
                   titulo=titulo, preco_exibido=parse_valor_usuario(preco) if preco else None, fonte_dados="snippet", snippet_origem=origem,
                   loja=loja_pelo_dominio(url))
        o.marcar("preco_exibido", Evidencia.NAO_VERIFICADO)
        o.alertas.append("preço visto só em resultado de busca/relato: NÃO confirmado na página da oferta")
        c = self.store.consulta(consulta_id)
        if c and titulo:
            o.identidade_produto, motivos = avaliar_identidade(titulo, c, o, url=url)
            o.notas += motivos
        self.store.salvar_oferta(o)
        self._garantir_loja(o.dominio)
        return o

    # ---------------------------------------------------------------- verificação na fonte
    def verificar(self, consulta_id: int, url: str, fresco: bool = False, renderizar: bool = False) -> Oferta:
        c = self.store.consulta(consulta_id)
        if c is None:
            raise KeyError(f"consulta {consulta_id} não existe")
        anterior = self.store.ultima_da_url(consulta_id, url)

        if renderizar:
            from .render import renderizar_pagina
            res = renderizar_pagina(url, self.cfg, self.fetcher)
        else:
            res = self.fetcher.obter(url, fresco=fresco)

        dom = dominio_de(res.url_final or url)
        o = Oferta(url=url, dominio=dom, status=Status.ERRO, coletado_em=res.coletado_em, consulta_id=consulta_id, loja=loja_pelo_dominio(url))
        if dom != dominio_de(url):
            o.alertas.append(f"redirecionado para outro domínio: {dom}")
        if res.do_cache:
            o.alertas.append(f"dados do cache local, coletados em {res.coletado_em:%d/%m/%Y %H:%M}")

        if not res.ok:
            o.status = Status.BLOQUEADA if res.bloqueio else Status.ERRO
            o.alertas.append(res.bloqueio or res.erro or "falha desconhecida")
            if res.bloqueio and not res.robots_ok:
                o.alertas.append("respeitamos o robots.txt do site: confira a oferta manualmente no navegador")
            elif res.bloqueio:
                o.alertas.append("não contornamos bloqueios anti-robô: confira a oferta manualmente no navegador")
            elif res.erro:
                o.alertas.append("falha técnica (não é recusa do site): pode ser tentada novamente mais tarde")
            if anterior and anterior.snippet_origem:      # preserva o que se sabia, ainda como NÃO confirmado
                o.preco_exibido, o.titulo, o.snippet_origem, o.fonte_dados = anterior.preco_exibido, anterior.titulo, anterior.snippet_origem, "snippet"
                o.identidade_produto = anterior.identidade_produto
                o.marcar("preco_exibido", Evidencia.NAO_VERIFICADO)
            self.store.salvar_oferta(o)
            self._garantir_loja(o.dominio)
            return o

        ex = extrair(res.texto, res.url_final)
        o.titulo, o.marca, o.sku, o.gtin, o.titulos_alt = ex.titulo, ex.marca, ex.sku, ex.gtin, ex.titulos
        o.vendedor, o.condicao, o.disponibilidade = ex.vendedor, ex.condicao, ex.disponibilidade
        o.loja = ex.loja or o.loja
        o.garantia = ex.garantia
        o.alertas += ex.avisos
        o.fonte_dados = ex.fonte_preco or "texto"
        if ex.cnpjs:
            o.cnpj_pagina = ex.cnpjs[0]
            if len(ex.cnpjs) > 1:
                o.notas.append(f"{len(ex.cnpjs)} CNPJs diferentes na página (ex.: rodapé de marketplace); considerado o primeiro")

        if ex.preco is not None:
            o.preco_exibido = ex.preco
            o.marcar("preco_exibido", Evidencia.CONFIRMADO_DIRETAMENTE if ex.preco_corroborado else Evidencia.ESTIMADO)
            o.status = Status.VERIFICADA
        else:
            o.status = Status.ERRO
            o.alertas.append("preço não encontrado em dados estruturados da página: oferta NÃO verificada (sem parser específico por site; confira manualmente)")
            if anterior and anterior.snippet_origem:
                o.preco_exibido, o.snippet_origem, o.fonte_dados = anterior.preco_exibido, anterior.snippet_origem, "snippet"
                o.marcar("preco_exibido", Evidencia.NAO_VERIFICADO)
        if ex.preco_pix:
            o.preco_pix, o.meio_vista = ex.preco_pix, ex.meio_vista
            o.marcar("preco_pix", Evidencia.ESTIMADO)
        if ex.parcelamentos:
            o.parcelamentos = ex.parcelamentos
            o.marcar("parcelamentos", Evidencia.ESTIMADO)
        if ex.disponibilidade:
            o.marcar("disponibilidade", Evidencia.CONFIRMADO_DIRETAMENTE if ex.fonte_preco == "json-ld" else Evidencia.ESTIMADO)
        if ex.vendedor:
            o.marcar("vendedor", Evidencia.CONFIRMADO_DIRETAMENTE)
        if ex.condicao:
            o.marcar("condicao", Evidencia.CONFIRMADO_DIRETAMENTE)
        if ex.garantia:
            o.marcar("garantia", Evidencia.ESTIMADO)
        if ex.disponibilidade == "sem_estoque" and o.status == Status.VERIFICADA:
            o.status = Status.INDISPONIVEL

        o.identidade_produto, motivos = avaliar_identidade(o.titulo, c, o, url=res.url_final, titulos_extra=ex.titulos)
        o.notas += motivos
        o.marcar("produto", {"confere": Evidencia.CONFIRMADO_INDEPENDENTE if (c.gtin and o.gtin == c.gtin) else Evidencia.ESTIMADO,
                             "divergente": Evidencia.DIVERGENTE}.get(o.identidade_produto, Evidencia.NAO_VERIFICADO))
        if eh_marketplace(o.dominio) and not o.vendedor:
            o.alertas.append("marketplace: vendedor real NÃO identificado nesta página")
        o.hash_conteudo = hashlib.sha256(f"{o.preco_exibido}|{o.preco_pix}|{o.disponibilidade}|{[(p.n, str(p.valor)) for p in o.parcelamentos]}".encode()).hexdigest()[:16]

        self.store.salvar_oferta(o)
        self._garantir_loja(o.dominio)
        return o

    def _garantir_loja(self, dom: str) -> Loja:
        loja = self.store.loja(dom)
        if loja is None:
            loja = Loja(dom)
            self.store.salvar_loja(loja)
        return loja

    # ---------------------------------------------------------------- enriquecimento (CNPJ, domínio) e consistência
    def enriquecer(self, ofertas: list[Oferta]) -> list[str]:
        avisos: list[str] = []
        for dom in sorted({o.dominio for o in ofertas if o.status == Status.VERIFICADA}):
            loja = self._garantir_loja(dom)
            if eh_marketplace(dom):
                continue  # CNPJ/domínio do marketplace NÃO descrevem o vendedor do anúncio
            ref = next((o for o in ofertas if o.dominio == dom and o.status == Status.VERIFICADA), None)
            cnpj = loja.cnpj or (ref.cnpj_pagina if ref else None)
            alterou = False
            if cnpj and (loja.identidade is None or loja.identidade.get("cnpj") != cnpj or _velho(loja.identidade.get("consultado_em"))):
                ident, erro = consultar_cnpj(cnpj, self.api)
                if ident:
                    loja.identidade, alterou = ident.to_dict(), True
                else:
                    avisos.append(f"{dom}: {erro}")
            if loja.identidade:
                ident = Identidade.from_dict(loja.identidade)
                comp = ident.compativel_com(loja.nome, ref.loja if ref else None, dom)
                if loja.identidade.get("compativel") != comp:
                    loja.identidade["compativel"], alterou = comp, True
            if loja.rdap is None or _velho(loja.rdap.get("consultado_em")):
                rd, erro = idade_dominio(dom, self.api)
                if rd:
                    loja.rdap, alterou = rd, True
                elif erro and "só é verificada" not in erro:
                    avisos.append(f"{dom}: {erro}")
            if alterou:
                self.store.salvar_loja(loja)
        return avisos

    @staticmethod
    def consistencia_gtin(ofertas: list[Oferta]) -> None:
        """O mesmo GTIN em ≥2 lojas distintas, com títulos compatíveis, identifica o produto por fonte independente."""
        grupos: dict[str, list[Oferta]] = {}
        for o in ofertas:
            if o.status == Status.VERIFICADA and o.gtin and o.identidade_produto == "confere":
                grupos.setdefault(o.gtin, []).append(o)
        for gtin, grupo in grupos.items():
            if len({o.dominio for o in grupo}) >= 2:
                for o in grupo:
                    o.marcar("produto", Evidencia.CONFIRMADO_INDEPENDENTE)
                    if not any("GTIN" in n for n in o.notas):
                        o.notas.append(f"GTIN {gtin} coincide em {len({x.dominio for x in grupo})} lojas distintas")

    # ---------------------------------------------------------------- comparação
    def comparar(self, consulta_id: int) -> Comparacao:
        c = self.store.consulta(consulta_id)
        if c is None:
            raise KeyError(f"consulta {consulta_id} não existe")
        ofertas = self.store.ultimas_ofertas(consulta_id)
        avisos = self.enriquecer(ofertas)
        self.consistencia_gtin(ofertas)
        lojas = self.store.lojas(sorted({o.dominio for o in ofertas}))
        modos = [c.pagamento] if c.pagamento in ("vista", "parcelado") else ["vista", "parcelado"]
        ranking = {m: pontuar(ofertas, c, lojas, m, self.cfg.desatualizada_apos_h) for m in modos}
        serie = serie_do_produto(self.store, c)
        melhor = next((p for p in ranking.get("vista", []) if p.elegivel and p.oferta.preco_exibido), None)
        classificacao = classificar(melhor.oferta.preco_exibido, serie) if melhor else None
        return Comparacao(c, agora_brt(), ofertas, lojas, ranking, [o for o in ofertas if o.status != Status.VERIFICADA], avisos, serie, classificacao)


def _velho(iso: str | None) -> bool:
    if not iso:
        return True
    try:
        return agora_brt() - datetime.fromisoformat(iso) > VALIDADE_CADASTRO
    except ValueError:
        return True
