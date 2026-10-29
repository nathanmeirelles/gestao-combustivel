"""Ramos que antes não eram exercitados: pontuação de entrega/garantia/pagamento, caminho da confiança ALTA, enriquecimento de lojas."""

import json
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

from precos.config import Config
from precos.http import Resultado
from precos.models import Consulta, Evidencia, Parcelamento, Status
from precos.pipeline import Pipeline, _velho
from precos.report import relatorio
from precos.scoring import avaliar_confiabilidade, custo_de, pontuar
from precos.store import Store
from precos.util import agora_brt
from tests.test_scoring import AGORA, loja_boa, of

C = Consulta("Produto X", modelo="produto x")


def crit(o, modo="vista", c=C, lojas=None):
    return pontuar([o], c, lojas or {}, modo)[0].criterios


class EntregaGarantiaPagamentoTest(unittest.TestCase):
    def test_entrega(self):
        gratis = of(frete_valor=D("0"))
        gratis.marcar("frete_valor", Evidencia.CONFIRMADO_DIRETAMENTE)
        self.assertEqual(crit(gratis)["entrega"].nota, 100.0)
        medio = of(preco="1000", frete_valor=D("50"))
        medio.marcar("frete_valor", Evidencia.CONFIRMADO_DIRETAMENTE)
        self.assertEqual(crit(medio)["entrega"].nota, 80.0)                      # 5% do preço => 100 - 4×5
        caro = of(preco="1000", frete_valor=D("300"))
        caro.marcar("frete_valor", Evidencia.CONFIRMADO_DIRETAMENTE)
        self.assertEqual(crit(caro)["entrega"].nota, 0.0)                        # nunca negativa
        self.assertIsNone(crit(of())["entrega"].nota)                            # sem frete = não verificado

    def test_frete_entra_no_total_e_no_ranking(self):
        a = of("a.com.br", "1000", frete_valor=D("200"))
        b = of("b.com.br", "1100", frete_valor=D("0"))
        for x in (a, b):
            x.marcar("frete_valor", Evidencia.CONFIRMADO_DIRETAMENTE)
        r = pontuar([a, b], C, {}, "vista")
        self.assertEqual(r[0].oferta.dominio, "b.com.br")                        # 1100 com frete grátis < 1200 com frete
        self.assertEqual(custo_de(r[0]).total, D("1100.00"))

    def test_garantia(self):
        casos = {"Garantia de 12 meses do fabricante": 100.0, "garantia 6 meses": 60.0, "Garantia de 2 anos": 100.0,
                 "garantia legal": 40.0}
        for texto, esperado in casos.items():
            o = of(garantia=texto)
            o.marcar("garantia", Evidencia.ESTIMADO)
            self.assertEqual(crit(o)["garantia"].nota, esperado, texto)
        self.assertIsNone(crit(of())["garantia"].nota)

    def test_pagamento_a_vista(self):
        self.assertEqual(crit(of())["pagamento"].nota, 70.0)                     # só um preço à vista
        pix = of(preco="1000", preco_pix=D("950"))
        pix.marcar("preco_pix", Evidencia.ESTIMADO)
        self.assertEqual(crit(pix)["pagamento"].nota, 85.0)                      # 5% de desconto => 70 + 15
        grande = of(preco="1000", preco_pix=D("500"))
        grande.marcar("preco_pix", Evidencia.ESTIMADO)
        self.assertEqual(crit(grande)["pagamento"].nota, 100.0)                  # teto de 100

    def test_pagamento_parcelado(self):
        c12 = Consulta("x", parcelas=12)
        curto = of(parcelamentos=[Parcelamento(6, D("166.67"), True)])
        curto.marcar("parcelamentos", Evidencia.ESTIMADO)
        self.assertEqual(crit(curto, "parcelado", c12)["pagamento"].nota, 80.0)  # sem juros, mas só 6x de 12 pedidos
        juros = of(parcelamentos=[Parcelamento(12, D("100"), False)])
        juros.marcar("parcelamentos", Evidencia.ESTIMADO)
        self.assertEqual(crit(juros, "parcelado", c12)["pagamento"].nota, 60.0)  # 20% de juros embutidos => 100 - 2×20
        self.assertIsNone(crit(of(), "parcelado", c12)["pagamento"].nota)        # sem parcelamento legível

    def test_custo_de_falha_alto_se_invariante_violada(self):
        p = pontuar([of()], C, {}, "vista")[0]
        p.custo = None
        with self.assertRaises(RuntimeError):
            custo_de(p)


