import tempfile
import unittest
from pathlib import Path
from unittest import mock

from precos import render
from precos.config import Config
from precos.http import Fetcher


class FakeFetcher(Fetcher):
    """Reaproveita robots/ritmo reais, mas sem rede: robots vem de um texto fixo."""

    def __init__(self, cfg, robots_txt):
        super().__init__(cfg, dormir=lambda s: None)
        from precos.robots import Robots
        self._fixo = Robots(robots_txt) if robots_txt is not None else Robots(None, indisponivel=True, motivo="robots.txt inacessível (teste)")

    def _robots_do_host(self, esquema, host):
        return self._fixo


def cfg():
    return Config(data_dir=Path(tempfile.mkdtemp()), intervalo_min_s=0)


class RenderTest(unittest.TestCase):
    def test_url_invalida_e_host_interno_nem_abrem_navegador(self):
        c = cfg()
        with mock.patch.object(render, "_navegar") as nav:
            for u in ("ftp://x.com/a", "http://127.0.0.1:8000/x", "http://localhost/x", "http://10.0.0.5/x"):
                self.assertIsNotNone(render.renderizar_pagina(u, c, FakeFetcher(c, "")).erro, u)
            nav.assert_not_called()

    def test_robots_proibindo_nao_abre_navegador(self):
        c = cfg()
        with mock.patch.object(render, "_navegar") as nav:
            r = render.renderizar_pagina("https://loja.com.br/search?q=x", c, FakeFetcher(c, "User-agent: *\nDisallow: *?q=\n"))
            self.assertFalse(r.robots_ok)
            self.assertIn("robots.txt", r.bloqueio)
            nav.assert_not_called()

    def test_robots_inacessivel_e_erro_tecnico_nao_bloqueio(self):
        c = cfg()
        with mock.patch.object(render, "_navegar") as nav:
            r = render.renderizar_pagina("https://loja.com.br/p", c, FakeFetcher(c, None))
            self.assertIsNone(r.bloqueio)
            self.assertIn("por cautela", r.erro)
            nav.assert_not_called()

    def test_pagina_renderizada_ok(self):
        c = cfg()
        with mock.patch.object(render, "_navegar", return_value=("<html>ok</html>", "https://loja.com.br/p", 200)) as nav:
            r = render.renderizar_pagina("https://loja.com.br/p", c, FakeFetcher(c, ""))
            self.assertTrue(r.ok)
            self.assertEqual(nav.call_args.args[1], c.user_agent)        # User-Agent honesto, não de navegador comum

    def test_pagina_de_verificacao_vira_bloqueio_sem_tentar_resolver(self):
        c = cfg()
        with mock.patch.object(render, "_navegar", return_value=("<html>Just a moment...</html>", "https://loja.com.br/p", 200)):
            r = render.renderizar_pagina("https://loja.com.br/p", c, FakeFetcher(c, ""))
            self.assertFalse(r.ok)
            self.assertIn("anti-robô", r.bloqueio)

    def test_sem_playwright_ou_navegador_da_erro_claro(self):
        c = cfg()
        with mock.patch.object(render, "_navegar", side_effect=ImportError):
            self.assertIn("playwright não instalado", render.renderizar_pagina("https://loja.com.br/p", c, FakeFetcher(c, "")).erro)
        with mock.patch.object(render, "_navegar", side_effect=RuntimeError("Failed to launch chromium\nstack...")):
            r = render.renderizar_pagina("https://loja.com.br/p", c, FakeFetcher(c, ""))
            self.assertIn("falha na renderização: Failed to launch chromium", r.erro)

    def test_http_404_na_renderizacao(self):
        c = cfg()
        with mock.patch.object(render, "_navegar", return_value=("<html>nada</html>", "https://loja.com.br/p", 404)):
            self.assertEqual(render.renderizar_pagina("https://loja.com.br/p", c, FakeFetcher(c, "")).erro, "HTTP 404")


if __name__ == "__main__":
    unittest.main()
