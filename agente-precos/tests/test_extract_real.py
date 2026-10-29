"""Regressões de páginas REAIS (24–29/10/2026): grade de relacionados, bloco de atacado e preço estruturado que já é o do Pix."""

import json
import unittest
from decimal import Decimal as D

from precos.extract import _ancora, extrair
from precos.matching import avaliar_identidade
from precos.models import Consulta, Evidencia, Status
from precos.pipeline import Pipeline
from precos.pricing import custos_da_oferta
from precos.report import relatorio
from tests.test_pipeline_report import ABERTOS, ApiOffline, FetcherFalso
from precos.config import Config
from precos.store import Store
from pathlib import Path
import tempfile

URL = "https://loja-exemplo.com.br/produto/fone-x"


def pagina(preco, corpo):
    ld = {"@context": "https://schema.org", "@type": "Product", "name": "Fone de Ouvido X",
          "offers": {"@type": "Offer", "price": preco, "priceCurrency": "BRL", "availability": "https://schema.org/InStock"}}
    return f"<html><head><title>x</title><script type='application/ld+json'>{json.dumps(ld)}</script></head><body>{corpo}</body></html>"


# estrutura da página da Truedata: grade de relacionados ANTES do item; bloco de atacado DEPOIS
CORPO_GRADE_E_ATACADO = """
<div class="relacionados">
  <p>Scanner Preto TL-B2310H GANHE 169 PONTOS R$ 152,91 no Pix ou 5x de R$ 33,98 sem juros</p>
  <p>Gabinete Slim 400W GANHE 319 PONTOS R$ 287,91 no Pix ou 10x de R$ 31,99 sem juros</p>
  <p>Cabo Sata GANHE 9 PONTOS R$ 8,91 no Pix ou 1x de R$ 9,90 sem juros</p>
</div>
<main><h1>Fone de Ouvido X</h1>
  <p>VAREJO ATACADO WHATSAPP Menor preço R$ 260,91 no Pix 10% OFF Parcelado R$ 289,90 Até 9x de R$ 32,21 sem juros</p>
  <p>Descrição do produto</p>
  <p>Preços para revendedores Atacado no Pix De R$ 289,90 R$ 231,92 no Pix 20% OFF Economia de R$ 57,98 Atacado Pague no Pix mínimo 05 unidades</p>
</main>
"""

# estrutura da página da Look Music: o preço estruturado é o do Pix; a lista e as parcelas vêm no texto; relacionados com outros valores
CORPO_ESTRUTURADO_E_PIX = """
<main><h1>Encordoamento X</h1><p>Marca: Elixir R$ 325,00 até 6x de R$ 54,16 sem juros ou R$ 292,50 via Pix Qtde: Comprar Estoque: Disponível</p></main>
<section>Veja também Encordoamento Y 12-53 R$ 277,00 até 5x de R$ 55,40 sem juros ou R$ 249,30 via Pix Ver mais
 Encordoamento Z R$ 325,00 até 6x de R$ 54,16 sem juros ou R$ 292,50 via Pix Comprar</section>
""" + (" " * 600) + "<footer>outros itens R$ 277,00 até 5x de R$ 55,40 sem juros ou R$ 249,30 via Pix</footer>"


class GradeEAtacadoTest(unittest.TestCase):
    def setUp(self):
        self.ex = extrair(pagina("289.90", CORPO_GRADE_E_ATACADO), URL)

    def test_pix_e_do_item_principal_nao_de_relacionado_nem_atacado(self):
        self.assertEqual(self.ex.preco_pix, D("260.91"))           # antes pegava R$ 152,91 (um scanner) por ser o MENOR
        self.assertEqual(self.ex.meio_vista, "pix")

    def test_atacado_nunca_vira_preco(self):
        self.assertNotEqual(self.ex.preco_pix, D("231.92"))

    def test_parcelas_do_item_e_nao_as_do_relacionado(self):
        self.assertEqual([(p.n, p.valor, p.sem_juros) for p in self.ex.parcelamentos], [(9, D("32.21"), True)])

    def test_preco_estruturado_segue_sendo_o_da_pagina(self):
        self.assertEqual(self.ex.preco, D("289.90"))
        self.assertTrue(self.ex.preco_corroborado)


