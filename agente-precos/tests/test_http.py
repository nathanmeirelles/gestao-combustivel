import tempfile
import unittest
from pathlib import Path

import requests

from precos.config import Config
from precos.http import Fetcher, detectar_bloqueio


class FakeResp:
    def __init__(self, status=200, corpo="", headers=None):
        self.status_code = status
        self.headers = {"Content-Type": "text/html; charset=utf-8", **(headers or {})}
        self._corpo = corpo.encode() if isinstance(corpo, str) else corpo

    def iter_content(self, chunk_size=65536):
        yield self._corpo

    def close(self):
        pass


class FakeSessao:
    """Mapeia URL -> lista de respostas (consumidas em ordem; a última se repete)."""

    def __init__(self, rotas):
        self.rotas, self.chamadas = rotas, []

    def get(self, url, **kw):
        self.chamadas.append(url)
        fila = self.rotas.get(url)
        if fila is None:
            return FakeResp(404, "nao")
        item = fila.pop(0) if len(fila) > 1 else fila[0]
        if isinstance(item, Exception):
            raise item
        return item


def montar(rotas, **cfg):
    tmp = tempfile.mkdtemp()
    base = {"data_dir": Path(tmp), "intervalo_min_s": 2.0, "max_tentativas": 3}
    base.update(cfg)
    sess = FakeSessao(rotas)
    relogio = {"t": 1000.0}
    esperas = []

    def dormir(s):
        esperas.append(s)
        relogio["t"] += s

    f = Fetcher(Config(**base), sessao=sess, dormir=dormir, relogio=lambda: relogio["t"])
    return f, sess, esperas


ROBOTS_OK = [FakeResp(200, "User-agent: *\nDisallow: /search*\n")]
PAGINA = "<html><head><title>Produto</title></head><body>ok</body></html>"