class ConfiabilidadeRamosTest(unittest.TestCase):
    def test_empresa_com_menos_de_um_ano_gera_sinal_e_zero_pontos_de_tempo(self):
        lj = loja_boa()
        lj.identidade["abertura"] = (AGORA - timedelta(days=100)).date().isoformat()
        c = avaliar_confiabilidade(of(), lj)
        tempo = next(x for x in c.componentes if x.nome.startswith("tempo"))
        self.assertEqual((tempo.pontos, tempo.verificado), (0, True))
        self.assertTrue(any("menos de 1 ano" in s for s in c.sinais_risco))

    def test_data_de_abertura_ilegivel_nao_derruba(self):
        lj = loja_boa()
        lj.identidade["abertura"] = "ontem"
        tempo = next(x for x in avaliar_confiabilidade(of(), lj).componentes if x.nome.startswith("tempo"))
        self.assertFalse(tempo.verificado)

    def test_reputacao_com_data_ilegivel(self):
        lj = loja_boa()
        lj.reputacao_data = "ontem"
        rep = next(x for x in avaliar_confiabilidade(of(), lj).componentes if x.nome.startswith("reputação"))
        self.assertIn("data ilegível", rep.nota)
        self.assertEqual(rep.pontos, 24.0)                                       # sem desconto por antiguidade

    def test_cadastro_editado_a_mao_nao_derruba(self):
        lj = loja_boa()
        lj.tier = "seguríssima"                                                  # inválido
        lj.reputacao_escala = None                                               # inconsistente
        c = avaliar_confiabilidade(of(), lj)
        canal = next(x for x in c.componentes if x.nome.startswith("canal"))
        rep = next(x for x in c.componentes if x.nome.startswith("reputação"))
        self.assertFalse(canal.verificado)
        self.assertFalse(rep.verificado)

    def test_nota_acima_da_escala_e_limitada(self):
        lj = loja_boa()
        lj.reputacao_nota = 15.0                                                 # >10 (edição manual)
        rep = next(x for x in avaliar_confiabilidade(of(), lj).componentes if x.nome.startswith("reputação"))
        self.assertEqual(rep.pontos, 30.0)

    def test_classificado_entre_pessoas_vira_informacao(self):
        c = avaliar_confiabilidade(of("olx.com.br"), None)
        self.assertTrue(any(s.startswith("[INFO] classificado") for s in c.sinais_risco))


# --------------------------------------------------------------------------- pipeline
class FetcherFalso:
    def __init__(self, paginas):
        self.paginas = paginas

    def obter(self, url, fresco=False):
        v = self.paginas[url]
        return v if isinstance(v, Resultado) else Resultado(url, url, 200, v, agora_brt())


class ApiFalsa:
    def __init__(self, cnpj_ok=True, rdap_ok=True):
        self.cnpj_ok, self.rdap_ok, self.chamadas = cnpj_ok, rdap_ok, []

    def get(self, url, params=None, headers=None, recusa_transitoria=False):
        self.chamadas.append(url)
        if "brasilapi" in url:
            if not self.cnpj_ok:
                return 500, None, "HTTP 500"
            return 200, {"razao_social": "LOJA TESTE COMERCIO LTDA", "nome_fantasia": "Loja Teste", "descricao_situacao_cadastral": "ATIVA",
                         "data_inicio_atividade": "2001-03-04", "cnae_fiscal_descricao": "x", "municipio": "SP", "uf": "SP"}, None
        if "rdap" in url:
            if not self.rdap_ok:
                return 403, None, "HTTP 403"
            return 200, {"events": [{"eventAction": "registration", "eventDate": "2005-05-05T00:00:00Z"}]}, None
        raise AssertionError(url)


