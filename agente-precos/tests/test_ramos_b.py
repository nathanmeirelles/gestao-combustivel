"""CLI: comandos que só eram exercitados à mão (doctor, verificar, cnpj, cep, comparar, relatorio, descobrir, nova --cep, __main__)."""

import contextlib
import io
import json
import os
import runpy
import sys
import unittest
from pathlib import Path
from unittest import mock

from precos.discovery import Candidato
from precos.http import Fetcher, Resultado
from precos.models import Status
from precos.seller import Identidade
from precos.util import agora_brt
from tests.test_cli_discovery import CliBase, rodar

URL = "https://lojateste.com.br/hx-stomp"


def html(preco="4300.00"):
    ld = {"@context": "https://schema.org", "@type": "Product", "name": "Pedaleira Line 6 HX Stomp",
          "offers": {"@type": "Offer", "price": preco, "priceCurrency": "BRL", "availability": "https://schema.org/InStock"}}
    return f"<html><head><title>x</title><script type='application/ld+json'>{json.dumps(ld)}</script></head><body><h1>Pedaleira Line 6 HX Stomp</h1><p>R$ 4.300,00</p></body></html>"


def ident():
    return Identidade(cnpj="00000000000191", razao_social="BANCO DO BRASIL SA", nome_fantasia=None, situacao="ATIVA", abertura="1966-08-01",
                      cnae="Bancos", municipio="Brasília", uf="DF", fonte="https://brasilapi.com.br/api/cnpj/v1/00000000000191", consultado_em="x")


def sem_rede():
    """Isola o comando de qualquer rede: páginas vêm do Fetcher falso; CNPJ/RDAP indisponíveis."""
    return [
        mock.patch.object(Fetcher, "obter", side_effect=lambda url, fresco=False: Resultado(url, url, 200, html(), agora_brt())),
        mock.patch("precos.pipeline.consultar_cnpj", return_value=(None, "offline")),
        mock.patch("precos.pipeline.idade_dominio", return_value=(None, "offline")),
    ]


class Isolado(CliBase):
    def setUp(self):
        super().setUp()
        for p in sem_rede():
            p.start()
            self.addCleanup(p.stop)


class VerificarCompararRelatorioTest(Isolado):
    def test_fluxo_completo_pela_cli(self):
        self.assertEqual(rodar("nova", "Line 6 HX Stomp", "--modelo", "line 6 hx stomp")[0], 0)
        code, out, _ = rodar("verificar", URL)
        self.assertEqual(code, 0)
        self.assertIn("[VERIFICADA] lojateste.com.br", out)
        self.assertIn("confirmado diretamente", out)
        self.assertIn("(oferta #1", out)
        code, out, _ = rodar("comparar")
        self.assertIn("Ranking (vista)", out)
        self.assertIn("Ranking (parcelado)", out)
        self.assertIn("Confiança da PESQUISA: BAIXA", out)                       # loja única
        arq = Path(self.tmp) / "rel.md"
        code, out, _ = rodar("relatorio", "--saida", str(arq))
        self.assertIn("Relatório salvo", out)
        txt = arq.read_text(encoding="utf-8")
        self.assertIn("## A. Veredito", txt)
        self.assertIn("Única oferta verificada e elegível", txt)
        code, out, _ = rodar("relatorio")
        self.assertIn("## F. Conclusão", out)                                    # sem --saida imprime

    def test_verificar_mostra_alertas_de_bloqueio(self):
        rodar("nova", "x")
        bloq = Resultado(URL, URL, 403, "", agora_brt(), bloqueio="HTTP 403: o site recusou o acesso automatizado")
        with mock.patch.object(Fetcher, "obter", return_value=bloq):
            code, out, _ = rodar("verificar", URL, "--fresco")
        self.assertIn("[BLOQUEADA]", out)
        self.assertIn("não contornamos bloqueios", out)

    def test_renderizar_usa_o_renderizador(self):
        rodar("nova", "Line 6 HX Stomp", "--modelo", "line 6 hx stomp")
        res = Resultado(URL, URL, 200, html(), agora_brt())
        with mock.patch("precos.render.renderizar_pagina", return_value=res) as r:
            code, out, _ = rodar("verificar", URL, "--renderizar")
        r.assert_called_once()
        self.assertIn("[VERIFICADA]", out)

    def test_comparar_com_duas_lojas_lista_ordem_e_excluidas(self):
        rodar("nova", "Line 6 HX Stomp", "--modelo", "line 6 hx stomp")
        rodar("verificar", URL)
        rodar("candidato", "https://x.com.br/semi", "--titulo", "Line 6 HX Stomp semi nova")
        code, out, _ = rodar("comparar")
        self.assertEqual(code, 0)
        self.assertIn("1 anúncio(s) fora do veredito", out)

    def test_comando_sem_consulta(self):
        code, _, _ = rodar("comparar")
        self.assertNotEqual(code, 0)


