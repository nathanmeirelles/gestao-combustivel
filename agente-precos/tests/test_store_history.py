import unittest
from datetime import timedelta
from decimal import Decimal as D

from precos.history import Serie, classificar, serie_do_produto
from precos.models import Consulta, Evidencia, Oferta, Parcelamento, Status
from precos.seller import Loja
from precos.store import Store
from precos.util import agora_brt


def of(cid, url="https://a.com.br/p?utm_source=x", preco="1000", quando=None, **kw):
    o = Oferta(url=url, dominio="a.com.br", status=Status.VERIFICADA, coletado_em=quando or agora_brt(), consulta_id=cid, preco_exibido=D(preco))
    for k, v in kw.items():
        setattr(o, k, v)
    return o


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.s = Store(":memory:")
        self.addCleanup(self.s.fechar)
        self.c = Consulta("Line 6 HX Stomp", modelo="line 6 hx stomp", parcelas=12)
        self.cid = self.s.nova_consulta(self.c)

    def test_consulta_roundtrip(self):
        c = self.s.consulta(self.cid)
        self.assertEqual((c.texto, c.parcelas, c.condicao), ("Line 6 HX Stomp", 12, "novo"))
        self.assertIsNotNone(c.criada_em)

    def test_oferta_roundtrip_preserva_decimal_e_evidencias(self):
        o = of(self.cid, preco="4299.00", preco_pix=D("4085.05"), parcelamentos=[Parcelamento(12, D("358.25"), True)])
        o.marcar("preco_exibido", Evidencia.CONFIRMADO_DIRETAMENTE)
        o.alertas = ["a"]
        oid = self.s.salvar_oferta(o)
        r = self.s.oferta(oid)
        self.assertEqual((r.preco_exibido, r.preco_pix), (D("4299.00"), D("4085.05")))
        self.assertEqual(r.parcelamentos[0].valor, D("358.25"))
        self.assertEqual(r.evid("preco_exibido"), Evidencia.CONFIRMADO_DIRETAMENTE)
        self.assertEqual(r.coletado_em, o.coletado_em)

    def test_ultima_observacao_por_url_deduplica_rastreadores(self):
        self.s.salvar_oferta(of(self.cid, "https://a.com.br/p?utm_source=x", "1000"))
        self.s.salvar_oferta(of(self.cid, "https://A.com.br/p/?fbclid=zzz", "950"))
        self.s.salvar_oferta(of(self.cid, "https://b.com.br/q", "2000"))
        ult = self.s.ultimas_ofertas(self.cid)
        self.assertEqual(len(ult), 2)
        self.assertEqual(next(o for o in ult if "a.com.br" in o.url.lower()).preco_exibido, D("950"))

    def test_historico_por_url_ordenado(self):
        agora = agora_brt()
        self.s.salvar_oferta(of(self.cid, preco="900", quando=agora - timedelta(days=3)))
        self.s.salvar_oferta(of(self.cid, preco="1000", quando=agora))
        h = self.s.historico_url("https://a.com.br/p")
        self.assertEqual([x.preco_exibido for x in h], [D("900"), D("1000")])

    def test_loja_upsert(self):
        lj = Loja("a.com.br", nome="A")
        self.s.salvar_loja(lj)
        lj.definir_tier("varejista", "https://prova")
        self.s.salvar_loja(lj)
        self.assertEqual(self.s.loja("a.com.br").tier, "varejista")
        self.assertIsNone(self.s.loja("nao.existe"))

    def test_oferta_sem_consulta_e_rejeitada(self):
        with self.assertRaises(ValueError):
            self.s.salvar_oferta(of(None))

    def test_monitores(self):
        mid = self.s.novo_monitor(self.cid, {"preco_alvo": "3500", "intervalo_h": 24})
        self.assertEqual(self.s.monitores()[0]["preco_alvo"], "3500")
        self.s.atualizar_monitor(mid, ultimo_preco="3600")
        self.assertEqual(self.s.monitores()[0]["ultimo_preco"], "3600")
        self.assertTrue(self.s.desativar_monitor(mid))
        self.assertEqual(self.s.monitores(), [])
        self.assertEqual(len(self.s.monitores(so_ativos=False)), 1)

    def test_sql_injection_nao_afeta(self):
        self.s.salvar_loja(Loja("x'; DROP TABLE lojas;--"))
        self.assertIsNotNone(self.s.loja("x'; DROP TABLE lojas;--"))
        self.assertEqual(self.s.lojas(["a"]), {})


class HistoricoTest(unittest.TestCase):
    def test_insuficiente_nao_classifica(self):
        s = Serie([(agora_brt(), "a", D("100"))])
        rotulo, fund = classificar(D("100"), s)
        self.assertEqual(rotulo, "Dados insuficientes para classificar")
        self.assertIn("1 observações", fund)

    def _serie(self, precos, dias=30):
        base = agora_brt() - timedelta(days=dias)
        return Serie([(base + timedelta(days=i * dias / (len(precos) - 1)), "a", D(str(p))) for i, p in enumerate(precos)])

    def test_classificacoes(self):
        s = self._serie([100, 110, 120, 130, 140, 150, 160, 170, 180, 200])
        self.assertEqual(classificar(D("100"), s)[0], "Excelente oportunidade")
        # Q1 (método exclusivo) de 10 pontos 100..200 = 110 + 0,75 × 10 = 117,5
        self.assertEqual(classificar(D("115"), s)[0], "Bom preço")
        self.assertEqual(classificar(D("120"), s)[0], "Preço dentro da faixa normal")
        self.assertEqual(classificar(D("150"), s)[0], "Preço dentro da faixa normal")
        self.assertEqual(classificar(D("195"), s)[0], "Preço elevado")

    def test_muitas_observacoes_em_pouco_tempo_nao_bastam(self):
        s = self._serie([100] * 10, dias=2)
        self.assertEqual(classificar(D("100"), s)[0], "Dados insuficientes para classificar")

    def test_serie_do_produto_ignora_divergentes_e_outras_condicoes(self):
        st = Store(":memory:")
        self.addCleanup(st.fechar)
        c = Consulta("Line 6 HX Stomp", modelo="line 6 hx stomp")
        cid = st.nova_consulta(c)
        st.salvar_oferta(of(cid, preco="1000"))
        st.salvar_oferta(of(cid, url="https://b.com.br/x", preco="50", identidade_produto="divergente"))
        c2 = Consulta("outro produto")
        cid2 = st.nova_consulta(c2)
        st.salvar_oferta(of(cid2, url="https://c.com.br/y", preco="7"))
        c3 = Consulta("Line 6 HX Stomp", modelo="LINE 6 hx stomp", condicao="usado")
        cid3 = st.nova_consulta(c3)
        st.salvar_oferta(of(cid3, url="https://d.com.br/z", preco="3"))
        s = serie_do_produto(st, c)
        self.assertEqual([p[2] for p in s.pontos], [D("1000")])


if __name__ == "__main__":
    unittest.main()
