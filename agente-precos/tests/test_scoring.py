import unittest
from datetime import timedelta
from decimal import Decimal as D

from precos.models import Consulta, Evidencia, Oferta, Parcelamento, Status
from precos.scoring import PESOS_PADRAO, PESOS_PRIORIDADE, avaliar_confiabilidade, pontuar, sinais_de_preco
from precos.seller import Loja
from precos.util import agora_brt

AGORA = agora_brt()


def of(dom="loja.com.br", preco="1000", **kw):
    o = Oferta(url=f"https://{dom}/p", dominio=dom, status=Status.VERIFICADA, coletado_em=AGORA - timedelta(minutes=10))
    o.preco_exibido = D(preco)
    o.marcar("preco_exibido", Evidencia.CONFIRMADO_DIRETAMENTE)
    o.marcar("produto", Evidencia.CONFIRMADO_INDEPENDENTE)
    o.marcar("vendedor", Evidencia.CONFIRMADO_DIRETAMENTE)
    o.identidade_produto = "confere"
    o.disponibilidade = "em_estoque"
    for k, v in kw.items():
        setattr(o, k, v)
    return o


def loja_boa(dom="loja.com.br"):
    lj = Loja(dom, nome="Loja", cnpj="00000000000191")
    lj.identidade = {"situacao": "ATIVA", "abertura": "2010-01-01", "razao_social": "LOJA LTDA", "compativel": True, "fonte": "https://brasilapi"}
    lj.rdap = {"idade_dias": 4000, "registrado_em": "2015-01-01", "fonte": "https://rdap"}
    lj.definir_tier("varejista", "https://prova")
    lj.definir_reputacao(8.0, 10, "https://rep", AGORA.date().isoformat())
    lj.politicas_ok, lj.politicas_fonte = True, "https://pol"
    return lj


class PesosTest(unittest.TestCase):
    def test_todas_as_ponderacoes_somam_100(self):
        self.assertEqual(sum(PESOS_PADRAO.values()), 100)
        self.assertEqual(PESOS_PADRAO, {"custo": 45, "confiabilidade": 25, "pagamento": 15, "entrega": 10, "garantia": 5})
        for nome, p in PESOS_PRIORIDADE.items():
            self.assertEqual(sum(p.values()), 100, nome)


class ConfiabilidadeTest(unittest.TestCase):
    def test_loja_totalmente_verificada(self):
        c = avaliar_confiabilidade(of(), loja_boa())
        self.assertEqual(c.pontos, 25 + 10 + 5 + 15 + 24 + 10)
        self.assertEqual(c.cobertura, 1.0)
        self.assertEqual(c.rotulo, "alta")

    def test_sem_nenhum_dado_nao_ganha_nota_e_diz_que_nao_verificou(self):
        c = avaliar_confiabilidade(of(), None)
        self.assertEqual(c.pontos, 0)
        self.assertEqual(c.rotulo, "não verificada suficientemente")
        self.assertTrue(all(not x.verificado for x in c.componentes))

    def test_cnpj_baixado_e_risco_alto(self):
        lj = loja_boa()
        lj.identidade["situacao"] = "BAIXADA"
        c = avaliar_confiabilidade(of(), lj)
        self.assertEqual(c.rotulo, "risco")
        self.assertTrue(any("BAIXADA" in s for s in c.sinais_risco))

    def test_cnpj_ativo_mas_sem_vinculo_vale_menos_e_nao_conta_tempo(self):
        lj = loja_boa()
        lj.identidade["compativel"] = False
        c = avaliar_confiabilidade(of(), lj)
        ident = next(x for x in c.componentes if x.nome.startswith("identidade"))
        tempo = next(x for x in c.componentes if x.nome.startswith("tempo"))
        self.assertEqual(ident.pontos, 15)
        self.assertEqual(ident.evidencia, Evidencia.ESTIMADO)
        self.assertEqual((tempo.pontos, tempo.verificado), (0, False))
        self.assertFalse(c.identidade_ok)

    def test_dominio_novo_gera_sinal(self):
        lj = loja_boa()
        lj.rdap["idade_dias"] = 20
        c = avaliar_confiabilidade(of(), lj)
        self.assertTrue(any("20 dias" in s for s in c.sinais_risco))

    def test_reputacao_antiga_vale_metade(self):
        lj = loja_boa()
        lj.reputacao_data = (AGORA - timedelta(days=500)).date().isoformat()
        c = avaliar_confiabilidade(of(), lj)
        self.assertEqual(next(x for x in c.componentes if x.nome.startswith("reputação")).pontos, 12.0)

    def test_tier_risco(self):
        lj = loja_boa()
        lj.definir_tier("risco", None)
        self.assertEqual(avaliar_confiabilidade(of(), lj).rotulo, "risco")


