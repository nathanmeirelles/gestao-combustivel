"""Utilitários, HTTP, extração, banco, descoberta e renderizador: ramos de borda."""

import contextlib
import json
import sys
import tempfile
import time
import types
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

from precos import render
from precos.apis import ApiJson
from precos.config import Config
from precos.discovery import buscar
from precos.extract import extrair
from precos.http import Cache, detectar_bloqueio
from precos.models import Evidencia, Oferta, Status
from precos.seller import Identidade, consultar_cep, idade_dominio
from precos.store import Store, _novo_id
from precos.util import (D as Dec, achar_cnpjs, agora_brt, cnpj_valido, dominio, fmt_brl, fmt_cnpj, fmt_dt, gtin_valido, normalizar_url,
                         parse_brl_texto, parse_decimal_maquina, redigir_url)
from tests.test_http import PAGINA, ROBOTS_OK, FakeResp, montar

URL = "https://www.loja-exemplo.com.br/p"


def pagina(ld_blocos, corpo="<p>R$ 100,00</p>"):
    s = "".join(f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in ld_blocos)
    return f"<html><head><title>x</title>{s}</head><body>{corpo}</body></html>"


class UtilTest(unittest.TestCase):
    def test_fmt_dt_e_dinheiro(self):
        self.assertEqual(fmt_dt(None), "—")
        self.assertIn("(Brasília)", fmt_dt(agora_brt()))
        self.assertEqual(fmt_brl(None), "—")
        self.assertEqual(fmt_brl(D("-1234.5")), "R$ -1.234,50")
        self.assertEqual(fmt_brl(D("0")), "R$ 0,00")

    def test_D(self):
        self.assertIsNone(Dec(None))
        self.assertIsNone(Dec(""))
        self.assertIsNone(Dec("abc"))
        self.assertEqual(Dec(D("1.5")), D("1.5"))
        self.assertEqual(Dec(" 12.30 "), D("12.30"))

    def test_parse_maquina_e_texto(self):
        self.assertEqual(parse_decimal_maquina(12), D("12"))
        self.assertEqual(parse_decimal_maquina(12.5), D("12.5"))
        self.assertEqual(parse_decimal_maquina("1,234.56"), D("1234.56"))        # ponto por último = decimal
        self.assertEqual(parse_decimal_maquina("1.234,56"), D("1234.56"))
        self.assertEqual(parse_decimal_maquina("R$ 99,90"), D("99.90"))
        self.assertIsNone(parse_decimal_maquina("abc"))
        self.assertIsNone(parse_decimal_maquina(None))
        self.assertIsNone(parse_brl_texto(""))
        self.assertIsNone(parse_brl_texto("sem número"))
        self.assertEqual(parse_brl_texto("4299,9"), D("4299.9"))

    def test_cnpj_gtin(self):
        self.assertFalse(cnpj_valido("123"))
        self.assertFalse(cnpj_valido("00000000000192"))
        self.assertEqual(fmt_cnpj("00000000000191"), "00.000.000/0001-91")
        self.assertEqual(fmt_cnpj("123"), "123")
        self.assertEqual(achar_cnpjs("sem cnpj aqui"), [])
        self.assertFalse(gtin_valido("123"))
        self.assertFalse(gtin_valido(""))
        self.assertTrue(gtin_valido("7891000100103"))

    def test_urls(self):
        self.assertEqual(dominio("https://www.Loja.com.br/x"), "loja.com.br")
        self.assertEqual(dominio("https://sub.loja.com.br/x"), "sub.loja.com.br")
        self.assertEqual(normalizar_url("https://a.com/p?cor=azul&utm_medium=x&gclid=1&tamanho=m"), "https://a.com/p?cor=azul&tamanho=m")
        self.assertEqual(normalizar_url("https://a.com/"), "https://a.com/")
        self.assertEqual(redigir_url("https://a.com/p?apikey=1&token=2&q=ok"), "https://a.com/p?apikey=***&token=***&q=ok")