class EstruturadoEPixTest(unittest.TestCase):
    def setUp(self):
        self.ex = extrair(pagina("292.50", CORPO_ESTRUTURADO_E_PIX), URL)

    def test_reconhece_que_o_preco_estruturado_e_o_do_pix(self):
        self.assertEqual((self.ex.preco, self.ex.preco_pix, self.ex.meio_vista), (D("292.50"), D("292.50"), "pix"))
        self.assertEqual(self.ex.preco_lista, D("325.00"))
        self.assertTrue(any("coincide com o valor do Pix" in a for a in self.ex.avisos))

    def test_nao_usa_o_pix_de_outro_item_da_pagina(self):
        self.assertNotEqual(self.ex.preco_pix, D("249.30"))

    def test_parcelas_somam_o_preco_de_lista(self):
        self.assertEqual([(p.n, p.valor, p.sem_juros) for p in self.ex.parcelamentos], [(6, D("54.16"), True)])

    def test_sem_preco_de_lista_identificavel_avisa(self):
        ex = extrair(pagina("292.50", "<main><p>Item R$ 292,50 via Pix</p></main>"), URL)
        self.assertEqual((ex.preco_pix, ex.preco_lista), (D("292.50"), None))
        self.assertTrue(any("preço de lista/cartão não identificado" in a for a in ex.avisos))


class KabumLikeTest(unittest.TestCase):
    """Texto real da KaBuM: 'R$ 269,10 - 10 % à vista no PIX 10 x R$ 29,90 s/ juros' com preço estruturado = Pix."""

    CORPO = "<main><h1>Fone</h1><p>5 ( 5 ) R$ 269,10 - 10 % à vista no PIX 10 x R$ 29,90 s/ juros Ver detalhes de parcelamento Inserir CEP Comprar agora</p></main>"

    def test_pix_com_percentual_no_meio_e_parcelas_s_barra_juros(self):
        ex = extrair(pagina("269.10", self.CORPO), URL)
        self.assertEqual((ex.preco_pix, ex.meio_vista), (D("269.10"), "pix"))
        self.assertEqual([(p.n, p.valor, p.sem_juros) for p in ex.parcelamentos], [(10, D("29.90"), True)])   # antes: sem_juros=False
        self.assertEqual(ex.preco_lista, D("299.00"))                      # derivado: 10 × 29,90
        self.assertTrue(any("derivado das parcelas sem juros" in a for a in ex.avisos))

    def test_formas_de_escrever_sem_juros(self):
        base = "<p>Item R$ 100,00 em 10x de R$ 10,00 {} fim</p>"
        casos = {"sem juros": True, "s/ juros": True, "s/juros": True, "sem juro": True, "sem acréscimo": True, "0% de juros": True,
                 "com juros": False, "c/ juros": False, "juros de 1,5% a.m.": False, "": None}
        for frase, esperado in casos.items():
            p = extrair(pagina("100.00", base.format(frase)), URL).parcelamentos
            self.assertEqual(p[0].sem_juros if p else "ausente", esperado, frase)

    def test_pix_sem_percentual_e_com_percentual_e_off(self):
        for trecho in ("R$ 90,00 à vista no Pix", "R$ 90,00 - 10 % à vista no PIX", "R$ 90,00 (10% off) no Pix", "R$ 90,00 10% de desconto via Pix"):
            ex = extrair(pagina("100.00", f"<p>Produto R$ 100,00 ou {trecho}</p>"), URL)
            self.assertEqual(ex.preco_pix, D("90.00"), trecho)


