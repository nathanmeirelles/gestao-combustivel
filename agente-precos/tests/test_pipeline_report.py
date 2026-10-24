import json
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

from precos.config import Config
from precos.extract import extrair
from precos.http import Resultado
from precos.matching import avaliar_identidade
from precos.models import Consulta, Evidencia, Status
from precos.pipeline import Pipeline
from precos.report import relatorio
from precos.store import Store
from precos.util import agora_brt


class FetcherFalso:
    def __init__(self, paginas):
        self.paginas, self.pedidos = paginas, []

    def obter(self, url, fresco=False):
        self.pedidos.append(url)
        v = self.paginas[url]
        if isinstance(v, Resultado):
            return v
        return Resultado(url, url, 200, v, agora_brt())


class ApiOffline:
    def get(self, *a, **k):
        return None, None, "offline"


def html(nome, preco, disp="InStock", corpo="", gtin=None, cnd=None, h1=None, title="Loja"):
    ld = {"@context": "https://schema.org", "@type": "Product", "name": nome, "sku": "S1",
          "offers": {"@type": "Offer", "price": preco, "priceCurrency": "BRL", "availability": f"https://schema.org/{disp}"}}
    if gtin:
        ld["gtin13"] = gtin
    if cnd:
        ld["offers"]["itemCondition"] = f"https://schema.org/{cnd}"
    return (f"<html><head><title>{title}</title><script type='application/ld+json'>{json.dumps(ld)}</script></head>"
            f"<body><h1>{h1 or nome}</h1>{corpo}</body></html>")


ABERTOS = []


def tearDownModule():
    for st in ABERTOS:
        st.fechar()


def montar(paginas, **cons):
    cfg = Config(data_dir=Path(tempfile.mkdtemp()))
    store = Store(":memory:")
    c = Consulta("Line 6 HX Stomp", modelo="line 6 hx stomp", parcelas=12, **cons)
    cid = store.nova_consulta(c)
    pipe = Pipeline(cfg, store, fetcher=FetcherFalso(paginas), api=ApiOffline())
    ABERTOS.append(store)
    return pipe, store, cid


U_HARMONIA = "https://harmonia.com.br/pedaleira-line-6-hx-stomp-1.html"
U_PEDALBOARD = "https://pedalboard.com.br/produtos/line-6-hx-stomp-multi-effects-semi-nova-tr3u4/"
U_LOJA3 = "https://terceira.com.br/line6-hx-stomp/p"
U_AMAZONLIKE = "https://www.amazon.com.br/dp/B07J2LV1N1"


class RegressoesTest(unittest.TestCase):
    def test_a_vista_sem_a_palavra_pix_e_reconhecido_com_meio_nao_especificado(self):
        corpo = "<p>R$ 9.190,00 R$ 8.730,50 à vista ou R$ 9.190,00 parcelado em até 10x de R$ 919,00 sem juros</p>"
        ex = extrair(html("Pedaleira Line 6 HX Stomp", "9190.00", corpo=corpo), U_HARMONIA)
        self.assertEqual((ex.preco_pix, ex.meio_vista), (D("8730.50"), "não especificado"))
        p10 = next(p for p in ex.parcelamentos if p.n == 10)
        self.assertEqual((p10.valor, p10.sem_juros), (D("919.00"), True))

    def test_meio_pix_e_boleto(self):
        a = extrair(html("X", "1000.00", corpo="<p>R$ 1.000,00</p><p>R$ 950,00 à vista no Pix</p>"), U_HARMONIA)
        b = extrair(html("X", "1000.00", corpo="<p>R$ 1.000,00</p><p>R$ 960,00 à vista no boleto</p>"), U_HARMONIA)
        self.assertEqual((a.meio_vista, b.meio_vista), ("pix", "boleto"))

    def test_url_semi_nova_contra_titulo_lacrada_e_contradicao(self):
        c = Consulta("Line 6 HX Stomp", modelo="line 6 hx stomp")
        status, motivos = avaliar_identidade("Line 6 HX STOMP Multi Effects | Lacrada", c, url=U_PEDALBOARD, titulos_extra=["HX Stomp"])
        self.assertEqual(status, "divergente")
        self.assertIn("CONTRADITÓRIOS", " ".join(motivos))

    def test_url_semi_nova_sozinha_tambem_exclui(self):
        c = Consulta("Line 6 HX Stomp", modelo="line 6 hx stomp")
        self.assertEqual(avaliar_identidade("Line 6 HX STOMP Multi Effects", c, url=U_PEDALBOARD)[0], "divergente")

    def test_url_limpa_nao_gera_falso_positivo(self):
        c = Consulta("Line 6 HX Stomp", modelo="line 6 hx stomp")
        self.assertEqual(avaliar_identidade("Pedaleira Line 6 HX Stomp", c, url=U_HARMONIA)[0], "confere")

    def test_semi_nova_nao_conta_como_sinal_de_novo(self):
        c = Consulta("Line 6 HX Stomp", modelo="line 6 hx stomp")
        s, m = avaliar_identidade("Line 6 HX Stomp semi nova", c)
        self.assertEqual(s, "divergente")
        self.assertNotIn("CONTRADIT", " ".join(m))


