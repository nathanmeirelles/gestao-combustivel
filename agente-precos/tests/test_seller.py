import unittest

from precos.apis import ApiJson
from precos.config import Config
from precos.seller import Identidade, Loja, consultar_cep, consultar_cnpj, dominio_registravel_br, eh_marketplace, idade_dominio
from pathlib import Path


class R:
    def __init__(self, status=200, corpo=None):
        self.status_code, self._c = status, corpo

    def json(self):
        if self._c is None:
            raise ValueError("sem json")
        return self._c


class S:
    def __init__(self, resp):
        self.resp, self.urls = resp, []

    def get(self, url, **kw):
        self.urls.append(url)
        return self.resp


def api(resp):
    return ApiJson(Config(data_dir=Path("/tmp/x")), sessao=S(resp), dormir=lambda s: None)


def ident(**kw):
    base = {"cnpj": "00000000000191", "razao_social": "MUNDO MUSICAL COMERCIO DE INSTRUMENTOS LTDA", "nome_fantasia": None, "situacao": "ATIVA",
            "abertura": "2000-01-01", "cnae": None, "municipio": None, "uf": None, "fonte": "x", "consultado_em": "x"}
    base.update(kw)
    return Identidade(**base)


class SellerTest(unittest.TestCase):
    def test_cnpj_invalido_nao_chega_a_consultar(self):
        a = api(R(200, {}))
        i, erro = consultar_cnpj("11.111.111/1111-11", a)
        self.assertIsNone(i)
        self.assertEqual(a.sessao.urls, [])

    def test_cnpj_ativo(self):
        a = api(R(200, {"razao_social": "X LTDA", "descricao_situacao_cadastral": "ATIVA", "data_inicio_atividade": "2010-05-01"}))
        i, erro = consultar_cnpj("00.000.000/0001-91", a)
        self.assertIsNone(erro)
        self.assertTrue(i.ativa)
        self.assertGreater(i.anos_atividade, 10)

    def test_cnpj_baixado_nao_e_ativo(self):
        a = api(R(200, {"razao_social": "X", "descricao_situacao_cadastral": "BAIXADA", "data_inicio_atividade": "2010-05-01"}))
        self.assertFalse(consultar_cnpj("00000000000191", a)[0].ativa)

    def test_falha_de_api_vira_erro_nao_excecao(self):
        i, erro = consultar_cnpj("00000000000191", api(R(500, None)))
        self.assertIsNone(i)
        self.assertIn("indisponível", erro)

    def test_cep(self):
        self.assertEqual(consultar_cep("01001-000", api(R(200, {"localidade": "São Paulo"})))[0]["localidade"], "São Paulo")
        self.assertIn("não encontrado", consultar_cep("00000000", api(R(200, {"erro": True})))[1])
        self.assertIn("8 dígitos", consultar_cep("123", api(R(200, {})))[1])

    def test_rdap(self):
        corpo = {"events": [{"eventAction": "registration", "eventDate": "2019-03-01T00:00:00Z"}, {"eventAction": "last changed", "eventDate": "2025-01-01T00:00:00Z"}]}
        r, erro = idade_dominio("www.loja.com.br", api(R(200, corpo)))
        self.assertEqual((r["dominio"], r["registrado_em"]), ("loja.com.br", "2019-03-01"))
        self.assertGreater(r["idade_dias"], 365 * 5)
        self.assertIn(".br", idade_dominio("loja.com", api(R(200, corpo)))[1])

    def test_dominio_registravel(self):
        self.assertEqual(dominio_registravel_br("a.b.loja.com.br"), "loja.com.br")
        self.assertEqual(dominio_registravel_br("loja.br"), "loja.br")
        self.assertIsNone(dominio_registravel_br("loja.com"))

    def test_compatibilidade_de_nome(self):
        i = ident()
        self.assertTrue(i.compativel_com("Mundo Musical", "mundomusical.com.br"))
        self.assertTrue(i.compativel_com(None, "mundomusical.com.br"))
        self.assertFalse(i.compativel_com("Outra Loja", "outraloja.com.br"))

    def test_marketplace(self):
        self.assertTrue(eh_marketplace("produto.mercadolivre.com.br"))
        self.assertFalse(eh_marketplace("mundomusical.com.br"))