class ValoresComEspacoTest(unittest.TestCase):
    """Plataforma que escreve 'R$ 220 , 41' e 'à vista com desconto Pix' (Megasom, Cheiro de Música, Ponto do Músico)."""

    def test_pix_com_desconto_e_parcelas_com_espaco_na_virgula(self):
        corpo = "<p>Disponibilidade: Imediata Por: R$ 244,90 R$ 220 , 41 à vista com desconto Pix ou 12 x de R$ 20 , 41 Sem juros Cartão MasterCard</p>"
        ex = extrair(pagina("244.90", corpo), URL)
        self.assertEqual((ex.preco_pix, ex.meio_vista), (D("220.41"), "pix"))
        self.assertEqual([(p.n, p.valor, p.sem_juros) for p in ex.parcelamentos], [(12, D("20.41"), True)])

    def test_a_vista_com_desconto_sem_citar_pix_fica_com_meio_nao_especificado(self):
        corpo = "<p>Por: R$ 699,00 R$ 650 , 07 à vista com desconto ou 12 x de R$ 58 , 25 Sem juros + Ver todas as formas de pagamentos</p>"
        ex = extrair(pagina("699.00", corpo), URL)
        self.assertEqual((ex.preco_pix, ex.meio_vista), (D("650.07"), "não especificado"))
        self.assertEqual([(p.n, p.valor) for p in ex.parcelamentos], [(12, D("58.25"))])

    def test_pix_escrito_depois_do_valor_com_nome_do_processador(self):
        corpo = "<p>De R$ 287,00 Por: R$ 209,94 Economize R$ 77,06 12 x de R$ 17 , 50 Sem juros Cartão de Crédito - Mercado Pago R$ 178 , 45 Pix - Vindi</p>"
        ex = extrair(pagina("209.94", corpo), URL)
        self.assertEqual((ex.preco_pix, ex.meio_vista), (D("178.45"), "pix"))
        self.assertEqual([(p.n, p.valor, p.sem_juros) for p in ex.parcelamentos], [(12, D("17.50"), True)])

    def test_normalizacao_nao_mexe_em_texto_sem_cifrao(self):
        corpo = "<p>Produto R$ 100,00 modelo 5 , 20 unidades ou R$ 90,00 no Pix</p>"
        self.assertEqual(extrair(pagina("100.00", corpo), URL).preco_pix, D("90.00"))


class AncoraTest(unittest.TestCase):
    def test_formas_de_escrita(self):
        self.assertEqual(_ancora("a R$ 1.234,50 b", D("1234.50")), 2)
        self.assertEqual(_ancora("a R$ 1234,50 b", D("1234.50")), 2)
        self.assertEqual(_ancora("a 1.234,50 b", D("1234.50")), 2)          # sem R$ como segunda opção
        self.assertEqual(_ancora("preço R$ 4.299 hoje", D("4299.00")), 6)   # inteiro sem centavos
        self.assertEqual(_ancora("R$ 4299", D("4299")), 0)

    def test_nao_casa_dentro_de_numero_maior_nem_com_centavos_diferentes(self):
        self.assertIsNone(_ancora("R$ 14.299,00", D("4299.00")))
        self.assertIsNone(_ancora("R$ 4.299,50", D("4299.00")))
        self.assertIsNone(_ancora("R$ 1.234,56", D("1234.5")))
        self.assertIsNone(_ancora("nada aqui", D("10")))

    def test_prefere_a_forma_com_cifrao_e_a_primeira_ocorrencia(self):
        self.assertEqual(_ancora("ref 100,00 e depois R$ 100,00 e R$ 100,00", D("100")), 20)
        self.assertEqual(_ancora("R$ 50,00 ... R$ 50,00", D("50")), 0)


