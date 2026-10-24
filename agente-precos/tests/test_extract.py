import json
import unittest
from decimal import Decimal

from precos.extract import extrair

URL = "https://www.loja-exemplo.com.br/pedaleira-line-6-hx-stomp/p"


def pagina(ld=None, corpo="", head_extra=""):
    blocos = "".join(f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in (ld or []))
    return f"<html><head><title>Loja</title>{head_extra}{blocos}</head><body>{corpo}</body></html>"


def produto(preco="4299.00", moeda="BRL", disp="https://schema.org/InStock", **extra):
    base = {
        "@context": "https://schema.org", "@type": "Product", "name": "Pedaleira Line 6 HX Stomp", "sku": "HXS-1",
        "gtin13": "7891000100103", "brand": {"@type": "Brand", "name": "Line 6"},
        "offers": {"@type": "Offer", "price": preco, "priceCurrency": moeda, "availability": disp,
                   "itemCondition": "https://schema.org/NewCondition", "seller": {"@type": "Organization", "name": "Loja Exemplo LTDA"}},
    }
    base.update(extra)
    return base


CORPO_OK = """<main><h1>Pedaleira Line 6 HX Stomp</h1><p>R$ 4.299,00</p>
<p>R$ 4.085,05 no Pix</p><p>ou em até 12x de R$ 358,25 sem juros</p><p>6x de R$ 760,00 com juros</p>
<p>Garantia de 12 meses do fabricante</p></main>
<footer>Loja Exemplo LTDA - CNPJ 00.000.000/0001-91</footer>"""