class FetcherTest(unittest.TestCase):
    def test_robots_bloqueia_antes_de_pedir_a_pagina(self):
        f, sess, _ = montar({"https://loja.com.br/robots.txt": ROBOTS_OK, "https://loja.com.br/search?q=x": [FakeResp(200, PAGINA)]})
        r = f.obter("https://loja.com.br/search?q=x")
        self.assertFalse(r.robots_ok)
        self.assertIn("robots.txt", r.bloqueio)
        self.assertNotIn("https://loja.com.br/search?q=x", sess.chamadas)

    def test_pagina_ok_e_cache_preserva_horario_original(self):
        f, sess, _ = montar({"https://loja.com.br/robots.txt": ROBOTS_OK, "https://loja.com.br/p/1": [FakeResp(200, PAGINA)]})
        a = f.obter("https://loja.com.br/p/1")
        b = f.obter("https://loja.com.br/p/1")
        self.assertTrue(a.ok)
        self.assertTrue(b.do_cache)
        self.assertEqual(a.coletado_em, b.coletado_em)
        self.assertEqual(sess.chamadas.count("https://loja.com.br/p/1"), 1)

    def test_fresco_ignora_cache(self):
        f, sess, _ = montar({"https://loja.com.br/robots.txt": ROBOTS_OK, "https://loja.com.br/p/1": [FakeResp(200, PAGINA)]})
        f.obter("https://loja.com.br/p/1")
        f.obter("https://loja.com.br/p/1", fresco=True)
        self.assertEqual(sess.chamadas.count("https://loja.com.br/p/1"), 2)

    def test_403_e_bloqueio_sem_retentativa(self):
        f, sess, _ = montar({"https://loja.com.br/robots.txt": ROBOTS_OK, "https://loja.com.br/p/1": [FakeResp(403, "negado")]})
        r = f.obter("https://loja.com.br/p/1")
        self.assertIn("recusou", r.bloqueio)
        self.assertFalse(r.ok)
        self.assertEqual(sess.chamadas.count("https://loja.com.br/p/1"), 1)

    def test_erro_transitorio_e_retentado(self):
        f, sess, esperas = montar({
            "https://loja.com.br/robots.txt": ROBOTS_OK,
            "https://loja.com.br/p/1": [requests.ConnectTimeout("t"), FakeResp(502, ""), FakeResp(200, PAGINA)],
        })
        r = f.obter("https://loja.com.br/p/1")
        self.assertTrue(r.ok)
        self.assertEqual(sess.chamadas.count("https://loja.com.br/p/1"), 3)
        self.assertTrue(any(e >= 1 for e in esperas))   # backoff aplicado

    def test_falha_persistente_vira_erro_nao_excecao(self):
        f, _, _ = montar({"https://loja.com.br/robots.txt": ROBOTS_OK, "https://loja.com.br/p/1": [requests.ConnectionError("x")]})
        r = f.obter("https://loja.com.br/p/1")
        self.assertIn("falha de rede", r.erro)

    def test_redirecionamento_para_captcha_e_bloqueio(self):
        f, _, _ = montar({
            "https://loja.com.br/robots.txt": ROBOTS_OK,
            "https://loja.com.br/p/1": [FakeResp(302, "", {"Location": "https://loja.com.br/gz/account-verification?go=x"})],
        })
        r = f.obter("https://loja.com.br/p/1")
        self.assertIn("verificação", r.bloqueio)

    def test_redirecionamento_respeita_robots_do_destino(self):
        f, sess, _ = montar({
            "https://loja.com.br/robots.txt": ROBOTS_OK,
            "https://loja.com.br/p/1": [FakeResp(301, "", {"Location": "/search?q=x"})],
            "https://loja.com.br/search?q=x": [FakeResp(200, PAGINA)],
        })
        r = f.obter("https://loja.com.br/p/1")
        self.assertFalse(r.robots_ok)
        self.assertNotIn("https://loja.com.br/search?q=x", sess.chamadas)

    def test_robots_5xx_e_conservador(self):
        f, sess, _ = montar({"https://loja.com.br/robots.txt": [FakeResp(500, "")], "https://loja.com.br/p/1": [FakeResp(200, PAGINA)]})
        r = f.obter("https://loja.com.br/p/1")
        self.assertFalse(r.robots_ok)
        self.assertNotIn("https://loja.com.br/p/1", sess.chamadas)

    def test_sem_robots_404_libera(self):
        f, _, _ = montar({"https://loja.com.br/robots.txt": [FakeResp(404, "")], "https://loja.com.br/p/1": [FakeResp(200, PAGINA)]})
        self.assertTrue(f.obter("https://loja.com.br/p/1").ok)

    def test_intervalo_entre_requisicoes_ao_mesmo_host(self):
        f, _, esperas = montar({
            "https://loja.com.br/robots.txt": ROBOTS_OK,
            "https://loja.com.br/p/1": [FakeResp(200, PAGINA)],
            "https://loja.com.br/p/2": [FakeResp(200, PAGINA)],
        })
        f.obter("https://loja.com.br/p/1")
        f.obter("https://loja.com.br/p/2")
        self.assertTrue(esperas and all(e <= 2.0 + 1e-6 for e in esperas) and any(e > 0 for e in esperas))

    def test_host_interno_recusado(self):
        f, sess, _ = montar({})
        for u in ("http://localhost:8080/x", "http://127.0.0.1/x", "http://192.168.0.10/x", "ftp://x.com/a"):
            self.assertIsNotNone(f.obter(u).erro, u)
        self.assertEqual(sess.chamadas, [])

    def test_404_vira_erro_claro(self):
        f, _, _ = montar({"https://loja.com.br/robots.txt": ROBOTS_OK, "https://loja.com.br/p/9": [FakeResp(404, "nada")]})
        self.assertIn("não encontrada", f.obter("https://loja.com.br/p/9").erro)


class RedirecionamentoSeguroTest(unittest.TestCase):
    """Um site público não pode nos redirecionar para a rede interna nem para esquemas estranhos (SSRF)."""

    def _redirect_para(self, destino):
        return montar({
            "https://loja.com.br/robots.txt": ROBOTS_OK,
            "https://loja.com.br/p/1": [FakeResp(302, "", {"location": destino})],
        })

    def test_destinos_nao_permitidos_nunca_sao_requisitados(self):
        for destino in ("http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8080/admin", "http://localhost/x",
                        "http://192.168.0.1/", "http://10.0.0.7/x", "ftp://loja.com.br/arquivo", "http:///sem-host"):
            f, sess, _ = self._redirect_para(destino)
            r = f.obter("https://loja.com.br/p/1")
            self.assertFalse(r.ok, destino)
            self.assertIn("redirecionamento recusado", r.erro or "", destino)
            self.assertEqual([u for u in sess.chamadas if u not in ("https://loja.com.br/robots.txt", "https://loja.com.br/p/1")], [], destino)

    def test_robots_que_redireciona_para_rede_interna_e_recusado(self):
        f, sess, _ = montar({
            "https://loja.com.br/robots.txt": [FakeResp(301, "", {"location": "http://169.254.169.254/robots.txt"})],
            "https://loja.com.br/p/1": [FakeResp(200, PAGINA)],
        })
        r = f.obter("https://loja.com.br/p/1")
        self.assertFalse(r.ok)
        self.assertIn("destino não permitido", r.erro)
        self.assertNotIn("http://169.254.169.254/robots.txt", sess.chamadas)
        self.assertNotIn("https://loja.com.br/p/1", sess.chamadas)

    def test_redirecionamento_normal_continua_funcionando(self):
        f, _, _ = montar({
            "https://loja.com.br/robots.txt": ROBOTS_OK,
            "https://loja.com.br/p/1": [FakeResp(302, "", {"location": "/p/2"})],
            "https://loja.com.br/p/2": [FakeResp(200, PAGINA)],
        })
        self.assertTrue(f.obter("https://loja.com.br/p/1").ok)


