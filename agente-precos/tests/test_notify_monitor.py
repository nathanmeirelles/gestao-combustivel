import io
import logging
import os
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

import requests

from precos import notify
from precos.config import Config
from precos.models import Evidencia, Oferta, Status
from precos.pipeline import Pipeline
from precos.util import agora_brt
from tests.test_cli_discovery import CliBase, rodar

TOKEN = "123456:SEGREDO-DO-BOT"
TOPICO = "topico-secreto-aleatorio"
HOOK = "https://hooks.exemplo.com/T000/SEGREDO-WEBHOOK"


class Resp:
    def __init__(self, status=200):
        self.status_code = status


def cfg(**kw):
    return Config(data_dir=Path(tempfile.mkdtemp()), **kw)


class NotifyTest(unittest.TestCase):
    def test_sem_canais_nao_envia_nada(self):
        sessao = mock.Mock()
        self.assertEqual(notify.notificar(cfg(), "t", "m", sessao=sessao), [])
        sessao.post.assert_not_called()

    def test_canais_configurados(self):
        self.assertEqual(notify.canais_configurados(cfg(ntfy_topic="x", webhook_url="u", telegram_token="t", telegram_chat="c")), ["ntfy", "webhook", "telegram"])
        self.assertEqual(notify.canais_configurados(cfg(telegram_token="t")), [])           # falta o chat

    def test_ntfy_publica_por_json_com_utf8_e_topico_fora_da_url(self):
        sessao = mock.Mock()
        sessao.post.return_value = Resp(200)
        r = notify.notificar(cfg(ntfy_topic=TOPICO, ntfy_token="tk"), "Preço-alvo atingido", "Pedaleira: R$ 3.999,00 — açaí", sessao=sessao)
        self.assertEqual(r, [("ntfy", True, None)])
        args, kw = sessao.post.call_args
        self.assertEqual(args[0], "https://ntfy.sh")
        self.assertNotIn(TOPICO, args[0])
        self.assertEqual(kw["json"], {"topic": TOPICO, "title": "Preço-alvo atingido", "message": "Pedaleira: R$ 3.999,00 — açaí"})
        self.assertEqual(kw["headers"]["Authorization"], "Bearer tk")

    def test_webhook_e_telegram(self):
        sessao = mock.Mock()
        sessao.post.return_value = Resp(204)
        r = notify.notificar(cfg(webhook_url=HOOK, telegram_token=TOKEN, telegram_chat="42"), "T", "M", sessao=sessao)
        self.assertEqual([x[:2] for x in r], [("webhook", True), ("telegram", True)])
        webhook, telegram = sessao.post.call_args_list
        self.assertEqual(webhook.args[0], HOOK)
        self.assertEqual(webhook.kwargs["json"]["mensagem"], "M")
        self.assertEqual(telegram.args[0], f"https://api.telegram.org/bot{TOKEN}/sendMessage")
        self.assertEqual(telegram.kwargs["json"]["chat_id"], "42")

    def test_http_de_erro_e_falha_sem_excecao(self):
        sessao = mock.Mock()
        sessao.post.return_value = Resp(500)
        self.assertEqual(notify.notificar(cfg(webhook_url=HOOK), "T", "M", sessao=sessao), [("webhook", False, "HTTP 500")])

    def test_segredos_nao_vazam_em_logs_nem_no_resultado_nem_no_repr(self):
        c = cfg(ntfy_topic=TOPICO, webhook_url=HOOK, telegram_token=TOKEN, telegram_chat="42", ntfy_token="NTFYTOKEN")
        for segredo in (TOKEN, TOPICO, HOOK, "NTFYTOKEN"):
            self.assertNotIn(segredo, repr(c))
        sessao = mock.Mock()
        # a exceção do requests carrega a URL (com o token) na mensagem — é exatamente o que NÃO pode ser registrado
        sessao.post.side_effect = requests.ConnectionError(f"falha em https://api.telegram.org/bot{TOKEN}/sendMessage e {HOOK} e {TOPICO}")
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        lg = logging.getLogger("precos")
        lg.addHandler(h)
        lg.setLevel(logging.DEBUG)
        try:
            r = notify.notificar(c, "T", "M", sessao=sessao)
        finally:
            lg.removeHandler(h)
        self.assertTrue(all(not ok for _, ok, _ in r))
        saida = buf.getvalue() + repr(r)
        for segredo in (TOKEN, TOPICO, HOOK, "NTFYTOKEN"):
            self.assertNotIn(segredo, saida)


class MonitorBase(CliBase):
    def _oferta(self, url="https://loja.com.br/p", status=Status.VERIFICADA, preco="4000", alertas=None, dominio="loja.com.br"):
        st = self.store()
        o = Oferta(url=url, dominio=dominio, status=status, coletado_em=agora_brt(), consulta_id=1, preco_exibido=D(preco), titulo="Line 6 HX Stomp",
                   identidade_produto="confere", disponibilidade="em_estoque", alertas=alertas or [])
        o.marcar("preco_exibido", Evidencia.CONFIRMADO_DIRETAMENTE)
        st.salvar_oferta(o)
        st.fechar()

    def _preparar(self, **kw):
        rodar("nova", "Line 6 HX Stomp", "--modelo", "line 6 hx stomp")
        self._oferta(**kw)

    def _check(self, *extra, env=None):
        ambiente = {"PRECOS_DATA_DIR": self.tmp, **(env or {})}
        with mock.patch.dict(os.environ, ambiente), \
                mock.patch.object(Pipeline, "verificar", return_value=None) as ver, \
                mock.patch("precos.pipeline.idade_dominio", return_value=(None, "offline")), \
                mock.patch("precos.notify.requests.post") as post:
            post.return_value = Resp(200)
            res = rodar("monitor", "check", *extra)
        return res, ver, post