class ExtracaoTest(unittest.TestCase):
    def test_pagina_completa(self):
        ex = extrair(pagina([produto()], CORPO_OK), URL)
        self.assertEqual(ex.preco, Decimal("4299.00"))
        self.assertEqual(ex.fonte_preco, "json-ld")
        self.assertEqual(ex.disponibilidade, "em_estoque")
        self.assertEqual(ex.condicao, "novo")
        self.assertEqual(ex.vendedor, "Loja Exemplo LTDA")
        self.assertEqual(ex.gtin, "7891000100103")
        self.assertEqual(ex.marca, "Line 6")
        self.assertTrue(ex.preco_corroborado)
        self.assertEqual(ex.preco_pix, Decimal("4085.05"))
        self.assertEqual(ex.cnpjs, ["00000000000191"])
        self.assertIn("12 meses", ex.garantia)
        p12 = next(p for p in ex.parcelamentos if p.n == 12)
        self.assertEqual((p12.valor, p12.sem_juros), (Decimal("358.25"), True))
        p6 = next(p for p in ex.parcelamentos if p.n == 6)
        self.assertIs(p6.sem_juros, False)

    def test_gtin_invalido_e_descartado(self):
        ex = extrair(pagina([produto(gtin13="7891000100104")], CORPO_OK), URL)
        self.assertIsNone(ex.gtin)

    def test_moeda_diferente_de_brl_nao_vira_preco(self):
        ex = extrair(pagina([produto(moeda="USD")], "<p>US$ 999</p>"), URL)
        self.assertIsNone(ex.preco)
        self.assertTrue(any("USD" in a for a in ex.avisos))

    def test_sem_estoque(self):
        ex = extrair(pagina([produto(disp="https://schema.org/OutOfStock")], CORPO_OK), URL)
        self.assertEqual(ex.disponibilidade, "sem_estoque")

    def test_listagem_nao_atribui_preco(self):
        itens = [produto(name=f"Item {i}", sku=f"S{i}", preco=str(100 + i)) for i in range(3)]
        for it in itens:
            it["offers"]["price"] = it["offers"]["price"]
        ex = extrair(pagina([{"@type": "ItemList", "itemListElement": itens}], "<p>lista</p>"), URL)
        self.assertIsNone(ex.preco)
        self.assertTrue(ex.listagem)

    def test_aggregate_offer_avisa_vendedor_desconhecido(self):
        p = produto()
        p["offers"] = {"@type": "AggregateOffer", "lowPrice": "3999.90", "highPrice": "5100", "priceCurrency": "BRL", "offerCount": 7}
        ex = extrair(pagina([p], "<p>R$ 3.999,90</p>"), URL)
        self.assertEqual(ex.preco, Decimal("3999.90"))
        self.assertTrue(any("AggregateOffer" in a for a in ex.avisos))
        self.assertIsNone(ex.vendedor)

    def test_varias_ofertas_pega_menor_em_estoque_e_avisa(self):
        p = produto()
        p["offers"] = [
            {"@type": "Offer", "price": "3000", "priceCurrency": "BRL", "availability": "https://schema.org/OutOfStock"},
            {"@type": "Offer", "price": "4500", "priceCurrency": "BRL", "availability": "https://schema.org/InStock", "seller": "Vendedor A"},
            {"@type": "Offer", "price": "4200", "priceCurrency": "BRL", "availability": "https://schema.org/InStock", "seller": "Vendedor B"},
        ]
        ex = extrair(pagina([p], "<p>R$ 4.200,00</p>"), URL)
        self.assertEqual((ex.preco, ex.vendedor), (Decimal("4200"), "Vendedor B"))
        self.assertTrue(any("ofertas" in a for a in ex.avisos))

    def test_fallback_meta(self):
        head = '<meta property="product:price:amount" content="1.234,56"><meta property="product:price:currency" content="BRL"><meta property="og:site_name" content="Minha Loja">'
        ex = extrair(pagina([], "<p>R$ 1.234,56</p>", head), URL)
        self.assertEqual((ex.preco, ex.fonte_preco, ex.loja), (Decimal("1234.56"), "meta", "Minha Loja"))

    def test_preco_estruturado_ausente_do_texto_gera_aviso(self):
        ex = extrair(pagina([produto("4299.00")], "<p>R$ 5.999,00</p>"), URL)
        self.assertFalse(ex.preco_corroborado)
        self.assertTrue(any("não aparece no texto visível" in a for a in ex.avisos))

    def test_parcelamento_de_produto_relacionado_e_ignorado(self):
        corpo = "<p>R$ 4.299,00</p><p>Veja também: cabo por 12x de R$ 9,90 sem juros</p>"
        ex = extrair(pagina([produto()], corpo), URL)
        self.assertEqual(ex.parcelamentos, [])

    def test_pix_implausivel_e_ignorado(self):
        corpo = "<p>R$ 4.299,00</p><p>Acessório R$ 99,00 no Pix</p>"
        self.assertIsNone(extrair(pagina([produto()], corpo), URL).preco_pix)

    def test_json_ld_quebrado_nao_derruba(self):
        html = '<html><head><script type="application/ld+json">{"@type": "Product", </script></head><body>R$ 10,00</body></html>'
        ex = extrair(html, URL)
        self.assertIsNone(ex.preco)
        self.assertTrue(any("malformado" in a for a in ex.avisos))

    def test_pagina_sem_preco_estatico_avisa_js(self):
        ex = extrair("<html><body><div id=app></div></body></html>", URL)
        self.assertIsNone(ex.preco)
        self.assertTrue(any("JavaScript" in a for a in ex.avisos))

    def test_produto_relacionado_no_grafo_nao_contamina(self):
        p = produto()
        p["isRelatedTo"] = produto(name="Cabo", sku="C1", preco="99.00")
        ex = extrair(pagina([p], "<p>R$ 4.299,00</p>"), URL)
        self.assertEqual(ex.preco, Decimal("4299.00"))
        self.assertFalse(ex.listagem)

    def test_grafo_com_multiplos_produtos_escolhe_o_da_url(self):
        a = produto(name="Pedaleira Line 6 HX Stomp", url=URL)
        b = produto(name="Outro", sku="B", preco="999.00", url="https://www.loja-exemplo.com.br/outro/p")
        ex = extrair(pagina([{"@graph": [a, b]}], "<p>R$ 4.299,00</p>"), URL)
        self.assertEqual(ex.preco, Decimal("4299.00"))


if __name__ == "__main__":
    unittest.main()