def pagina(nome, preco, corpo="", gtin=None, vendedor=None):
    ld = {"@context": "https://schema.org", "@type": "Product", "name": nome,
          "offers": {"@type": "Offer", "price": preco, "priceCurrency": "BRL", "availability": "https://schema.org/InStock"}}
    if gtin:
        ld["gtin13"] = gtin
    if vendedor:
        ld["offers"]["seller"] = {"@type": "Organization", "name": vendedor}
    return f"<html><head><title>x</title><script type='application/ld+json'>{json.dumps(ld)}</script></head><body><h1>{nome}</h1>{corpo}</body></html>"


ABERTOS: list[Store] = []


def tearDownModule():
    for s in ABERTOS:
        s.fechar()


def montar(paginas, api=None, **cons):
    cfg = Config(data_dir=Path(tempfile.mkdtemp()))
    store = Store(":memory:")
    ABERTOS.append(store)
    cid = store.nova_consulta(Consulta("Line 6 HX Stomp", modelo="line 6 hx stomp", **cons))
    return Pipeline(cfg, store, fetcher=FetcherFalso(paginas), api=api or ApiFalsa()), store, cid


class EnriquecimentoTest(unittest.TestCase):
    URL = "https://lojateste.com.br/hx-stomp"
    CORPO = "<p>R$ 4.300,00</p><footer>Loja Teste Comércio LTDA — CNPJ 00.000.000/0001-91</footer>"

    def test_consulta_cnpj_e_dominio_uma_vez_e_usa_cache(self):
        api = ApiFalsa()
        pipe, store, cid = montar({self.URL: pagina("Pedaleira Line 6 HX Stomp", "4300.00", self.CORPO)}, api)
        pipe.verificar(cid, self.URL)
        pipe.comparar(cid)
        lj = store.loja("lojateste.com.br")
        self.assertEqual(lj.identidade["razao_social"], "LOJA TESTE COMERCIO LTDA")
        self.assertTrue(lj.identidade["compativel"])
        self.assertEqual(lj.rdap["registrado_em"], "2005-05-05")
        n = len(api.chamadas)
        self.assertEqual(n, 2)                                                   # 1 CNPJ + 1 RDAP
        pipe.comparar(cid)
        self.assertEqual(len(api.chamadas), n)                                   # cadastro recente: sem nova consulta

    def test_cadastro_antigo_e_reconsultado(self):
        api = ApiFalsa()
        pipe, store, cid = montar({self.URL: pagina("Pedaleira Line 6 HX Stomp", "4300.00", self.CORPO)}, api)
        pipe.verificar(cid, self.URL)
        pipe.comparar(cid)
        lj = store.loja("lojateste.com.br")
        velho = (agora_brt() - timedelta(days=45)).isoformat()
        lj.identidade["consultado_em"], lj.rdap["consultado_em"] = velho, velho
        store.salvar_loja(lj)
        antes = len(api.chamadas)
        pipe.comparar(cid)
        self.assertEqual(len(api.chamadas), antes + 2)

    def test_falha_das_apis_vira_aviso_nao_excecao(self):
        pipe, store, cid = montar({self.URL: pagina("Pedaleira Line 6 HX Stomp", "4300.00", self.CORPO)}, ApiFalsa(cnpj_ok=False, rdap_ok=False))
        pipe.verificar(cid, self.URL)
        comp = pipe.comparar(cid)
        self.assertTrue(any("indisponível" in a and "lojateste" in a for a in comp.avisos))
        self.assertIsNone(store.loja("lojateste.com.br").identidade)
        self.assertEqual(len(comp.ranking["vista"]), 1)                          # a pesquisa continua mesmo assim

    def test_falha_vira_cache_negativo_e_nao_martela_o_servico(self):
        api = ApiFalsa(cnpj_ok=False, rdap_ok=False)
        pipe, store, cid = montar({self.URL: pagina("Pedaleira Line 6 HX Stomp", "4300.00", self.CORPO)}, api)
        pipe.verificar(cid, self.URL)
        pipe.comparar(cid)
        chamadas = len(api.chamadas)
        self.assertEqual(chamadas, 2)                                              # 1 CNPJ + 1 RDAP, ambos falharam
        comp = pipe.comparar(cid)
        self.assertEqual(len(api.chamadas), chamadas)                              # 2ª comparação NÃO repete
        self.assertTrue(any("falhou há pouco" in a for a in comp.avisos))
        lj = store.loja("lojateste.com.br")
        antigo = (agora_brt() - timedelta(hours=7)).isoformat()
        lj.cnpj_tentado_em, lj.rdap_tentado_em = antigo, antigo
        store.salvar_loja(lj)
        pipe.comparar(cid)
        self.assertEqual(len(api.chamadas), chamadas + 2)                          # passadas 7 h, tenta de novo

    def test_sucesso_limpa_o_cache_negativo(self):
        api = ApiFalsa()
        pipe, store, cid = montar({self.URL: pagina("Pedaleira Line 6 HX Stomp", "4300.00", self.CORPO)}, api)
        pipe.verificar(cid, self.URL)
        lj = store.loja("lojateste.com.br")
        lj.cnpj_tentado_em = lj.rdap_tentado_em = (agora_brt() - timedelta(hours=7)).isoformat()
        store.salvar_loja(lj)
        pipe.comparar(cid)
        lj = store.loja("lojateste.com.br")
        self.assertIsNone(lj.cnpj_tentado_em)
        self.assertIsNone(lj.rdap_tentado_em)
        self.assertIsNotNone(lj.identidade)

    def test_recente_com_valor_invalido(self):
        from precos.pipeline import ESPERA_APOS_FALHA, _recente
        self.assertFalse(_recente("lixo", ESPERA_APOS_FALHA))
        self.assertFalse(_recente(None, ESPERA_APOS_FALHA))
        self.assertTrue(_recente(agora_brt().isoformat(), ESPERA_APOS_FALHA))

    def test_velho(self):
        self.assertTrue(_velho(None))
        self.assertTrue(_velho("lixo"))
        self.assertTrue(_velho((agora_brt() - timedelta(days=31)).isoformat()))
        self.assertFalse(_velho((agora_brt() - timedelta(days=2)).isoformat()))

    def test_varios_cnpjs_geram_nota(self):
        corpo = "<p>R$ 4.300,00</p><footer>00.000.000/0001-91 e 11.222.333/0001-81</footer>"
        pipe, _, cid = montar({self.URL: pagina("Pedaleira Line 6 HX Stomp", "4300.00", corpo)})
        o = pipe.verificar(cid, self.URL)
        self.assertTrue(any("CNPJs diferentes" in n for n in o.notas) or o.cnpj_pagina)

    def test_inexistente(self):
        pipe, _, _ = montar({})
        with self.assertRaises(KeyError):
            pipe.verificar(999, "https://x.com.br/p")
        with self.assertRaises(KeyError):
            pipe.comparar(999)

    def test_renderizar_usa_o_renderizador(self):
        from unittest import mock
        html = pagina("Pedaleira Line 6 HX Stomp", "4300.00", "<p>R$ 4.300,00</p>")
        pipe, _, cid = montar({})
        with mock.patch("precos.render.renderizar_pagina", return_value=Resultado(self.URL, self.URL, 200, html, agora_brt())) as r:
            o = pipe.verificar(cid, self.URL, renderizar=True)
        r.assert_called_once()
        self.assertEqual(o.status, Status.VERIFICADA)

    def test_redirecionamento_para_outro_dominio_e_cache_geram_alerta(self):
        html = pagina("Pedaleira Line 6 HX Stomp", "4300.00", "<p>R$ 4.300,00</p>")
        res = Resultado(self.URL, "https://outro.com.br/p", 200, html, agora_brt() - timedelta(minutes=5), do_cache=True)
        pipe, _, cid = montar({self.URL: res})
        o = pipe.verificar(cid, self.URL)
        self.assertTrue(any("outro domínio" in a for a in o.alertas))
        self.assertTrue(any("cache local" in a for a in o.alertas))
        self.assertEqual(o.dominio, "outro.com.br")


