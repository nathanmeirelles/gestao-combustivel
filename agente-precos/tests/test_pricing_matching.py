import unittest
from datetime import datetime
from decimal import Decimal as D

from precos.matching import avaliar_identidade, tokens
from precos.models import Consulta, Evidencia, Oferta, Parcelamento, Status
from precos.pricing import custo_parcelado, custo_vista, custos_da_oferta, economia, escolher_parcelamento, melhor_vista, pior, taxa_mensal_implicita


def oferta(**kw):
    o = Oferta(url="https://x.com.br/p", dominio="x.com.br", status=Status.VERIFICADA, coletado_em=datetime.now())
    for k, v in kw.items():
        setattr(o, k, v)
    return o


class PricingTest(unittest.TestCase):
    def test_formulas_basicas(self):
        self.assertEqual(custo_vista(D("3000"), D("50"), D("10")), D("3060.00"))
        self.assertEqual(custo_parcelado(10, D("330"), D("50")), D("3350.00"))

    def test_economia(self):
        self.assertEqual(economia(D("3300"), D("3000")), (D("300.00"), D("9.1")))
        self.assertEqual(economia(D("0"), D("10"))[1], D("0.0"))

    def test_exemplo_do_briefing_oferta_a(self):
        # Pix 3000; cartão 10 x 330 = 3300 (preço no cartão 3300 "sem juros"); diferença para o Pix = 300
        o = oferta(preco_pix=D("3000"), preco_exibido=D("3300"), parcelamentos=[Parcelamento(10, D("330"), True)])
        c = custos_da_oferta(o)
        self.assertEqual(c["parcelado"].total, D("3300.00"))
        self.assertTrue(c["parcelado"].sem_juros)
        self.assertEqual(c["parcelado"].total - c["pix"].total, D("300.00"))
        self.assertEqual(melhor_vista(c).modalidade, "pix")

    def test_exemplo_do_briefing_oferta_b(self):
        o = oferta(preco_pix=D("3100"), preco_exibido=D("3100"), parcelamentos=[Parcelamento(10, D("310"), True)])
        c = custos_da_oferta(o)
        self.assertEqual(c["parcelado"].total - c["pix"].total, D("0.00"))
        self.assertEqual(c["parcelado"].taxa_am, D("0.00"))

    def test_parcelamento_com_juros_calcula_taxa_e_juros(self):
        # caso real observado na busca: R$ 4.599,00 à vista; 12x de R$ 467,98
        o = oferta(preco_exibido=D("4599.00"), parcelamentos=[Parcelamento(12, D("467.98"), None)])
        p = custos_da_oferta(o)["parcelado"]
        self.assertFalse(p.sem_juros)
        self.assertEqual(p.juros_total, D("1016.76"))
        self.assertTrue(D("3.0") < p.taxa_am < D("3.6"))  # ~3,3% a.m., conferido independentemente abaixo

    def test_taxa_conhecida(self):
        # 1000 em 2x de 520 => taxa i tal que 520/(1+i)+520/(1+i)^2 = 1000  (≈ 2,53% a.m.)
        t = taxa_mensal_implicita(D("1000"), 2, D("520"))
        i = float(t) / 100
        self.assertAlmostEqual(520 / (1 + i) + 520 / (1 + i) ** 2, 1000, delta=1.5)

    def test_sem_juros_declarado_mas_numeros_nao_batem_vira_divergente(self):
        o = oferta(preco_exibido=D("1000"), parcelamentos=[Parcelamento(10, D("120"), True)])
        o.marcar("preco_exibido", Evidencia.CONFIRMADO_DIRETAMENTE)
        p = custos_da_oferta(o)["parcelado"]
        self.assertEqual(p.evidencia, Evidencia.DIVERGENTE)

    def test_frete_so_entra_se_conhecido(self):
        o = oferta(preco_exibido=D("1000"))
        c = custos_da_oferta(o)["cartao_vista"]
        self.assertFalse(c.frete_incluido)
        self.assertTrue(any("frete não calculado" in n for n in c.notas))
        o.frete_valor = D("49.90")
        o.marcar("frete_valor", Evidencia.CONFIRMADO_DIRETAMENTE)
        c2 = custos_da_oferta(o)["cartao_vista"]
        self.assertTrue(c2.frete_incluido)
        self.assertEqual(c2.total, D("1049.90"))

    def test_evidencia_e_o_elo_mais_fraco(self):
        self.assertEqual(pior(Evidencia.CONFIRMADO_DIRETAMENTE, Evidencia.ESTIMADO), Evidencia.ESTIMADO)
        self.assertEqual(pior(Evidencia.ESTIMADO, Evidencia.DIVERGENTE), Evidencia.DIVERGENTE)
        o = oferta(preco_exibido=D("1000"), preco_pix=D("900"))
        o.marcar("preco_exibido", Evidencia.CONFIRMADO_DIRETAMENTE)
        o.marcar("preco_pix", Evidencia.ESTIMADO)
        c = custos_da_oferta(o)
        self.assertEqual(c["cartao_vista"].evidencia, Evidencia.CONFIRMADO_DIRETAMENTE)
        self.assertEqual(c["pix"].evidencia, Evidencia.ESTIMADO)

    def test_escolha_de_parcelamento_respeita_limite_e_desempata_por_parcela_menor(self):
        ps = [Parcelamento(6, D("200"), True), Parcelamento(10, D("120"), True), Parcelamento(12, D("100"), True), Parcelamento(12, D("110"), False)]
        self.assertEqual(escolher_parcelamento(ps, 12).n, 12)
        self.assertEqual(escolher_parcelamento(ps, 10).n, 10)
        self.assertIsNone(escolher_parcelamento(ps, 3))