class PipelineTest(unittest.TestCase):
    def test_verificar_registra_evidencias_e_horario(self):
        corpo = "<p>R$ 4.299,00</p><p>R$ 4.085,05 no Pix</p><p>12x de R$ 358,25 sem juros</p>"
        pipe, store, cid = montar({U_HARMONIA: html("Pedaleira Line 6 HX Stomp", "4299.00", corpo=corpo)})
        o = pipe.verificar(cid, U_HARMONIA)
        self.assertEqual(o.status, Status.VERIFICADA)
        self.assertEqual(o.evid("preco_exibido"), Evidencia.CONFIRMADO_DIRETAMENTE)
        self.assertEqual(o.evid("preco_pix"), Evidencia.ESTIMADO)
        self.assertEqual((o.preco_pix, o.meio_vista), (D("4085.05"), "pix"))
        self.assertEqual(o.identidade_produto, "confere")
        self.assertIsNotNone(o.coletado_em.tzinfo)
        self.assertEqual(store.oferta(o.id).preco_exibido, D("4299.00"))

    def test_pedalboard_com_contradicao_nao_confere_e_fica_fora(self):
        pagina = html("Line 6 HX STOMP Multi Effects | Lacrada", "4599.00", corpo="<p>R$ 4.599,00</p>", title="HX Stomp")
        pipe, store, cid = montar({U_PEDALBOARD: pagina})
        o = pipe.verificar(cid, U_PEDALBOARD)
        self.assertEqual(o.identidade_produto, "divergente")
        self.assertEqual(o.evid("produto"), Evidencia.DIVERGENTE)
        comp = pipe.comparar(cid)
        self.assertFalse(comp.ranking["vista"][0].elegivel)

    def test_sem_estoque_vira_indisponivel(self):
        pipe, _, cid = montar({U_HARMONIA: html("Pedaleira Line 6 HX Stomp", "4299.00", disp="OutOfStock", corpo="<p>R$ 4.299,00</p>")})
        self.assertEqual(pipe.verificar(cid, U_HARMONIA).status, Status.INDISPONIVEL)

    def test_bloqueio_preserva_snippet_como_nao_confirmado(self):
        bloq = Resultado(U_HARMONIA, U_HARMONIA, 403, "", agora_brt(), bloqueio="HTTP 403: o site recusou o acesso automatizado")
        pipe, store, cid = montar({U_HARMONIA: bloq})
        pipe.registrar_candidato(cid, U_HARMONIA, "9190", "WebSearch", "Pedaleira Line 6 HX Stomp")
        o = pipe.verificar(cid, U_HARMONIA)
        self.assertEqual(o.status, Status.BLOQUEADA)
        self.assertEqual(o.preco_exibido, D("9190"))
        self.assertEqual(o.evid("preco_exibido"), Evidencia.NAO_VERIFICADO)
        self.assertEqual(pipe.comparar(cid).ranking["vista"], [])          # nunca entra no ranking

    def test_falha_de_rede_nao_e_rotulada_como_bloqueio_do_site(self):
        falha = Resultado(U_HARMONIA, U_HARMONIA, None, "", agora_brt(), robots_ok=False, erro="não foi possível baixar o robots.txt (falha de rede)")
        pipe, _, cid = montar({U_HARMONIA: falha})
        o = pipe.verificar(cid, U_HARMONIA)
        self.assertEqual(o.status, Status.ERRO)
        self.assertFalse(any("não contornamos" in a for a in o.alertas))
        self.assertTrue(any("falha técnica" in a for a in o.alertas))

    def test_robots_proibindo_gera_mensagem_propria(self):
        proib = Resultado(U_HARMONIA, U_HARMONIA, None, "", agora_brt(), robots_ok=False, bloqueio="robots.txt do site proíbe a coleta automatizada desta URL")
        pipe, _, cid = montar({U_HARMONIA: proib})
        o = pipe.verificar(cid, U_HARMONIA)
        self.assertEqual(o.status, Status.BLOQUEADA)
        self.assertTrue(any("respeitamos o robots.txt" in a for a in o.alertas))

    def test_pagina_sem_preco_estruturado_preserva_snippet(self):
        pipe, store, cid = montar({U_AMAZONLIKE: "<html><head><title>Line 6 HX Stomp</title></head><body>R$ 9.926,68</body></html>"})
        pipe.registrar_candidato(cid, U_AMAZONLIKE, "9926.68", "WebSearch", "Line 6 HX Stomp")
        o = pipe.verificar(cid, U_AMAZONLIKE)
        self.assertEqual(o.status, Status.ERRO)
        self.assertEqual((o.preco_exibido, o.evid("preco_exibido")), (D("9926.68"), Evidencia.NAO_VERIFICADO))

    def test_gtin_igual_em_duas_lojas_confirma_produto_por_fonte_independente(self):
        gtin = "7891000100103"
        pipe, store, cid = montar({
            U_HARMONIA: html("Pedaleira Line 6 HX Stomp", "4300.00", corpo="<p>R$ 4.300,00</p>", gtin=gtin),
            U_LOJA3: html("Line6 HX Stomp Multi-Efeitos", "4400.00", corpo="<p>R$ 4.400,00</p>", gtin=gtin),
        })
        pipe.verificar(cid, U_HARMONIA)
        pipe.verificar(cid, U_LOJA3)
        comp = pipe.comparar(cid)
        self.assertTrue(all(p.oferta.evid("produto") == Evidencia.CONFIRMADO_INDEPENDENTE for p in comp.ranking["vista"]))

    def test_marketplace_nao_herda_cnpj_nem_dominio(self):
        pagina = html("Line 6 HX Stomp", "4300.00", corpo="<p>R$ 4.300,00</p><footer>CNPJ 00.000.000/0001-91</footer>")
        pipe, store, cid = montar({"https://produto.mercadolivre.com.br/MLB-1-x": pagina})
        pipe.verificar(cid, "https://produto.mercadolivre.com.br/MLB-1-x")
        pipe.comparar(cid)
        self.assertIsNone(store.loja("produto.mercadolivre.com.br").identidade)