class CnpjCepNovaTest(Isolado):
    def test_cnpj_ok_e_erro(self):
        with mock.patch("precos.cli.consultar_cnpj", return_value=(ident(), None)):
            code, out, _ = rodar("cnpj", "00.000.000/0001-91")
        self.assertEqual(code, 0)
        self.assertIn("BANCO DO BRASIL SA", out)
        self.assertIn("00.000.000/0001-91", out)
        with mock.patch("precos.cli.consultar_cnpj", return_value=(None, "CNPJ inválido")):
            code, _, err = rodar("cnpj", "123")
        self.assertEqual(code, 1)
        self.assertIn("CNPJ inválido", err)

    def test_cep_ok_e_erro(self):
        with mock.patch("precos.cli.consultar_cep", return_value=({"logradouro": "Praça da Sé", "bairro": "Sé", "localidade": "São Paulo", "uf": "SP"}, None)):
            code, out, _ = rodar("cep", "01001-000")
        self.assertEqual(code, 0)
        self.assertIn("Praça da Sé", out)
        with mock.patch("precos.cli.consultar_cep", return_value=(None, "CEP não encontrado")):
            self.assertEqual(rodar("cep", "00000000")[0], 1)

    def test_nova_com_cep_valido_e_invalido(self):
        with mock.patch("precos.cli.consultar_cep", return_value=({"localidade": "São Paulo"}, None)):
            self.assertEqual(rodar("nova", "x", "--cep", "01001-000")[0], 0)
        self.assertEqual(self.store().consulta(1).cep, "01001000")
        with mock.patch("precos.cli.consultar_cep", return_value=(None, "CEP não encontrado")):
            code, _, err = rodar("nova", "y", "--cep", "00000-000")
        self.assertEqual(code, 0)                                                # mantém o informado, mas avisa
        self.assertIn("CEP não validado", err)

    def test_nova_sem_modelo_sugere_modelo(self):
        self.assertIn("--modelo", rodar("nova", "Produto qualquer")[1])


class DescobrirTest(Isolado):
    def test_registra_candidatos_nao_confirmados(self):
        rodar("nova", "Line 6 HX Stomp", "--modelo", "line 6 hx stomp")
        achados = [Candidato("Line 6 HX Stomp novo", "https://a.com.br/hx", "R$ 4.300 trecho", "brave"),
                   Candidato("HX Stomp XL", "https://b.com.br/xl", "outro", "brave")]
        with mock.patch("precos.cli.buscar", return_value=(achados, [])):
            code, out, _ = rodar("descobrir", "line 6 hx stomp", "--dominios", "a.com.br,b.com.br", "--registrar")
        self.assertEqual(code, 0)
        self.assertIn("2 candidato(s) registrados como NÃO confirmados", out)
        ofs = self.store().ultimas_ofertas(1)
        self.assertEqual({o.status for o in ofs}, {Status.NAO_CONFIRMADA})
        xl = next(o for o in ofs if o.dominio == "b.com.br")
        self.assertEqual(xl.identidade_produto, "divergente")                    # XL é outro produto
        self.assertTrue(all(o.preco_exibido is None for o in ofs))               # trecho de busca nunca vira preço

    def test_erro_de_provedor_sai_com_1(self):
        with mock.patch("precos.cli.buscar", return_value=([], ["brave: HTTP 401"])):
            code, _, err = rodar("descobrir", "x")
        self.assertEqual(code, 1)
        self.assertIn("brave: HTTP 401", err)