class JanelaETextoAusenteTest(unittest.TestCase):
    def test_preco_ausente_do_texto_nao_atribui_pix_nem_parcelas(self):
        corpo = "<p>Outro item R$ 80,00 no Pix ou 10x de R$ 10,00 sem juros</p>"
        ex = extrair(pagina("100.00", corpo), URL)
        self.assertEqual((ex.preco_pix, ex.parcelamentos, ex.preco_corroborado), (None, [], False))
        self.assertTrue(any("NÃO foram atribuídos" in a for a in ex.avisos))

    def test_candidato_fora_da_janela_e_ignorado(self):
        corpo = "<p>Produto R$ 100,00</p><p>" + "texto " * 200 + "</p><p>R$ 90,00 no Pix</p>"
        self.assertIsNone(extrair(pagina("100.00", corpo), URL).preco_pix)

    def test_entre_dois_candidatos_vale_o_mais_proximo_nao_o_menor(self):
        corpo = "<p>R$ 60,00 no Pix</p><p>" + "x " * 60 + "</p><p>Produto R$ 100,00 ou R$ 92,00 no Pix</p>"
        self.assertEqual(extrair(pagina("100.00", corpo), URL).preco_pix, D("92.00"))

    def test_condicao_de_revenda_ou_quantidade_exclui_o_candidato(self):
        casos = ["<p>Produto R$ 100,00</p><p>Preços para revendedores R$ 70,00 no Pix</p>",
                 "<p>Produto R$ 100,00</p><p>R$ 70,00 no Pix mínimo 5 unidades</p>",
                 "<p>Produto R$ 100,00</p><p>R$ 70,00 no Pix a partir de 10 peças</p>"]
        for corpo in casos:
            self.assertIsNone(extrair(pagina("100.00", corpo), URL).preco_pix, corpo)

    def test_aba_varejo_atacado_nao_exclui_o_preco_de_varejo(self):
        corpo = "<p>VAREJO ATACADO Menor preço R$ 92,00 no Pix 8% OFF Produto R$ 100,00</p>"
        self.assertEqual(extrair(pagina("100.00", corpo), URL).preco_pix, D("92.00"))

    def test_kit_e_combo_no_nome_do_item_nao_apagam_o_pix(self):
        corpo = "<p>Kit 3 jogos de cordas R$ 300,00 ou R$ 270,00 no Pix</p>"
        self.assertEqual(extrair(pagina("300.00", corpo), URL).preco_pix, D("270.00"))

    def test_parcela_sem_juros_que_nao_fecha_com_o_preco_e_de_outro_item(self):
        corpo = "<p>Item R$ 289,90 em 9x de R$ 32,21 sem juros</p><p>Outro: 10x de R$ 31,99 sem juros</p>"
        self.assertEqual([(p.n, p.sem_juros) for p in extrair(pagina("289.90", corpo), URL).parcelamentos], [(9, True)])

    def test_parcela_com_juros_precisa_custar_mais_que_a_vista_mas_nao_absurdamente(self):
        ok = extrair(pagina("1000.00", "<p>R$ 1.000,00 em 12x de R$ 110,00 com juros</p>"), URL).parcelamentos
        absurdo = extrair(pagina("1000.00", "<p>R$ 1.000,00 em 12x de R$ 300,00 com juros</p>"), URL).parcelamentos
        menor = extrair(pagina("1000.00", "<p>R$ 1.000,00 em 12x de R$ 50,00 com juros</p>"), URL).parcelamentos
        self.assertEqual(([(p.n, p.sem_juros) for p in ok], absurdo, menor), ([(12, False)], [], []))


class PipelineComPixEstruturadoTest(unittest.TestCase):
    def test_precos_rotulados_corretamente_e_comparaveis(self):
        cfg = Config(data_dir=Path(tempfile.mkdtemp()))
        store = Store(":memory:")
        ABERTOS.append(store)
        cid = store.nova_consulta(Consulta("Fone X", modelo="fone x", parcelas=6))
        pipe = Pipeline(cfg, store, fetcher=FetcherFalso({URL: pagina("292.50", CORPO_ESTRUTURADO_E_PIX)}), api=ApiOffline())
        o = pipe.verificar(cid, URL)
        self.assertEqual(o.status, Status.VERIFICADA)
        self.assertEqual((o.preco_exibido, o.preco_pix, o.preco_cartao_vista, o.preco_ref), (D("292.50"), D("292.50"), D("325.00"), D("325.00")))
        self.assertEqual(o.evid("preco_exibido"), Evidencia.CONFIRMADO_DIRETAMENTE)
        self.assertEqual(o.evid("preco_cartao_vista"), Evidencia.ESTIMADO)
        c = custos_da_oferta(o, 6)
        self.assertEqual((c["pix"].total, c["pix"].rotulo), (D("292.50"), "Pix"))
        self.assertEqual(c["cartao_vista"].total, D("325.00"))
        self.assertTrue(c["parcelado"].sem_juros)                                    # 6 × 54,16 ≈ 325,00, contra a LISTA
        self.assertEqual(c["parcelado"].taxa_am, D("0.00"))
        txt = relatorio(pipe.comparar(cid))
        self.assertIn("R$ 292,50 — Pix", txt)
        self.assertNotIn("R$ 292,50 — cartão", txt)                                  # antes: rotulado cartão à vista


class IdentidadeNaoMudaTest(unittest.TestCase):
    def test_titulo_continua_conferindo(self):
        self.assertEqual(avaliar_identidade("Fone de Ouvido X", Consulta("Fone X", modelo="fone x"))[0], "confere")


if __name__ == "__main__":
    unittest.main()
