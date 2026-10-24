import contextlib
import io
import logging
import os
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal as D
from unittest import mock

from precos import cli
from precos.apis import ApiJson
from precos.config import Config
from precos.discovery import SemProvedor, buscar, provedores_configurados
from precos.models import Evidencia, Oferta, Status
from precos.pipeline import Pipeline
from precos.store import Store
from precos.util import agora_brt


def rodar(*argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class CliBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        p = mock.patch.dict(os.environ, {"PRECOS_DATA_DIR": self.tmp, "BRAVE_API_KEY": "", "GOOGLE_CSE_KEY": "", "GOOGLE_CSE_CX": ""})
        p.start()
        self.addCleanup(p.stop)
        # impede que um .env real do projeto afete os testes
        q = mock.patch("precos.config._carregar_dotenv", lambda *_: None)
        q.start()
        self.addCleanup(q.stop)

    def store(self):
        st = Store(Config.from_env().db_path)
        self.addCleanup(st.fechar)
        return st


class ValorCliTest(unittest.TestCase):
    def test_formatos(self):
        self.assertEqual(cli.valor_cli("4.299"), D("4299"))
        self.assertEqual(cli.valor_cli("4299,90"), D("4299.90"))
        self.assertEqual(cli.valor_cli("R$ 1.234,56"), D("1234.56"))
        self.assertEqual(cli.valor_cli("4299.5"), D("4299.5"))
        with self.assertRaises(Exception):
            cli.valor_cli("abc")


class CliFluxoTest(CliBase):
    def test_nova_e_consultas(self):
        code, out, _ = rodar("nova", "Line 6 HX Stomp", "--modelo", "line 6 hx stomp", "--parcelas", "12")
        self.assertEqual(code, 0)
        self.assertIn("Consulta #1 criada", out)
        self.assertIn("Line 6 HX Stomp", rodar("consultas")[1])

    def test_loja_exige_fonte_para_classificar(self):
        code, _, err = rodar("loja", "x.com.br", "--tier", "oficial")
        self.assertEqual(code, 2)
        self.assertIn("exige --fonte", err)
        self.assertEqual(rodar("loja", "x.com.br", "--tier", "oficial", "--fonte", "https://marca.com/revendedores")[0], 0)
        self.assertEqual(self.store().loja("x.com.br").tier, "oficial")

    def test_loja_reputacao_exige_fonte_e_cnpj_valido(self):
        self.assertEqual(rodar("loja", "x.com.br", "--nota", "9")[0], 2)
        self.assertEqual(rodar("loja", "x.com.br", "--cnpj", "11.111.111/1111-11", "--fonte", "https://a.b")[0], 2)
        self.assertEqual(rodar("loja", "x.com.br", "--nota", "9", "--nota-fonte", "https://rep.exemplo/x")[0], 0)

    def test_politicas_exigem_fonte(self):
        self.assertEqual(rodar("loja", "x.com.br", "--politicas", "sim")[0], 2)

    def test_anotar_exige_fonte_e_cria_nova_observacao_confirmada(self):
        rodar("nova", "Produto X")
        st = self.store()
        o = Oferta(url="https://x.com.br/p", dominio="x.com.br", status=Status.VERIFICADA, coletado_em=agora_brt(), consulta_id=1, preco_exibido=D("1000"))
        oid = st.salvar_oferta(o)
        st.fechar()
        self.assertEqual(rodar("anotar", str(oid), "--frete", "49,90")[0], 2)           # argparse: --fonte obrigatório
        code, out, _ = rodar("anotar", str(oid), "--fonte", "carrinho 24/10 14:02", "--frete", "49,90", "--parcela", "12x358,25:sj")
        self.assertEqual(code, 0, out)
        st = self.store()
        nova = st.ultimas_ofertas(1)[0]
        self.assertNotEqual(nova.id, oid)                                                 # append-only: a original permanece
        self.assertEqual(nova.frete_valor, D("49.90"))
        self.assertEqual(nova.evid("frete_valor"), Evidencia.CONFIRMADO_DIRETAMENTE)
        self.assertEqual((nova.parcelamentos[0].n, nova.parcelamentos[0].sem_juros), (12, True))
        self.assertTrue(any("carrinho 24/10 14:02" in n for n in nova.notas))
        self.assertIsNotNone(st.oferta(oid))
        st.fechar()

    def test_anotar_pix_define_o_meio_e_a_evidencia(self):
        rodar("nova", "Produto X")
        st = self.store()
        oid = st.salvar_oferta(Oferta(url="https://x.com.br/p", dominio="x.com.br", status=Status.VERIFICADA, coletado_em=agora_brt(), consulta_id=1,
                                      preco_exibido=D("1000"), preco_pix=D("950"), meio_vista="não especificado"))
        self.assertEqual(rodar("anotar", str(oid), "--fonte", "checkout 24/10", "--pix", "940")[0], 0)
        o = self.store().ultimas_ofertas(1)[0]
        self.assertEqual((o.preco_pix, o.meio_vista, o.evid("preco_pix")), (D("940"), "pix", Evidencia.CONFIRMADO_DIRETAMENTE))

    def test_anotar_parcela_invalida(self):
        rodar("nova", "Produto X")
        st = self.store()
        oid = st.salvar_oferta(Oferta(url="https://x.com.br/p", dominio="x.com.br", status=Status.VERIFICADA, coletado_em=agora_brt(), consulta_id=1, preco_exibido=D("1")))
        st.fechar()
        self.assertEqual(rodar("anotar", str(oid), "--fonte", "x", "--parcela", "doze vezes")[0], 2)

    def test_candidato_fica_nao_confirmado(self):
        rodar("nova", "Line 6 HX Stomp", "--modelo", "line 6 hx stomp")
        code, out, _ = rodar("candidato", "https://x.com.br/hx-stomp", "--preco", "4.299", "--titulo", "Line 6 HX Stomp")
        self.assertIn("NÃO confirmado", out)
        o = self.store().ultimas_ofertas(1)[0]
        self.assertEqual((o.status, o.evid("preco_exibido")), (Status.NAO_CONFIRMADA, Evidencia.NAO_VERIFICADO))
        self.assertEqual(o.preco_exibido, D("4299"))

    def test_consulta_inexistente(self):
        with self.assertRaises(SystemExit):
            rodar_sem_captura = cli.main(["comparar", "-c", "99"])

    def test_descobrir_sem_chave_explica_o_que_fazer(self):
        rodar("nova", "x")
        code, _, err = rodar("descobrir", "line 6 hx stomp")
        self.assertEqual(code, 3)
        self.assertIn("BRAVE_API_KEY", err)


class MonitorTest(CliBase):
    def _preparar(self, preco="4000"):
        rodar("nova", "Line 6 HX Stomp", "--modelo", "line 6 hx stomp")
        st = self.store()
        o = Oferta(url="https://loja.com.br/p", dominio="loja.com.br", status=Status.VERIFICADA, coletado_em=agora_brt(), consulta_id=1,
                   preco_exibido=D(preco), titulo="Line 6 HX Stomp", identidade_produto="confere", disponibilidade="em_estoque")
        o.marcar("preco_exibido", Evidencia.CONFIRMADO_DIRETAMENTE)
        st.salvar_oferta(o)
        st.fechar()

    def _check(self, *extra):
        with mock.patch.object(Pipeline, "verificar", return_value=None), mock.patch("precos.pipeline.idade_dominio", return_value=(None, "offline")):
            return rodar("monitor", "check", *extra)

    def test_add_exige_alvo_e_avisa_que_nao_roda_sozinho(self):
        rodar("nova", "x")
        self.assertEqual(rodar("monitor", "add")[0], 2)
        code, out, _ = rodar("monitor", "add", "--alvo", "3.500")
        self.assertEqual(code, 0)
        self.assertIn("nada roda sozinho", out)

    def test_alerta_quando_atinge_o_alvo(self):
        self._preparar("4000")
        rodar("monitor", "add", "--alvo", "4500")
        code, out, _ = self._check()
        self.assertIn("ALERTA monitor #1", out)
        self.assertIn("https://loja.com.br/p", out)

    def test_sem_alerta_acima_do_alvo(self):
        self._preparar("4000")
        rodar("monitor", "add", "--alvo", "3500")
        out = self._check()[1]
        self.assertNotIn("ALERTA", out)
        self.assertIn("alvo R$ 3.500,00", out)

    def test_respeita_intervalo_entre_checagens(self):
        self._preparar("4000")
        rodar("monitor", "add", "--alvo", "3500", "--intervalo-h", "24")
        self._check()
        self.assertIn("pulado", self._check()[1])
        self.assertNotIn("pulado", self._check("--forcar")[1])

    def test_monitor_expirado_e_desativado(self):
        self._preparar()
        rodar("monitor", "add", "--alvo", "3500", "--ate", (agora_brt().date() - timedelta(days=1)).isoformat())
        self.assertIn("expirou", self._check()[1])
        self.assertEqual(self.store().monitores(), [])

    def test_remove(self):
        rodar("nova", "x")
        rodar("monitor", "add", "--alvo", "10")
        self.assertIn("desativado", rodar("monitor", "remove", "--id", "1")[1])
        self.assertEqual(rodar("monitor", "remove")[0], 2)


class DiscoveryTest(unittest.TestCase):
    CHAVE = "CHAVE-SECRETA-123"

    def _cfg(self, **kw):
        return Config(data_dir=__import__("pathlib").Path("/tmp/x"), **kw)

    def test_sem_provedor_levanta(self):
        with self.assertRaises(SemProvedor):
            buscar(self._cfg(), mock.Mock(), "x")

    def test_provedores_configurados(self):
        self.assertEqual(provedores_configurados(self._cfg(brave_key="k")), ["brave"])
        self.assertEqual(provedores_configurados(self._cfg(google_key="k")), [])           # falta o cx
        self.assertEqual(provedores_configurados(self._cfg(google_key="k", google_cx="c")), ["google_cse"])

    def test_brave_parse_dedup_e_dominios(self):
        api = mock.Mock()
        api.get.return_value = (200, {"web": {"results": [
            {"title": "A", "url": "https://a.com.br/p", "description": "d1"},
            {"title": "A de novo", "url": "https://a.com.br/p", "description": "d2"},
            {"title": "B", "url": "https://b.com.br/p", "description": "d3"}]}}, None)
        achados, erros = buscar(self._cfg(brave_key=self.CHAVE), api, "line 6 hx stomp", ["a.com.br", "b.com.br"])
        self.assertEqual([c.url for c in achados], ["https://a.com.br/p", "https://b.com.br/p"])
        self.assertEqual(erros, [])
        params = api.get.call_args.kwargs["params"]
        self.assertIn("site:a.com.br OR site:b.com.br", params["q"])
        self.assertEqual((params["country"], params["search_lang"]), ("br", "pt"))

    def test_erro_de_provedor_vira_erro_nao_excecao(self):
        api = mock.Mock()
        api.get.return_value = (401, None, "HTTP 401")
        achados, erros = buscar(self._cfg(brave_key=self.CHAVE), api, "x")
        self.assertEqual(achados, [])
        self.assertIn("brave: HTTP 401", erros[0])

    def test_chave_nao_vaza_em_repr_nem_em_logs(self):
        cfg = self._cfg(brave_key=self.CHAVE, google_key=self.CHAVE, google_cx="cx")
        self.assertNotIn(self.CHAVE, repr(cfg))
        import requests

        class SessaoQueFalha:
            def get(self, url, **kw):
                raise requests.ConnectionError(f"falha em {url} {kw.get('params')}")

        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        lg = logging.getLogger("precos")
        lg.addHandler(h)
        lg.setLevel(logging.DEBUG)
        try:
            api = ApiJson(cfg, sessao=SessaoQueFalha(), dormir=lambda s: None)
            buscar(cfg, api, "x")
        finally:
            lg.removeHandler(h)
        self.assertNotIn(self.CHAVE, buf.getvalue())


if __name__ == "__main__":
    unittest.main()


class ParseValorUsuarioTest(unittest.TestCase):
    def test_milhar_brasileiro_nunca_vira_decimal(self):
        from precos.util import parse_valor_usuario as pv
        self.assertEqual(pv("4.299"), D("4299"))              # regressão: virava 4,299
        self.assertEqual(pv("1.234.567"), D("1234567"))
        self.assertEqual(pv("4.299,90"), D("4299.90"))
        self.assertEqual(pv("4299.90"), D("4299.90"))
        self.assertEqual(pv("R$ 9.926,68"), D("9926.68"))
        self.assertEqual(pv(D("12.5")), D("12.5"))
        for ruim in ("", "abc", "-5"):
            self.assertIsNone(pv(ruim))
