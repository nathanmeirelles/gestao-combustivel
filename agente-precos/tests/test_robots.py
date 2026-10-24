import unittest

from precos.robots import Robots

# Trecho REAL de https://www.zoom.com.br/robots.txt (lido em 24/10/2026) e do da KaBuM.
ZOOM = """
User-agent: *
Allow: /*?_lc=
Allow: /psc/*/*/*
Disallow: /ajax/tracker
Disallow: /lead*
Disallow: */loja
Disallow: *?q=
Disallow: /conta/*

User-agent: Bingbot
Crawl-delay: 1
Allow: /lead*?*pla=
"""
KABUM = """
User-agent: *
Disallow: /carrinho*
Disallow: /busca/*?
Disallow: *?query=*
Disallow: *sort=price

User-agent: ia_archiver
Disallow: /
"""
UA = "agente-precos/0.1 (pesquisa)"


class RobotsTest(unittest.TestCase):
    def test_curinga_bloqueia_busca_do_zoom(self):
        r = Robots(ZOOM)
        self.assertFalse(r.permitido("https://www.zoom.com.br/search?q=line+6+hx+stomp", UA))

    def test_pagina_de_produto_liberada(self):
        r = Robots(ZOOM)
        self.assertTrue(r.permitido("https://www.zoom.com.br/pedal-guitarra/line-6-hx-stomp", UA))

    def test_prefixo_com_curinga(self):
        r = Robots(ZOOM)
        self.assertFalse(r.permitido("https://www.zoom.com.br/conta/pedidos", UA))
        self.assertFalse(r.permitido("https://www.zoom.com.br/celular/loja", UA))

    def test_allow_mais_especifico_vence(self):
        r = Robots("User-agent: *\nDisallow: /a\nAllow: /a/publico\n")
        self.assertTrue(r.permitido("https://x.com/a/publico/1", UA))
        self.assertFalse(r.permitido("https://x.com/a/privado", UA))

    def test_empate_favorece_allow(self):
        r = Robots("User-agent: *\nDisallow: /p\nAllow: /p\n")
        self.assertTrue(r.permitido("https://x.com/p", UA))

    def test_cifrao_ancora_fim(self):
        r = Robots("User-agent: *\nDisallow: /*.pdf$\n")
        self.assertFalse(r.permitido("https://x.com/doc.pdf", UA))
        self.assertTrue(r.permitido("https://x.com/doc.pdf?v=2", UA))

    def test_kabum_busca_e_query(self):
        r = Robots(KABUM)
        self.assertFalse(r.permitido("https://www.kabum.com.br/busca/hx-stomp?page=1", UA))
        self.assertFalse(r.permitido("https://www.kabum.com.br/produto?query=abc", UA))
        self.assertTrue(r.permitido("https://www.kabum.com.br/produto/123456/algum-item", UA))

    def test_grupo_do_agente_especifico_tem_prioridade(self):
        r = Robots(KABUM)
        self.assertFalse(r.permitido("https://www.kabum.com.br/produto/1", "ia_archiver/1.0"))

    def test_disallow_vazio_libera_tudo(self):
        r = Robots("User-agent: *\nDisallow:\n")
        self.assertTrue(r.permitido("https://x.com/qualquer?q=1", UA))

    def test_sem_robots_sem_regras_libera(self):
        self.assertTrue(Robots("").permitido("https://x.com/a", UA))

    def test_robots_indisponivel_e_conservador(self):
        self.assertFalse(Robots(None, indisponivel=True).permitido("https://x.com/a", UA))

    def test_crawl_delay_do_agente_generico(self):
        r = Robots("User-agent: *\nCrawl-delay: 5\n")
        self.assertEqual(r.atraso(UA), 5.0)

    def test_agentes_consecutivos_dividem_regras(self):
        r = Robots("User-agent: a\nUser-agent: b\nDisallow: /x\n")
        self.assertFalse(r.permitido("https://s.com/x", "b"))


if __name__ == "__main__":
    unittest.main()