# --------------------------------------------------------------------------- confiança ALTA (caminho positivo)
class ConfiancaAltaTest(unittest.TestCase):
    GTIN = "7891000100103"
    LOJAS = ["https://alfa.com.br/hx", "https://beta.com.br/hx", "https://gama.com.br/hx"]

    def _cenario(self, extra_corpo=""):
        precos = ["4300.00", "4450.00", "4600.00"]
        paginas = {u: pagina("Pedaleira Line 6 HX Stomp", p, f"<p>R$ {int(float(p)):,}".replace(",", ".") + f",00</p>{extra_corpo}", gtin=self.GTIN, vendedor=f"Loja {i}")
                   for i, (u, p) in enumerate(zip(self.LOJAS, precos, strict=True))}
        pipe, store, cid = montar(paginas)
        for u in self.LOJAS:
            pipe.verificar(cid, u)
            dom = u.split("/")[2]
            lj = store.loja(dom)
            lj.identidade = {"cnpj": "00000000000191", "situacao": "ATIVA", "abertura": "2005-01-01",
                             "razao_social": f"{dom.split('.')[0].upper()} COMERCIO DE INSTRUMENTOS LTDA", "compativel": True,
                             "fonte": "https://brasilapi", "consultado_em": agora_brt().isoformat()}
            lj.rdap = {"idade_dias": 5000, "registrado_em": "2012-01-01", "fonte": "https://rdap", "consultado_em": agora_brt().isoformat()}
            store.salvar_loja(lj)
        return pipe, cid

    def test_tres_lojas_com_tudo_confirmado_chegam_a_alta(self):
        pipe, cid = self._cenario()
        comp = pipe.comparar(cid)
        venc = comp.ranking["vista"][0]
        self.assertEqual(venc.oferta.dominio, "alfa.com.br")
        self.assertEqual(venc.confianca, "alta")
        self.assertTrue(all(v.confirmado for v in venc.essenciais.values()), venc.essenciais)
        txt = relatorio(comp)
        self.assertIn("Confiança da pesquisa: ALTA", txt)
        self.assertIn("foram confirmados e há ≥3 lojas comparáveis", txt)
        self.assertIn("**Melhor compra:", txt)

    def test_pix_so_do_texto_derruba_de_alta(self):
        pipe, cid = self._cenario(extra_corpo="<p>R$ 4.000,00 no Pix</p>")
        txt = relatorio(pipe.comparar(cid))
        self.assertNotIn("Confiança da pesquisa: ALTA", txt)

    def test_duas_lojas_nunca_passam_de_media(self):
        pipe, cid = self._cenario()
        comp = pipe.comparar(cid)
        comp.ranking["vista"] = [p for p in comp.ranking["vista"] if p.oferta.dominio != "gama.com.br"]
        comp.ranking["parcelado"] = []
        txt = relatorio(comp)
        self.assertIn("Confiança da pesquisa: MÉDIA", txt)
        self.assertIn("apenas 2 lojas", txt)

    def test_frete_gratis_e_risco_aparecem_no_relatorio(self):
        pipe, cid = self._cenario()
        comp = pipe.comparar(cid)
        comp.ranking["vista"][0].oferta.frete_valor = D("0")
        comp.ranking["vista"][1].confiab.rotulo = "risco"
        txt = relatorio(comp)
        self.assertIn("grátis", txt)
        self.assertIn("RISCO", txt)


if __name__ == "__main__":
    unittest.main()