class CausaDaFalhaTest(unittest.TestCase):
    def _falha(self, excecao):
        f, _, _ = montar({"https://loja.com.br/robots.txt": ROBOTS_OK, "https://loja.com.br/p/1": [excecao]})
        return f.obter("https://loja.com.br/p/1")

    def test_causas_legiveis_sem_expor_url(self):
        casos = {
            "certificado/TLS": requests.exceptions.SSLError("falha em https://loja.com.br/p/1?token=SEGREDO"),
            "tempo esgotado": requests.exceptions.ReadTimeout("t"),
            "conexão recusada": requests.exceptions.ConnectionError("c"),
            "política de acesso": requests.exceptions.ProxyError("p"),
        }
        for trecho, exc in casos.items():
            r = self._falha(exc)
            self.assertIn(trecho, r.erro, trecho)
            self.assertNotIn("SEGREDO", r.erro)

    def test_falha_no_robots_tambem_informa_a_causa(self):
        f, _, _ = montar({"https://loja.com.br/robots.txt": [requests.exceptions.SSLError("x")], "https://loja.com.br/p/1": [FakeResp(200, PAGINA)]})
        r = f.obter("https://loja.com.br/p/1")
        self.assertFalse(r.robots_ok)
        self.assertIn("certificado/TLS", r.erro)


class BloqueioTest(unittest.TestCase):
    def test_pagina_de_desafio_pequena(self):
        self.assertTrue(detectar_bloqueio(200, "https://x.com", "<html>Just a moment...</html>"))

    def test_pagina_grande_que_menciona_captcha_nao_e_bloqueio(self):
        html = "<html>" + ("x" * 50_000) + "reCAPTCHA no login</html>"
        self.assertIsNone(detectar_bloqueio(200, "https://x.com", html))


if __name__ == "__main__":
    unittest.main()


class RegressoesReaisTest(unittest.TestCase):
    """Bugs que só apareceram contra sites reais (24/10/2026)."""

    def test_cabecalho_location_em_minusculas_e_seguido(self):
        f, sess, _ = montar({
            "https://loja.com.br/robots.txt": ROBOTS_OK,
            "https://loja.com.br/p/1": [FakeResp(302, "", {"location": "https://loja.com.br/p/2"})],
            "https://loja.com.br/p/2": [FakeResp(200, PAGINA)],
        })
        r = f.obter("https://loja.com.br/p/1")
        self.assertTrue(r.ok)
        self.assertEqual(r.url_final, "https://loja.com.br/p/2")

    def test_robots_txt_com_redirecionamento_e_seguido(self):
        f, sess, _ = montar({
            "https://loja.com.br/robots.txt": [FakeResp(301, "", {"Location": "https://www.loja.com.br/robots.txt"})],
            "https://www.loja.com.br/robots.txt": [FakeResp(200, "User-agent: *\nDisallow: /privado\n")],
            "https://loja.com.br/p/1": [FakeResp(200, PAGINA)],
            "https://loja.com.br/privado": [FakeResp(200, PAGINA)],
        })
        self.assertTrue(f.obter("https://loja.com.br/p/1").ok)                 # antes: negado por engano
        self.assertFalse(f.obter("https://loja.com.br/privado").robots_ok)    # regra do destino aplicada

    def test_falha_de_rede_no_robots_explica_o_motivo(self):
        f, _, _ = montar({"https://loja.com.br/robots.txt": [requests.ConnectionError("proxy 502")]})
        r = f.obter("https://loja.com.br/p/1")
        self.assertFalse(r.robots_ok)
        self.assertIn("não foi possível baixar o robots.txt", r.erro)
        self.assertIn("conexão recusada ou interrompida", r.erro)   # a causa aparece
        self.assertIsNone(r.bloqueio)          # problema do ambiente não é "bloqueio do site"