class DoctorTest(CliBase):
    def _doctor(self, *extra, cnpj_ok=True):
        with mock.patch("precos.cli.consultar_cnpj", return_value=(ident() if cnpj_ok else None, None if cnpj_ok else "x")), \
                mock.patch("precos.cli.consultar_cep", return_value=({"localidade": "SP"}, None)), \
                mock.patch("precos.cli.idade_dominio", return_value=({"idade_dias": 1}, None)):
            return rodar("doctor", *extra)

    def test_tudo_ok_retorna_zero_e_lista_o_que_nao_esta_configurado(self):
        code, out, _ = self._doctor()
        self.assertEqual(code, 0)
        for trecho in ("CNPJ (BrasilAPI", "CEP (ViaCEP)", "RDAP", "busca automática", "cálculo de frete", "agendamento", "notificações"):
            self.assertIn(trecho, out)
        self.assertIn("NÃO CONFIGURADO", out)

    def test_servico_fora_do_ar_retorna_um(self):
        code, out, _ = self._doctor(cnpj_ok=False)
        self.assertEqual(code, 1)
        self.assertRegex(out, r"CNPJ \(BrasilAPI → Receita\)\s+INDISPONÍVEL")

    def test_falha_de_servico_publico_mostra_a_causa_e_o_limite_de_taxa(self):
        with mock.patch("precos.cli.consultar_cnpj", return_value=(ident(), None)), \
                mock.patch("precos.cli.consultar_cep", return_value=({"localidade": "SP"}, None)), \
                mock.patch("precos.cli.idade_dominio", return_value=(None, "RDAP indisponível (HTTP 403)")):
            code, out, _ = rodar("doctor")
        self.assertEqual(code, 1)
        self.assertRegex(out, r"RDAP Registro\.br\)\s+INDISPONÍVEL\s+[\d.]+s — RDAP indisponível \(HTTP 403\) \(provável limite de taxa")

    def test_excecao_inesperada_no_diagnostico_nao_derruba(self):
        with mock.patch("precos.cli.consultar_cnpj", side_effect=RuntimeError("boom")), \
                mock.patch("precos.cli.consultar_cep", return_value=({"localidade": "SP"}, None)), \
                mock.patch("precos.cli.idade_dominio", return_value=({"idade_dias": 1}, None)):
            code, out, _ = rodar("doctor")
        self.assertEqual(code, 1)
        self.assertIn("RuntimeError", out)

    def test_navegador_ausente_e_reportado_sem_derrubar(self):
        with mock.patch.dict(sys.modules, {"playwright": None, "playwright.sync_api": None}):
            code, out, _ = self._doctor("--navegador")
        self.assertRegex(out, r"Chromium headless\s+INDISPONÍVEL")

    def test_canais_de_notificacao_aparecem(self):
        with mock.patch.dict(os.environ, {"NTFY_TOPIC": "t", "ALERT_WEBHOOK_URL": "https://h.exemplo/x"}):
            _, out, _ = self._doctor()
        self.assertRegex(out, r"notificações\s+OK\s+ntfy, webhook")
        self.assertNotIn("https://h.exemplo/x", out)                             # segredo não é impresso


class ModuloPrincipalTest(unittest.TestCase):
    def test_python_m_precos(self):
        with mock.patch.object(sys, "argv", ["precos", "--help"]), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            runpy.run_module("precos", run_name="__main__")
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