class ConfigTest(unittest.TestCase):
    def test_dotenv_le_chaves_sem_sobrescrever_o_ambiente(self):
        from precos.config import _carregar_dotenv
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d) / ".env"
            arq.write_text("# comentário\n\nBRAVE_API_KEY=\"abc 123\"\nGOOGLE_CSE_KEY='k'\nJA_DEFINIDA=do_arquivo\nlinha sem igual\nVAZIA=\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"JA_DEFINIDA": "do_ambiente"}, clear=False):
                for k in ("BRAVE_API_KEY", "GOOGLE_CSE_KEY", "VAZIA"):
                    __import__("os").environ.pop(k, None)
                _carregar_dotenv(arq)
                env = __import__("os").environ
                self.assertEqual((env["BRAVE_API_KEY"], env["GOOGLE_CSE_KEY"], env["VAZIA"]), ("abc 123", "k", ""))
                self.assertEqual(env["JA_DEFINIDA"], "do_ambiente")             # o ambiente real tem prioridade
                for k in ("BRAVE_API_KEY", "GOOGLE_CSE_KEY", "VAZIA"):
                    env.pop(k, None)

    def test_dotenv_inexistente_e_ignorado(self):
        from precos.config import _carregar_dotenv
        _carregar_dotenv(Path("/caminho/que/nao/existe/.env"))

    def test_numero_invalido_na_variavel_usa_o_padrao(self):
        with mock.patch.dict("os.environ", {"PRECOS_INTERVALO_S": "dois", "PRECOS_DESATUALIZADA_H": "3"}):
            c = Config.from_env(carregar_dotenv=False)
        self.assertEqual((c.intervalo_min_s, c.desatualizada_apos_h), (2.0, 3.0))

    def test_chaves_vindas_do_ambiente_nao_aparecem_no_repr(self):
        env = {"BRAVE_API_KEY": "SEGREDO1", "NTFY_TOPIC": "SEGREDO2", "TELEGRAM_BOT_TOKEN": "SEGREDO3", "ALERT_WEBHOOK_URL": "https://h/SEGREDO4"}
        with mock.patch.dict("os.environ", env):
            c = Config.from_env(carregar_dotenv=False)
        for seg in ("SEGREDO1", "SEGREDO2", "SEGREDO3", "SEGREDO4"):
            self.assertNotIn(seg, repr(c))
        self.assertEqual(c.brave_key, "SEGREDO1")


class ModelosTest(unittest.TestCase):
    def test_rotulos_e_risco(self):
        self.assertEqual(Evidencia.ESTIMADO.rotulo, "estimado")
        self.assertEqual(Evidencia.DIVERGENTE.rotulo, "informação divergente")
        self.assertTrue(Evidencia.CONFIRMADO_INDEPENDENTE.confirmado)
        self.assertFalse(Evidencia.ESTIMADO.confirmado)
        o = Oferta(url="u", dominio="d", status=Status.VERIFICADA, coletado_em=agora_brt())
        self.assertFalse(o.risco_alto)
        o.sinais_risco = ["[MÉDIO] x", "[ALTO] y"]
        self.assertTrue(o.risco_alto)
        self.assertEqual(o.evid("qualquer"), Evidencia.NAO_VERIFICADO)


class HttpBordaTest(unittest.TestCase):
    def test_cache_expirado_busca_de_novo(self):
        f, sess, _ = montar({"https://loja.com.br/robots.txt": ROBOTS_OK, "https://loja.com.br/p/1": [FakeResp(200, PAGINA)]}, cache_pagina_s=0)
        f.obter("https://loja.com.br/p/1")
        time.sleep(0.01)
        f.obter("https://loja.com.br/p/1")
        self.assertEqual(sess.chamadas.count("https://loja.com.br/p/1"), 2)

    def test_cache_corrompido_e_ignorado(self):
        with tempfile.TemporaryDirectory() as d:
            c = Cache(Path(d))
            c.gravar("k", {"a": 1})
            arq = next(Path(d).glob("*.json"))
            arq.write_text("{ corrompido")
            self.assertIsNone(c.ler("k", 60))

    def test_falha_ao_gravar_cache_nao_derruba(self):
        with tempfile.TemporaryDirectory() as d:
            ocupado = Path(d) / "arquivo"
            ocupado.write_text("x")
            Cache(ocupado / "subpasta").gravar("k", {"a": 1})                    # pasta impossível de criar
            self.assertTrue(True)

    def test_limite_de_bytes(self):
        grande = "<html><body>" + "x" * 50_000 + "</body></html>"
        f, _, _ = montar({"https://loja.com.br/robots.txt": ROBOTS_OK, "https://loja.com.br/p/1": [FakeResp(200, grande)]}, max_bytes=10_000)
        r = f.obter("https://loja.com.br/p/1")
        self.assertLess(len(r.texto), 20_000)

    def test_status_inesperado_vira_erro_com_codigo(self):
        f, _, _ = montar({"https://loja.com.br/robots.txt": ROBOTS_OK, "https://loja.com.br/p/1": [FakeResp(418, "chá")]})
        self.assertEqual(f.obter("https://loja.com.br/p/1").erro, "HTTP 418")

    def test_redirecionamentos_demais(self):
        rotas = {"https://loja.com.br/robots.txt": ROBOTS_OK}
        for i in range(10):
            rotas[f"https://loja.com.br/r{i}"] = [FakeResp(302, "", {"location": f"/r{i + 1}"})]
        f, _, _ = montar(rotas)
        self.assertIn("redirecionamentos demais", f.obter("https://loja.com.br/r0").erro)

    def test_charset_por_meta_e_desconhecido(self):
        corpo = '<html><head><meta charset="iso-8859-1"></head><body>açaí</body></html>'.encode("iso-8859-1")
        f, _, _ = montar({"https://loja.com.br/robots.txt": ROBOTS_OK, "https://loja.com.br/p/1": [FakeResp(200, corpo, {"Content-Type": "text/html"})]})
        self.assertIn("açaí", f.obter("https://loja.com.br/p/1").texto)
        estranho = FakeResp(200, "açaí", {"Content-Type": "text/html; charset=codificacao-que-nao-existe"})
        f2, _, _ = montar({"https://loja.com.br/robots.txt": ROBOTS_OK, "https://loja.com.br/p/1": [estranho]})
        self.assertIn("açaí", f2.obter("https://loja.com.br/p/1").texto)

    def test_deteccao_de_bloqueio_por_url_e_503(self):
        self.assertIn("captcha", detectar_bloqueio(200, "https://x.com/captcha?go=1", "<html></html>"))
        self.assertIn("503", detectar_bloqueio(503, "https://x.com/p", "<html>indisponível</html>"))
        self.assertIsNone(detectar_bloqueio(200, "https://x.com/p", "<html>página normal</html>"))
        self.assertIn("429", detectar_bloqueio(429, "https://x.com/p", ""))


class ExtracaoBordaTest(unittest.TestCase):
    def test_ld_json_vazio_e_nao_dict(self):
        html = '<html><head><script type="application/ld+json">   </script></head><body>R$ 1,00</body></html>'
        self.assertIsNone(extrair(html, URL).preco)

    def test_json_muito_aninhado_nao_estoura(self):
        no = {"x": 1}
        for _ in range(30):
            no = {"filho": no}
        self.assertIsNone(extrair(pagina([no]), URL).preco)

    def test_oferta_nao_dict_e_aggregate_com_ofertas_aninhadas(self):
        p = {"@type": "Product", "name": "X", "offers": ["lixo", {"@type": "AggregateOffer", "priceCurrency": "BRL", "lowPrice": "50.00",
                                                                   "offers": [{"@type": "Offer", "price": "55.00", "priceCurrency": "BRL", "availability": "InStock"}]}]}
        ex = extrair(pagina([p], "<p>R$ 55,00</p>"), URL)
        self.assertEqual(ex.preco, D("55.00"))            # prefere a oferta explícita em estoque ao lowPrice agregado

    def test_preco_zero_ou_negativo_e_ignorado(self):
        p = {"@type": "Product", "name": "X", "offers": {"@type": "Offer", "price": "0", "priceCurrency": "BRL"}}
        self.assertIsNone(extrair(pagina([p]), URL).preco)

    def test_grupo_de_variacoes_com_uma_variante(self):
        p = {"@type": "ProductGroup", "name": "Grupo", "hasVariant": [{"@type": "Product", "name": "Var", "sku": "V1",
                                                                      "offers": {"@type": "Offer", "price": "70.00", "priceCurrency": "BRL"}}]}
        ex = extrair(pagina([p], "<p>R$ 70,00</p>"), URL)
        self.assertEqual(ex.preco, D("70.00"))
        self.assertTrue(any("variações" in a for a in ex.avisos))

    def test_grupo_com_varias_variantes_pega_a_menor_em_estoque_e_avisa(self):
        def var(sku, preco, disp):
            return {"@type": "Product", "name": f"Camisa {sku}", "sku": sku,
                    "offers": {"@type": "Offer", "price": preco, "priceCurrency": "BRL", "availability": f"https://schema.org/{disp}"}}
        p = {"@type": "ProductGroup", "name": "Camisa", "hasVariant": [var("P", "80.00", "InStock"), var("M", "90.00", "InStock"), var("G", "50.00", "OutOfStock")]}
        ex = extrair(pagina([p], "<p>R$ 80,00</p>"), URL)
        self.assertFalse(ex.listagem)                               # regressão: variantes não são fichas distintas
        self.assertEqual(ex.preco, D("80.00"))                     # a de R$ 50 está sem estoque
        self.assertTrue(any("variações" in a for a in ex.avisos))
        self.assertTrue(any("preços diferentes" in a for a in ex.avisos))

    def test_grupo_ao_lado_de_outro_produto_continua_sendo_listagem(self):
        grupo = {"@type": "ProductGroup", "name": "G", "hasVariant": [{"@type": "Product", "name": "G1", "sku": "1", "offers": {"@type": "Offer", "price": "10", "priceCurrency": "BRL"}}]}
        outro = {"@type": "Product", "name": "Outro", "sku": "2", "offers": {"@type": "Offer", "price": "20", "priceCurrency": "BRL"}}
        self.assertTrue(extrair(pagina([grupo, outro]), URL).listagem)

    def test_parcelas_fora_da_faixa_sao_ignoradas(self):
        p = {"@type": "Product", "name": "X", "offers": {"@type": "Offer", "price": "1000.00", "priceCurrency": "BRL"}}
        corpo = "<p>R$ 1.000,00</p><p>1x de R$ 1.000,00</p><p>36x de R$ 30,00</p><p>10x de R$ 100,00 sem juros</p>"
        ex = extrair(pagina([p], corpo), URL)
        self.assertEqual([x.n for x in ex.parcelamentos], [10])


class BancoBordaTest(unittest.TestCase):
    def test_id_nulo_falha_alto(self):
        cur = mock.Mock(lastrowid=None)
        with self.assertRaises(RuntimeError):
            _novo_id(cur)

    def test_transacao_com_erro_faz_rollback(self):
        s = Store(":memory:")
        self.addCleanup(s.fechar)
        with self.assertRaises(ValueError), s._tx():
            s.db.execute("INSERT INTO consultas(criada_em, payload) VALUES ('x','{}')")
            raise ValueError("falha no meio")
        self.assertIsNone(s.ultima_consulta_id())

    def test_atualizar_monitor_inexistente(self):
        s = Store(":memory:")
        self.addCleanup(s.fechar)
        with self.assertRaises(KeyError):
            s.atualizar_monitor(99, x=1)
        self.assertFalse(s.desativar_monitor(99))


class SellerBordaTest(unittest.TestCase):
    class R:
        def __init__(self, status, corpo):
            self.status_code, self._c = status, corpo

        def json(self):
            return self._c

    def _api(self, status, corpo):
        sess = mock.Mock()
        sess.get.return_value = self.R(status, corpo)
        return ApiJson(Config(data_dir=Path("/tmp/x")), sessao=sess, dormir=lambda s: None)

    def test_cep_com_erro_de_api(self):
        _, erro = consultar_cep("01001000", self._api(500, None))
        self.assertIn("indisponível", erro)

    def test_rdap_sem_registro_e_data_ilegivel(self):
        self.assertIn("sem data de registro", idade_dominio("a.com.br", self._api(200, {"events": [{"eventAction": "last changed"}]}))[1])
        self.assertIn("ilegível", idade_dominio("a.com.br", self._api(200, {"events": [{"eventAction": "registration", "eventDate": "xx"}]}))[1])

    def test_identidade_serializacao_e_abertura_invalida(self):
        i = Identidade.from_dict({"cnpj": "00000000000191", "abertura": "data-ruim", "situacao": "ATIVA"})
        self.assertIsNone(i.anos_atividade)
        self.assertEqual(i.to_dict()["cnpj"], "00000000000191")


class DiscoveryGoogleTest(unittest.TestCase):
    def test_google_cse_parse(self):
        api = mock.Mock()
        api.get.return_value = (200, {"items": [{"title": "T", "link": "https://a.com.br/p", "snippet": "s"}]}, None)
        achados, erros = buscar(Config(data_dir=Path("/tmp/x"), google_key="k", google_cx="c"), api, "x")
        self.assertEqual([(c.url, c.provedor) for c in achados], [("https://a.com.br/p", "google_cse")])
        self.assertEqual(api.get.call_args.kwargs["params"]["gl"], "br")

    def test_google_com_erro(self):
        api = mock.Mock()
        api.get.return_value = (403, None, "HTTP 403")
        achados, erros = buscar(Config(data_dir=Path("/tmp/x"), google_key="k", google_cx="c"), api, "x")
        self.assertEqual((achados, erros), ([], ["google_cse: HTTP 403"]))

    def test_dois_provedores_somam_sem_repetir(self):
        api = mock.Mock()
        api.get.side_effect = [(200, {"web": {"results": [{"title": "A", "url": "https://a.com.br/p", "description": ""}]}}, None),
                               (200, {"items": [{"title": "A2", "link": "https://a.com.br/p", "snippet": ""}, {"title": "B", "link": "https://b.com.br/p", "snippet": ""}]}, None)]
        achados, _ = buscar(Config(data_dir=Path("/tmp/x"), brave_key="k", google_key="g", google_cx="c"), api, "x", limite=10)
        self.assertEqual([c.url for c in achados], ["https://a.com.br/p", "https://b.com.br/p"])


# --------------------------------------------------------------------------- renderizador com Playwright simulado
class _Rota:
    def __init__(self, url):
        self.request = types.SimpleNamespace(url=url)
        self.abortada = self.continuada = False

    def abort(self):
        self.abortada = True

    def continue_(self):
        self.continuada = True


class _Pag:
    url = "https://loja.com.br/final"

    def __init__(self, esp):
        self.esp = esp

    def goto(self, url, wait_until, timeout):
        self.esp["goto"] = (url, wait_until, timeout)
        return types.SimpleNamespace(status=200)

    def wait_for_load_state(self, estado, timeout):
        if self.esp.get("networkidle_falha"):
            raise self.esp["Error"]("timeout")

    def content(self):
        return "<html>ok</html>"


class _Ctx:
    def __init__(self, esp):
        self.esp = esp

    def route(self, padrao, handler):
        self.esp["rota"] = (padrao, handler)

    def new_page(self):
        return _Pag(self.esp)


class _Nav:
    def __init__(self, esp):
        self.esp = esp

    def new_context(self, **kw):
        self.esp["contexto"] = kw
        return _Ctx(self.esp)

    def close(self):
        self.esp["fechou"] = True


class _Chromium:
    def __init__(self, esp):
        self.esp = esp

    def launch(self, **kw):
        self.esp["launch"] = kw
        return _Nav(self.esp)


def playwright_falso(esp):
    class PwError(Exception):
        pass

    esp["Error"] = PwError

    @contextlib.contextmanager
    def sync_playwright():
        yield types.SimpleNamespace(chromium=_Chromium(esp))

    api = types.ModuleType("playwright.sync_api")
    api.Error, api.sync_playwright = PwError, sync_playwright
    raiz = types.ModuleType("playwright")
    raiz.sync_api = api
    return {"playwright": raiz, "playwright.sync_api": api}


class NavegadorSimuladoTest(unittest.TestCase):
    def _rodar(self, esp, **kw):
        with mock.patch.dict(sys.modules, playwright_falso(esp)):
            return render._navegar("https://loja.com.br/p", "agente-precos/0.1", kw.pop("exe", None), 5000, **kw)

    def test_parametros_e_resultado(self):
        esp = {}
        html, final, status = self._rodar(esp, exe="/opt/chrome")
        self.assertEqual((html, final, status), ("<html>ok</html>", "https://loja.com.br/final", 200))
        self.assertEqual(esp["launch"], {"executable_path": "/opt/chrome"})
        self.assertEqual(esp["contexto"]["user_agent"], "agente-precos/0.1")      # User-Agent honesto
        self.assertEqual((esp["contexto"]["locale"], esp["contexto"]["timezone_id"]), ("pt-BR", "America/Sao_Paulo"))
        self.assertEqual(esp["goto"], ("https://loja.com.br/p", "domcontentloaded", 5000))
        self.assertTrue(esp["fechou"])

    def test_sem_caminho_usa_chromium_padrao(self):
        esp = {}
        self._rodar(esp)
        self.assertEqual(esp["launch"], {})

    def test_rota_aborta_hosts_internos_e_deixa_passar_os_publicos(self):
        esp = {}
        self._rodar(esp)
        _, handler = esp["rota"]
        for interno in ("http://169.254.169.254/", "http://localhost:9/x", "http://10.1.2.3/", "file:///etc/passwd"):
            r = _Rota(interno)
            handler(r)
            self.assertTrue(r.abortada and not r.continuada, interno)
        publica = _Rota("https://cdn.loja.com.br/app.js")
        handler(publica)
        self.assertTrue(publica.continuada and not publica.abortada)

    def test_sem_bloqueio_nao_registra_rota(self):
        esp = {}
        self._rodar(esp, bloquear_internos=False)
        self.assertNotIn("rota", esp)

    def test_networkidle_que_estoura_e_tolerado_e_o_navegador_fecha(self):
        esp = {"networkidle_falha": True}
        self.assertEqual(self._rodar(esp)[2], 200)
        self.assertTrue(esp["fechou"])


if __name__ == "__main__":
    unittest.main()