class IdentidadeTest(unittest.TestCase):
    C = Consulta("Line 6 HX Stomp", modelo="line 6 hx stomp")

    def test_tokens(self):
        self.assertEqual(tokens("Line6 HX-Stomp"), ["line", "6", "hx", "stomp"])

    def test_confere(self):
        s, _ = avaliar_identidade("Pedal de efeito Line 6 Helix HX Stomp preto", self.C)
        self.assertEqual(s, "confere")

    def test_variante_xl_diverge(self):
        s, m = avaliar_identidade("Pedaleira Line 6 HX Stomp XL Multi-efeitos", self.C)
        self.assertEqual(s, "divergente")
        self.assertIn("xl", " ".join(m))

    def test_xl_pedido_pelo_usuario_nao_diverge(self):
        s, _ = avaliar_identidade("Pedaleira Line 6 HX Stomp XL", Consulta("Line 6 HX Stomp XL", modelo="line 6 hx stomp xl"))
        self.assertEqual(s, "confere")

    def test_semi_nova_diverge_quando_quer_novo(self):
        s, m = avaliar_identidade("Line 6 HX STOMP Multi Effects (semi-nova)", self.C)
        self.assertEqual(s, "divergente")

    def test_usado_ok_quando_usuario_aceita_usado(self):
        s, _ = avaliar_identidade("Line 6 HX Stomp usado", Consulta("Line 6 HX Stomp", modelo="line 6 hx stomp", condicao="usado"))
        self.assertEqual(s, "confere")

    def test_acessorio_diverge(self):
        s, _ = avaliar_identidade("Capa case para Line 6 HX Stomp", self.C)
        self.assertEqual(s, "divergente")

    def test_modelo_diferente_diverge(self):
        s, _ = avaliar_identidade("Pedaleira Boss GT-1000 Core", self.C)
        self.assertEqual(s, "divergente")

    def test_modelo_explicito_irmao_da_mesma_linha_diverge(self):
        """HX Effects e Pod Go são outros produtos da Line 6 (regressão: HX Effects era só 'incerto' e ficava elegível)."""
        for titulo in ("Pedaleira Multi-efeitos Line 6 HX Effects Guitarra", "Pedaleira Line 6 Multiefeitos Pod Go", "Line 6 Helix LT", "Line 6 HX Stomp Plus"):
            self.assertEqual(avaliar_identidade(titulo, self.C)[0], "divergente", titulo)

    def test_modelo_explicito_com_marca_omitida_e_incerto_nao_divergente(self):
        s, m = avaliar_identidade("Pedaleira HX Stomp Modeladora de Amplificadores", self.C)
        self.assertEqual(s, "incerto")
        self.assertIn("marca/linha", " ".join(m))

    def test_marca_omitida_nao_escapa_das_outras_regras(self):
        """Regressão: a tolerância à marca omitida retornava antes de checar variante, acessório e condição."""
        for titulo in ("Pedaleira HX Stomp XL Modeladora", "Capa para HX Stomp", "Pedaleira HX Stomp usada", "Pedaleira HX Stomp semi-nova"):
            self.assertEqual(avaliar_identidade(titulo, self.C)[0], "divergente", titulo)

    def test_marca_omitida_mas_so_um_termo_do_modelo_continua_divergente(self):
        c = Consulta("Boss DS-1", modelo="boss ds 1")
        self.assertEqual(avaliar_identidade("Pedal DS", c)[0], "divergente")
        self.assertEqual(avaliar_identidade("Pedal Distortion DS-1 Laranja", c)[0], "incerto")      # 'ds 1' em sequência

    def test_modelo_derivado_do_texto_continua_leniente(self):
        c = Consulta("Pedaleira Line 6 HX Stomp")                                                    # sem --modelo
        self.assertEqual(avaliar_identidade("Pedal de efeito Line 6 Helix HX Stomp preto", c)[0], "incerto")   # 'pedaleira' ausente

    def test_combo_e_incerto(self):
        s, _ = avaliar_identidade("Kit Line 6 HX Stomp com fonte e bag", self.C)
        self.assertEqual(s, "incerto")

    def test_gtin_prevalece(self):
        o = oferta(gtin="7891000100103")
        self.assertEqual(avaliar_identidade("qualquer coisa", Consulta("x", gtin="7891000100103"), o)[0], "confere")
        self.assertEqual(avaliar_identidade("Line 6 HX Stomp", Consulta("x", gtin="7890000000000"), o)[0], "divergente")

    def test_condicao_declarada_na_pagina_diverge(self):
        o = oferta(condicao="usado")
        self.assertEqual(avaliar_identidade("Line 6 HX Stomp", self.C, o)[0], "divergente")

    def test_excluir_do_usuario(self):
        s, _ = avaliar_identidade("Line 6 HX Stomp Roxo", Consulta("x", modelo="line 6 hx stomp", excluir=["roxo"]))
        self.assertEqual(s, "divergente")


if __name__ == "__main__":
    unittest.main()