class MonitorReverificacaoTest(MonitorBase):
    def test_nao_reinsiste_em_site_que_bloqueou_nem_em_pagina_sem_preco(self):
        rodar("nova", "Line 6 HX Stomp", "--modelo", "line 6 hx stomp")
        self._oferta("https://ok.com.br/p", Status.VERIFICADA, dominio="ok.com.br")
        self._oferta("https://bloqueada.com.br/p", Status.BLOQUEADA, dominio="bloqueada.com.br", alertas=["HTTP 403"])
        self._oferta("https://semprecoo.com.br/p", Status.ERRO, dominio="semprecoo.com.br", alertas=["preço não encontrado em dados estruturados"])
        self._oferta("https://rede.com.br/p", Status.ERRO, dominio="rede.com.br", alertas=["falha técnica (não é recusa do site)"])
        self._oferta("https://candidato.com.br/p", Status.NAO_CONFIRMADA, dominio="candidato.com.br")
        rodar("monitor", "add", "--alvo", "3500")
        (code, out, _), ver, _ = self._check()
        chamadas = sorted(c.args[1] for c in ver.call_args_list)
        self.assertEqual(chamadas, ["https://candidato.com.br/p", "https://ok.com.br/p", "https://rede.com.br/p"])
        self.assertIn("2 anúncio(s) não reverificados", out)


class MonitorNotificacaoTest(MonitorBase):
    CANAL = {"NTFY_TOPIC": TOPICO}

    def test_sem_flag_notificar_nao_envia_mesmo_com_canal(self):
        self._preparar(preco="4000")
        rodar("monitor", "add", "--alvo", "4500")
        (code, out, _), _, post = self._check(env=self.CANAL)
        self.assertIn("ALERTA monitor #1", out)
        post.assert_not_called()

    def test_notifica_uma_vez_e_nao_repete_o_mesmo_preco(self):
        self._preparar(preco="4000")
        rodar("monitor", "add", "--alvo", "4500")
        (_, out1, _), _, post = self._check("--notificar", env=self.CANAL)
        self.assertIn("notificação via ntfy: enviada", out1)
        self.assertEqual(post.call_count, 1)
        payload = post.call_args.kwargs["json"]
        self.assertIn("R$ 4.000,00", payload["message"])
        self.assertIn("https://loja.com.br/p", payload["message"])
        self.assertNotIn("CEP", payload["message"])
        (_, out2, _), _, post2 = self._check("--notificar", "--forcar", env=self.CANAL)
        self.assertIn("(já notificado)", out2)
        post2.assert_not_called()

    def test_preco_novo_notifica_de_novo(self):
        self._preparar(preco="4000")
        rodar("monitor", "add", "--alvo", "4500")
        self._check("--notificar", env=self.CANAL)
        self._oferta(preco="3800")
        (_, out, _), _, post = self._check("--notificar", "--forcar", env=self.CANAL)
        self.assertEqual(post.call_count, 1)
        self.assertIn("R$ 3.800,00", post.call_args.kwargs["json"]["message"])

    def test_falha_de_envio_nao_marca_como_notificado_e_tenta_de_novo(self):
        self._preparar(preco="4000")
        rodar("monitor", "add", "--alvo", "4500")
        with mock.patch.dict(os.environ, {"PRECOS_DATA_DIR": self.tmp, **self.CANAL}), \
                mock.patch.object(Pipeline, "verificar", return_value=None), \
                mock.patch("precos.pipeline.idade_dominio", return_value=(None, "offline")), \
                mock.patch("precos.notify.requests.post", return_value=Resp(503)):
            _, out, _ = rodar("monitor", "check", "--notificar")
        self.assertIn("FALHOU (HTTP 503)", out)
        (_, out2, _), _, post = self._check("--notificar", "--forcar", env=self.CANAL)
        self.assertEqual(post.call_count, 1)                       # tentou de novo
        self.assertNotIn("(já notificado)", out2)

    def test_notificar_sem_canal_avisa_mas_imprime_o_alerta(self):
        self._preparar(preco="4000")
        rodar("monitor", "add", "--alvo", "4500")
        (code, out, err), _, post = self._check("--notificar")
        self.assertEqual(code, 0)
        self.assertIn("ALERTA monitor #1", out)
        self.assertIn("nenhum canal configurado", err)
        post.assert_not_called()

    def test_acima_do_alvo_nao_notifica(self):
        self._preparar(preco="5000")
        rodar("monitor", "add", "--alvo", "4500")
        (_, out, _), _, post = self._check("--notificar", env=self.CANAL)
        self.assertNotIn("ALERTA", out)
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