class LojaTest(unittest.TestCase):
    def test_tier_com_evidencia_exige_url(self):
        lj = Loja("x.com.br")
        with self.assertRaises(ValueError):
            lj.definir_tier("oficial", None)
        with self.assertRaises(ValueError):
            lj.definir_tier("varejista", "sem url")
        lj.definir_tier("varejista", "https://fonte.exemplo/prova")
        self.assertEqual(lj.tier, "varejista")
        lj.definir_tier("insuficiente", None)  # classificações cautelosas não exigem prova

    def test_tier_invalido(self):
        with self.assertRaises(ValueError):
            Loja("x").definir_tier("seguríssima", "https://a.b")

    def test_reputacao_exige_fonte_e_escala_valida(self):
        lj = Loja("x.com.br")
        with self.assertRaises(ValueError):
            lj.definir_reputacao(8, 10, "")
        with self.assertRaises(ValueError):
            lj.definir_reputacao(11, 10, "https://a.b")
        lj.definir_reputacao(8.2, 10, "https://a.b/perfil")
        self.assertEqual(lj.reputacao_nota, 8.2)

    def test_serializacao(self):
        lj = Loja("x.com.br", nome="X")
        lj.definir_tier("autorizado", "https://a.b")
        self.assertEqual(Loja.from_dict(lj.to_dict()).tier, "autorizado")


if __name__ == "__main__":
    unittest.main()


class ApiPoliticaTest(unittest.TestCase):
    def _api(self, respostas):
        class Sess:
            def __init__(s):
                s.n = 0

            def get(s, url, **kw):
                r = respostas[min(s.n, len(respostas) - 1)]
                s.n += 1
                return r

        esperas = []
        t = {"v": 100.0}

        def dormir(x):
            esperas.append(x)
            t["v"] += x

        a = ApiJson(Config(data_dir=Path("/tmp/x")), sessao=Sess(), dormir=dormir, relogio=lambda: t["v"])
        return a, esperas

    def test_403_sem_flag_nao_e_repetido(self):
        a, esperas = self._api([R(403, None), R(200, {"ok": 1})])
        st, corpo, erro = a.get("https://x.com/a")
        self.assertEqual((st, erro), (403, "HTTP 403"))
        self.assertEqual(a.sessao.n, 1)

    def test_403_com_flag_espera_e_repete_uma_vez_ate_dar_certo(self):
        a, esperas = self._api([R(403, None), R(200, {"ok": 1})])
        st, corpo, erro = a.get("https://x.com/a", recusa_transitoria=True)
        self.assertEqual((st, corpo, erro), (200, {"ok": 1}, None))
        self.assertIn(ApiJson.ESPERA_RECUSA_S, esperas)

    def test_403_persistente_desiste_com_erro_claro(self):
        a, _ = self._api([R(403, None)])
        st, corpo, erro = a.get("https://x.com/a", recusa_transitoria=True)
        self.assertEqual(erro, "HTTP 403")
        self.assertEqual(a.sessao.n, a.cfg.max_tentativas)

    def test_intervalo_minimo_por_host(self):
        a, esperas = self._api([R(200, {})])
        a.get("https://x.com/1")
        a.get("https://x.com/2")
        a.get("https://outro.com/3")
        self.assertEqual(len([e for e in esperas if e > 0]), 1)   # só a 2ª chamada ao mesmo host espera


class IdentidadeTolerante(unittest.TestCase):
    def test_cadastro_incompleto_nao_derruba(self):
        i = Identidade.from_dict({"cnpj": "00000000000191", "situacao": "ATIVA", "razao_social": "X LTDA"})
        self.assertTrue(i.ativa)
        self.assertIsNone(i.anos_atividade)
