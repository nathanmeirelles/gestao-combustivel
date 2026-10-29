"""Fluxo manual: o usuário lê o preço no navegador (loja que bloqueia o robô) e registra com `anotar --preco`."""

import unittest
from decimal import Decimal as D

from precos.models import Evidencia, Oferta, Status
from precos.report import relatorio
from precos.pipeline import Pipeline
from precos.config import Config
from precos.util import agora_brt
from tests.test_cli_discovery import CliBase, rodar


class AnotarPrecoTest(CliBase):
    def _bloqueada(self, titulo="Pedaleira Line 6 HX Stomp", url="https://www.magazineluiza.com.br/pedaleira-line-6-hx-stomp/p/x/", **kw):
        rodar("nova", "Line 6 HX Stomp", "--modelo", "line 6 hx stomp", "--parcelas", "12")
        st = self.store()
        o = Oferta(url=url, dominio="magazineluiza.com.br", status=Status.BLOQUEADA, coletado_em=agora_brt(), consulta_id=1, titulo=titulo,
                   alertas=["HTTP 403: o site recusou o acesso automatizado"], **kw)
        oid = st.salvar_oferta(o)
        st.fechar()
        return oid

    def _ultima(self):
        return self.store().ultimas_ofertas(1)[0]

    def test_preco_lido_pelo_usuario_promove_a_oferta_bloqueada(self):
        oid = self._bloqueada()
        code, out, err = rodar("anotar", str(oid), "--fonte", "navegador, 29/10 10:12", "--preco", "4.299", "--disponibilidade", "em_estoque",
                               "--pix", "4.085,05", "--parcela", "12x358,25:sj")
        self.assertEqual(code, 0, err)
        o = self._ultima()
        self.assertEqual(o.status, Status.VERIFICADA)
        self.assertEqual(o.preco_exibido, D("4299"))
        self.assertEqual(o.evid("preco_exibido"), Evidencia.CONFIRMADO_DIRETAMENTE)
        self.assertEqual((o.fonte_dados, o.disponibilidade), ("manual", "em_estoque"))
        self.assertTrue(any("leitura manual" in a for a in o.alertas))
        self.assertTrue(any("navegador, 29/10 10:12" in n for n in o.notas))
        self.assertEqual(o.identidade_produto, "confere")

    def test_oferta_manual_entra_no_ranking_e_no_relatorio_com_a_origem(self):
        oid = self._bloqueada()
        rodar("anotar", str(oid), "--fonte", "navegador", "--preco", "4299")
        cfg = Config.from_env()
        from unittest import mock
        with mock.patch("precos.pipeline.idade_dominio", return_value=(None, "offline")):
            comp = Pipeline(cfg, self.store()).comparar(1)
        self.assertEqual(len(comp.ranking["vista"]), 1)
        self.assertTrue(comp.ranking["vista"][0].elegivel)
        txt = relatorio(comp)
        self.assertIn("R$ 4.299,00", txt)

    def test_titulo_com_variante_diferente_continua_sendo_excluido(self):
        oid = self._bloqueada(titulo=None)
        code, out, err = rodar("anotar", str(oid), "--fonte", "navegador", "--preco", "8486", "--titulo", "Pedaleira Line 6 HX Stomp XL")
        self.assertEqual(code, 0, err)
        o = self._ultima()
        self.assertEqual(o.identidade_produto, "divergente")                    # digitar à mão não contorna a checagem do modelo
        self.assertEqual(o.evid("produto"), Evidencia.DIVERGENTE)

    def test_sem_titulo_avisa_e_nao_afirma_que_confere(self):
        oid = self._bloqueada(titulo=None)
        code, out, err = rodar("anotar", str(oid), "--fonte", "navegador", "--preco", "4299")
        self.assertIn("--titulo", err)
        o = self._ultima()
        self.assertNotEqual(o.identidade_produto, "confere")
        self.assertFalse(o.evid("produto").confirmado)

    def test_sem_estoque_vira_indisponivel(self):
        oid = self._bloqueada()
        rodar("anotar", str(oid), "--fonte", "navegador", "--preco", "4299", "--disponibilidade", "sem_estoque")
        self.assertEqual(self._ultima().status, Status.INDISPONIVEL)

    def test_condicao_usado_informada_a_mao_exclui_quando_se_quer_novo(self):
        oid = self._bloqueada()
        rodar("anotar", str(oid), "--fonte", "navegador", "--preco", "3000", "--condicao", "usado")
        self.assertEqual(self._ultima().identidade_produto, "divergente")

    def test_preco_invalido_e_recusado_e_nada_e_gravado(self):
        oid = self._bloqueada()
        self.assertEqual(rodar("anotar", str(oid), "--fonte", "x", "--preco", "0")[0], 2)
        self.assertEqual(rodar("anotar", str(oid), "--fonte", "x", "--preco", "abc")[0], 2)
        self.assertEqual(self._ultima().status, Status.BLOQUEADA)

    def test_preco_manual_substitui_a_lista_derivada_do_texto(self):
        oid = self._bloqueada(preco_cartao_vista=D("325"), preco_exibido=D("292.5"))
        rodar("anotar", str(oid), "--fonte", "navegador", "--preco", "330")
        o = self._ultima()
        self.assertEqual((o.preco_exibido, o.preco_cartao_vista, o.preco_ref), (D("330"), None, D("330")))

    def test_anotar_sem_preco_nao_promove_oferta_bloqueada(self):
        oid = self._bloqueada()
        rodar("anotar", str(oid), "--fonte", "navegador", "--frete", "49,90")
        self.assertEqual(self._ultima().status, Status.BLOQUEADA)

    def test_observacao_original_permanece(self):
        oid = self._bloqueada()
        rodar("anotar", str(oid), "--fonte", "navegador", "--preco", "4299")
        self.assertEqual(self.store().oferta(oid).status, Status.BLOQUEADA)     # append-only


if __name__ == "__main__":
    unittest.main()
