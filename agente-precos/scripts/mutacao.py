"""Teste de mutação manual: injeta, um de cada vez, bugs deliberados nos pontos críticos e confirma que a suíte FALHA.

Uso (a partir de agente-precos/):  python3 scripts/mutacao.py
- "PEGOU"   : a suíte detectou o bug (o que se espera).
- "ESCAPOU" : nenhum teste falhou -> falta teste para aquele comportamento.
- "NÃO APLICÁVEL": o trecho de código mudou; atualize o mutante.
Cada arquivo é restaurado ao final (mesmo se interrompido por erro); não rode com alterações não salvas que você queira preservar.
"""
import pathlib, subprocess, sys
import os
os.chdir(pathlib.Path(__file__).resolve().parent.parent)
MUT = [
 ("robots: curinga deixa de funcionar",        "precos/robots.py",  '".*".join(partes)', '"".join(partes)'),
 ("http: redirect sem validar destino",        "precos/http.py",    '                invalido = _destino_invalido(atual)\n                if invalido:\n                    return Resultado(url, atual, resp.status_code, "", agora_brt(), erro=f"redirecionamento recusado: {invalido}")\n', ''),
 ("http: 403 passa a ser retentado",           "precos/http.py",    'if status in (401, 403, 407, 429, 451):', 'if status in (407, 451):'),
 ("scoring: loja sem verificação vira média",  "precos/scoring.py", 'if Evidencia.DIVERGENTE in ess.values() or loja_sem_verificacao:', 'if Evidencia.DIVERGENTE in ess.values():'),
 ("scoring: critério não verificado vale 50",  "precos/scoring.py", 'return 0.0 if self.nota is None else self.nota * self.peso / 100', 'return self.peso * 0.5 if self.nota is None else self.nota * self.peso / 100'),
 ("pricing: frete esquecido no parcelado",     "precos/pricing.py", 'return money(valor_parcela * n + (frete or 0) + (taxas or 0))', 'return money(valor_parcela * n + (taxas or 0))'),
 ("util: milhar BR vira decimal",              "precos/util.py",    "v = parse_brl_texto(s) if re.fullmatch(r\"\\d{1,3}(\\.\\d{3})+\", s) else parse_decimal_maquina(s)", "v = parse_decimal_maquina(s)"),
 ("matching: ignora a slug da URL",            "precos/matching.py", 'fontes["URL"] = _tokens_url(url)', 'fontes["URL"] = []'),
 ("cli: monitor reverifica tudo (bug antigo)", "precos/cli.py",     "    if o.status in (Status.VERIFICADA, Status.INDISPONIVEL, Status.NAO_CONFIRMADA):\n        return True\n    return o.status == Status.ERRO and any(\"falha técnica\" in a for a in o.alertas)", "    return True"),
 ("notify: loga a exceção inteira (vaza URL)", "precos/notify.py",   'log.warning("falha ao notificar via %s (%s)", canal, type(e).__name__)', 'log.warning("falha ao notificar via %s (%s)", canal, e)'),
 ("extract: variantes viram produtos distintos",  "precos/extract.py", "    produtos = [pr for pr in produtos if id(pr) not in variantes_dos_grupos]\n", ""),
 ("http: limite de bytes não trunca",          "precos/http.py",    "                corpo = corpo[: self.cfg.max_bytes]\n", ""),
 ("scoring: pontua entrega sem frete",         "precos/scoring.py", "    if o.frete_valor is None:\n        return None, Evidencia.NAO_VERIFICADO, \"frete não calculado", "    if False:\n        return None, Evidencia.NAO_VERIFICADO, \"frete não calculado"),
 ("report: confiança alta inalcançável",       "precos/scoring.py", "        elif all(v.confirmado for v in ess.values()) and not desat:\n            confianca = \"alta\"", "        elif False:\n            confianca = \"alta\""),
 ("notify: ntfy sem tópico no corpo",          "precos/notify.py",  'json={"topic": cfg.ntfy_topic, "title": titulo, "message": mensagem}', 'json={"title": titulo, "message": mensagem}'),
 ("extract: Pix escolhe o MENOR (bug da Truedata)", "precos/extract.py", "        descontos = sorted((dist, valor, meio) for dist, valor, _, meio in cands if valor < preco)", "        descontos = sorted((valor, dist, meio) for dist, valor, _, meio in cands if valor < preco)"),
 ("extract: sem âncora ainda atribui Pix",     "precos/extract.py", "        else:\n            _condicoes_de_pagamento(ex, texto, ancora)", "        if True:\n            _condicoes_de_pagamento(ex, texto, ancora or 0)"),
 ("extract: 's/ juros' lido como com juros",   "precos/extract.py", r'r"\b(?:sem|s\s*/)\s*juros?\b|', r'r"\bsem\s*juros?\b|'),
 ("extract: parcela sem juros não precisa fechar", "precos/extract.py", "            if abs(total - base) > max(Decimal(\"1.00\"), base * Decimal(\"0.01\")):\n                continue", "            pass"),
 ("extract: atacado deixa de ser excluído",    "precos/extract.py", '_RE_EXCLUI_ANTES = re.compile(r"revendedor|revenda", re.I)', '_RE_EXCLUI_ANTES = re.compile(r"NUNCA_CASA_XYZ", re.I)'),
 ("pipeline: sem cache negativo (martela o RDAP)", "precos/pipeline.py", "        return agora_brt() - datetime.fromisoformat(iso) < janela", "        return False"),
 ("cli: anotar não refaz identidade",          "precos/cli.py",     '    if any(m in mudou for m in ("preço", "título", "condição")):', '    if False:'),
]
falhou_ao_testar = 0
for nome, arq, velho, novo in MUT:
    p = pathlib.Path(arq); orig = p.read_text()
    if velho not in orig:
        print(f"?? NÃO APLICÁVEL: {nome} (trecho não encontrado)"); falhou_ao_testar += 1; continue
    try:
        p.write_text(orig.replace(velho, novo, 1))
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], capture_output=True, text=True, check=False)
        pegou = r.returncode != 0
    finally:
        p.write_text(orig)
    print(("PEGOU   " if pegou else "ESCAPOU ") + nome)
    falhou_ao_testar += (not pegou)
print("\nmutantes que ESCAPARAM:", falhou_ao_testar)