class RelatorioTest(unittest.TestCase):
    def _cenario(self):
        pa = html("Pedaleira Line 6 HX Stomp", "4300.00", corpo="<p>R$ 4.300,00</p><p>R$ 4.085,00 no Pix</p><p>12x de R$ 358,34 sem juros</p>")
        pb = html("Pedaleira Line 6 HX Stomp", "4600.00", corpo="<p>R$ 4.600,00</p><p>6x de R$ 840,00 com juros</p>")
        pc = html("Pedaleira Line 6 HX Stomp", "9200.00", corpo="<p>R$ 9.200,00</p>")
        pipe, store, cid = montar({"https://a.com.br/p": pa, "https://b.com.br/p": pb, "https://c.com.br/p": pc})
        for u in ("https://a.com.br/p", "https://b.com.br/p", "https://c.com.br/p"):
            pipe.verificar(cid, u)
        pipe.registrar_candidato(cid, "https://d.com.br/p", "100", "WebSearch", "Line 6 HX Stomp")
        return pipe, store, cid

    def test_relatorio_tem_secoes_e_nao_recomenda_o_candidato_nao_confirmado(self):
        pipe, store, cid = self._cenario()
        txt = relatorio(pipe.comparar(cid))
        for sec in ("## A. Veredito", "## B. Melhores ofertas", "## C. Links diretos", "## D. Comparação de pagamento", "## E. Análise de confiabilidade", "## F. Conclusão"):
            self.assertIn(sec, txt)
        self.assertIn("Melhor compra: A", txt)
        self.assertIn("https://a.com.br/p", txt)
        self.assertIn("NÃO confirmado", txt)                       # candidato barato aparece só como não confirmado
        veredito = txt.split("## B.")[0]
        self.assertNotIn("d.com.br", veredito)
        self.assertIn("sem frete", txt.lower())
        self.assertIn("nenhum cupom ou cashback foi verificado", txt.lower())
        self.assertIn("Nenhuma compra foi feita", txt)

    def test_confianca_nao_e_alta_sem_essenciais_confirmados(self):
        pipe, _, cid = self._cenario()
        txt = relatorio(pipe.comparar(cid))
        self.assertNotIn("Confiança da pesquisa: ALTA", txt)       # Pix só do texto, produto só por título, lojas sem verificação

    def test_com_juros_e_sinalizado(self):
        pipe, _, cid = self._cenario()
        txt = relatorio(pipe.comparar(cid))
        self.assertIn("parcelamento com juros embutidos de R$ 440,00", txt)
        self.assertIn("não confunda parcela menor", txt)

    def test_outlier_barato_com_tres_lojas_e_marcado(self):
        pipe, store, cid = self._cenario()
        pipe.verificar(cid, "https://c.com.br/p")
        pd_ = html("Pedaleira Line 6 HX Stomp", "900.00", corpo="<p>R$ 900,00</p>")
        pipe.fetcher.paginas["https://golpe.com.br/p"] = pd_
        pipe.verificar(cid, "https://golpe.com.br/p")
        txt = relatorio(pipe.comparar(cid))
        self.assertIn("abaixo da mediana", txt)
        self.assertNotIn("Melhor compra: golpe", txt)

    def test_loja_vencedora_sem_verificacao_tem_confianca_baixa_e_aviso_explicito(self):
        pipe, _, cid = self._cenario()
        txt = relatorio(pipe.comparar(cid))
        self.assertIn("Confiança da pesquisa: BAIXA", txt)
        self.assertIn("Confiabilidade da loja vencedora NÃO verificada suficientemente", txt)
        self.assertIn("condicional", txt)

    def test_oferta_valida_so_a_vista_nao_aparece_como_fora_da_recomendacao(self):
        pipe, _, cid = self._cenario()
        txt = relatorio(pipe.comparar(cid))
        self.assertNotIn("Verificadas, mas fora da recomendação", txt)      # C está na tabela B, apenas sem parcelamento

    def test_alerta_de_dispersao_aparece_uma_vez_so(self):
        pipe, _, cid = self._cenario()
        self.assertEqual(relatorio(pipe.comparar(cid)).count("dispersão alta"), 1)

    def test_sem_codigos_crus_no_texto(self):
        pipe, _, cid = self._cenario()
        txt = relatorio(pipe.comparar(cid))
        for cru in ("em_estoque", "nao_confirmada", "sem_estoque"):
            self.assertNotIn(cru, txt)

    def test_loja_com_cnpj_e_dominio_verificados_pode_ter_confianca_media_ou_alta(self):
        pipe, store, cid = self._cenario()
        from precos.seller import Loja
        lj = store.loja("a.com.br")
        lj.identidade = {"cnpj": "00000000000191", "situacao": "ATIVA", "abertura": "2005-01-01", "razao_social": "A LTDA", "compativel": True, "fonte": "https://brasilapi", "consultado_em": agora_brt().isoformat()}
        lj.rdap = {"idade_dias": 4000, "registrado_em": "2015-01-01", "fonte": "https://rdap", "consultado_em": agora_brt().isoformat()}
        lj.definir_tier("varejista", "https://prova")
        store.salvar_loja(lj)
        r = pipe.comparar(cid).ranking["vista"][0]
        self.assertEqual(r.oferta.dominio, "a.com.br")
        self.assertIn(r.confianca, ("média", "alta"))

    def test_loja_unica_nao_e_chamada_de_melhor_compra_e_tem_confianca_baixa(self):
        pipe, _, cid = montar({U_HARMONIA: html("Pedaleira Line 6 HX Stomp", "9190.00", corpo="<p>R$ 9.190,00</p>")})
        pipe.verificar(cid, U_HARMONIA)
        txt = relatorio(pipe.comparar(cid))
        self.assertIn("Única oferta verificada e elegível", txt)
        self.assertNotIn("Melhor compra:", txt)
        self.assertIn("não** significa que seja a melhor compra do mercado", txt)
        self.assertIn("Confiança da pesquisa: BAIXA", txt)
        self.assertIn("recomendação, condicional", txt)

    def test_duas_lojas_limitam_a_confianca_a_media_no_maximo(self):
        pipe, store, cid = montar({
            U_HARMONIA: html("Pedaleira Line 6 HX Stomp", "4300.00", corpo="<p>R$ 4.300,00</p>"),
            U_LOJA3: html("Pedaleira Line 6 HX Stomp", "4500.00", corpo="<p>R$ 4.500,00</p>"),
        })
        pipe.verificar(cid, U_HARMONIA)
        pipe.verificar(cid, U_LOJA3)
        txt = relatorio(pipe.comparar(cid))
        self.assertIn("Melhor compra:", txt)
        self.assertNotIn("Confiança da pesquisa: ALTA", txt)

    def test_preco_confirmado_sem_estoque_nao_e_rotulado_como_nao_confirmado(self):
        pagina = html("Line 6 HX STOMP Multi Effects | Lacrada", "4599.00", disp="OutOfStock", corpo="<p>R$ 4.599,00</p>")
        pipe, _, cid = montar({U_PEDALBOARD: pagina})
        pipe.verificar(cid, U_PEDALBOARD)
        txt = relatorio(pipe.comparar(cid))
        self.assertIn("porém **sem estoque**", txt)
        self.assertNotIn("origem: None", txt)
        self.assertIn("CONTRADIT", txt)

    def test_razao_social_verificada_substitui_nome_colado(self):
        pipe, store, cid = montar({U_HARMONIA: html("Pedaleira Line 6 HX Stomp", "4300.00", corpo="<p>R$ 4.300,00</p>")})
        pipe.verificar(cid, U_HARMONIA)
        lj = store.loja("harmonia.com.br")
        lj.identidade = {"cnpj": "00000000000191", "situacao": "ATIVA", "abertura": "2000-01-01", "razao_social": "HARMONIA MUSICAL LTDA",
                         "compativel": True, "fonte": "https://brasilapi", "consultado_em": agora_brt().isoformat()}
        store.salvar_loja(lj)
        self.assertIn("Harmonia Musical LTDA (harmonia.com.br)", relatorio(pipe.comparar(cid)))

    def test_sem_ofertas_verificadas_diz_que_nao_ha_recomendacao(self):
        pipe, store, cid = montar({})
        pipe.registrar_candidato(cid, "https://x.com.br/p", "100", "WebSearch", "Line 6 HX Stomp")
        txt = relatorio(pipe.comparar(cid))
        self.assertIn("Não há oferta verificada suficiente para recomendar", txt)
        self.assertIn("não comprar ainda", txt)
        self.assertNotIn("Melhor compra:", txt)

    def test_dados_antigos_sao_sinalizados(self):
        pipe, store, cid = self._cenario()
        comp = pipe.comparar(cid)
        for o in comp.ofertas:
            o.coletado_em -= timedelta(hours=12)
            store.salvar_oferta(o)
        self.assertIn("reverifique", relatorio(pipe.comparar(cid)))


if __name__ == "__main__":
    unittest.main()