class RankingTest(unittest.TestCase):
    C = Consulta("Produto X", modelo="produto x")

    def test_mais_barata_mas_nao_verificada_perde_para_confiavel(self):
        barata = of("barata.com.br", "900")
        confiavel = of("confiavel.com.br", "1000")
        r = pontuar([barata, confiavel], self.C, {"confiavel.com.br": loja_boa("confiavel.com.br")}, "vista")
        self.assertEqual(r[0].oferta.dominio, "confiavel.com.br")
        self.assertTrue(all(p.elegivel for p in r))

    def test_criterio_nao_verificado_vale_zero_e_nao_neutro(self):
        r = pontuar([of()], self.C, {}, "vista")[0]
        self.assertIsNone(r.criterios["entrega"].nota)
        self.assertEqual(r.criterios["entrega"].pontos, 0)
        self.assertIsNone(r.criterios["garantia"].nota)
        self.assertLess(r.pontos, 45 + 15 + 1)   # custo(45) + pagamento(≤15) no máximo

    def test_exclui_produto_divergente(self):
        r = pontuar([of(identidade_produto="divergente")], self.C, {}, "vista")[0]
        self.assertFalse(r.elegivel)
        self.assertTrue(any("não confere" in m for m in r.motivos_exclusao))

    def test_exclui_sem_estoque(self):
        self.assertFalse(pontuar([of(disponibilidade="sem_estoque")], self.C, {}, "vista")[0].elegivel)

    def test_exclui_risco_alto_mesmo_sendo_a_mais_barata(self):
        golpe = of("golpe.com.br", "100", sinais_risco=["[ALTO] suspeita"])
        ok = of("ok.com.br", "1000")
        r = pontuar([golpe, ok], self.C, {}, "vista")
        self.assertEqual(r[0].oferta.dominio, "ok.com.br")
        self.assertFalse(next(p for p in r if p.oferta.dominio == "golpe.com.br").elegivel)

    def test_outlier_de_preco_vira_risco_alto(self):
        ofs = [of("a.com.br", "4300"), of("b.com.br", "4600"), of("c.com.br", "9200"), of("d.com.br", "1000")]
        sinais = sinais_de_preco(ofs)
        self.assertTrue(any(s.startswith("[ALTO]") for s in sinais["https://d.com.br/p"]))
        r = pontuar(ofs, self.C, {}, "vista")
        self.assertFalse(next(p for p in r if p.oferta.dominio == "d.com.br").elegivel)

    def test_outlier_exige_tres_dominios(self):
        self.assertEqual(sinais_de_preco([of("a.com.br", "4300"), of("d.com.br", "100")]), {})

    def test_marketplace_nao_aceito(self):
        c = Consulta("x", aceita_marketplace=False)
        self.assertFalse(pontuar([of("produto.mercadolivre.com.br")], c, {}, "vista")[0].elegivel)

    def test_prioridade_oficial_exige_loja_oficial(self):
        c = Consulta("x", prioridade="oficial")
        lj = loja_boa()
        r = pontuar([of()], c, {"loja.com.br": lj}, "vista")[0]
        self.assertFalse(r.elegivel)                    # varejista != oficial/autorizado
        lj.definir_tier("autorizado", "https://prova")
        self.assertTrue(pontuar([of()], c, {"loja.com.br": lj}, "vista")[0].elegivel)

    def test_oferta_nao_verificada_nunca_entra(self):
        o = of()
        o.status = Status.NAO_CONFIRMADA
        self.assertEqual(pontuar([o], self.C, {}, "vista"), [])

    def test_confianca_alta_exige_essenciais_confirmados(self):
        lj = {"loja.com.br": loja_boa()}
        self.assertEqual(pontuar([of()], self.C, lj, "vista")[0].confianca, "alta")
        self.assertEqual(pontuar([of(coletado_em=AGORA - timedelta(hours=9))], self.C, lj, "vista")[0].confianca, "média")
        o = of()
        o.marcar("produto", Evidencia.ESTIMADO)
        self.assertEqual(pontuar([o], self.C, lj, "vista")[0].confianca, "média")
        o2 = of()
        o2.marcar("preco_exibido", Evidencia.ESTIMADO)
        self.assertEqual(pontuar([o2], self.C, lj, "vista")[0].confianca, "baixa")

    def test_pix_so_do_texto_impede_confianca_alta(self):
        o = of(preco_pix=D("900"))
        o.marcar("preco_pix", Evidencia.ESTIMADO)
        r = pontuar([o], self.C, {"loja.com.br": loja_boa()}, "vista")[0]
        self.assertEqual(r.custo.modalidade, "pix")
        self.assertNotEqual(r.confianca, "alta")

    def test_marketplace_sem_vendedor_identificado_nao_tem_vendedor_confirmado(self):
        o = of("produto.mercadolivre.com.br")
        o.evidencias.pop("vendedor")
        r = pontuar([o], self.C, {}, "vista")[0]
        self.assertFalse(r.essenciais["vendedor"].confirmado)

    def test_modo_parcelado_sem_juros_vs_com_juros(self):
        a = of("a.com.br", "1200", parcelamentos=[Parcelamento(10, D("120"), True)])
        b = of("b.com.br", "1000", parcelamentos=[Parcelamento(10, D("130"), None)])
        for x in (a, b):
            x.marcar("parcelamentos", Evidencia.ESTIMADO)
        r = pontuar([a, b], Consulta("x", parcelas=10), {}, "parcelado")
        self.assertEqual(r[0].oferta.dominio, "a.com.br")     # total 1200 < 1300
        self.assertEqual(r[0].criterios["pagamento"].nota, 100.0)

    def test_ordem_estavel_elegiveis_primeiro(self):
        ruim = of("ruim.com.br", "10", identidade_produto="divergente")
        bom = of("bom.com.br", "1000")
        r = pontuar([ruim, bom], self.C, {}, "vista")
        self.assertEqual([p.oferta.dominio for p in r], ["bom.com.br", "ruim.com.br"])


if __name__ == "__main__":
    unittest.main()
