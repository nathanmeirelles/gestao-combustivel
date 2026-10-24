"""Interface de linha de comando: `python -m precos <comando>`."""

from __future__ import annotations

import argparse
import copy
import logging
import re
import sys
import time
from datetime import date
from decimal import Decimal

from . import __version__
from .apis import ApiJson
from .config import Config
from .discovery import SemProvedor, buscar, provedores_configurados
from .models import Consulta, Evidencia, Oferta, Parcelamento, Status
from .pipeline import Pipeline
from .report import confianca_geral, melhor_elegivel, nome_loja, relatorio
from .seller import Loja, TIERS, consultar_cep, consultar_cnpj, idade_dominio
from .store import Store
from .util import agora_brt, fmt_brl, fmt_cnpj, fmt_dt, parse_valor_usuario


def valor_cli(texto: str) -> Decimal:
    v = parse_valor_usuario(texto)
    if v is None:
        raise argparse.ArgumentTypeError(f"valor inválido: {texto!r}")
    return v


def _lista(txt: str | None) -> list[str]:
    return [x.strip() for x in (txt or "").split(",") if x.strip()]


_ABERTOS: list[Store] = []     # bancos abertos pelo comando em execução; fechados em main()


def _ctx(args) -> tuple[Config, Store, Pipeline]:
    cfg = Config.from_env()
    store = Store(cfg.db_path)
    _ABERTOS.append(store)
    return cfg, store, Pipeline(cfg, store)


def _cid(args, store: Store) -> int:
    cid = args.consulta if getattr(args, "consulta", None) else store.ultima_consulta_id()
    if cid is None or store.consulta(cid) is None:
        raise SystemExit("Consulta não encontrada. Crie uma com: python -m precos nova \"produto\"")
    return cid


# ------------------------------------------------------------------ comandos
def cmd_doctor(args) -> int:
    cfg = Config.from_env()
    api = ApiJson(cfg)
    linhas: list[tuple[str, str, str]] = []

    def item(nome, ok, detalhe):
        linhas.append((nome, "OK" if ok is True else "NÃO CONFIGURADO" if ok is None else "INDISPONÍVEL", detalhe))

    item("Python", sys.version_info >= (3, 11), sys.version.split()[0])
    for mod in ("requests", "bs4", "lxml"):
        try:
            __import__(mod)
            item(f"módulo {mod}", True, "importado")
        except ImportError:
            item(f"módulo {mod}", False, "instale: pip install requests beautifulsoup4 lxml")
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        Store(cfg.db_path).fechar()
        item("banco SQLite", True, str(cfg.db_path))
    except Exception as e:
        item("banco SQLite", False, f"{type(e).__name__}: {e}")

    for nome, fn in (
        ("CNPJ (BrasilAPI → Receita)", lambda: consultar_cnpj("00.000.000/0001-91", api)[0] is not None),
        ("CEP (ViaCEP)", lambda: consultar_cep("01001-000", api)[0] is not None),
        ("idade de domínio .br (RDAP Registro.br)", lambda: idade_dominio("mundomusical.com.br", api)[0] is not None),
    ):
        t0 = time.time()
        try:
            ok = fn()
        except Exception as e:  # diagnóstico nunca derruba
            ok = False
        item(nome, ok, f"{time.time() - t0:.1f}s")

    provs = provedores_configurados(cfg)
    item("busca automática (Brave/Google CSE)", True if provs else None, ", ".join(provs) if provs else "sem chave: use WebSearch na sessão ou forneça URLs")
    try:
        import playwright  # noqa: F401
        item("Playwright", True, "importado (use --navegador para testar o Chromium)")
    except ImportError:
        item("Playwright", None, "opcional: pip install playwright")
    if args.navegador:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                b = pw.chromium.launch()
                b.close()
            item("Chromium headless", True, "abriu e fechou")
        except Exception as e:
            item("Chromium headless", False, str(e).splitlines()[0][:100])
    item("cálculo de frete", None, "sem API de frete: informe o valor lido no carrinho com `precos anotar`")
    item("agendamento / monitoramento contínuo", None, "nenhum agendador ativo: `monitor check` roda só quando chamado (cron/GitHub Actions/tarefa agendada)")
    item("notificações", None, "nenhum canal configurado; `monitor check` apenas imprime alertas")

    larg = max(len(n) for n, _, _ in linhas)
    print(f"agente-precos {__version__} — diagnóstico em {fmt_dt(agora_brt())}\n")
    for n, s, d in linhas:
        print(f"{n.ljust(larg)}  {s.ljust(16)}  {d}")
    return 0 if all(s != "INDISPONÍVEL" for _, s, _ in linhas) else 1


def cmd_nova(args) -> int:
    cfg, store, pipe = _ctx(args)
    cep = None
    if args.cep:
        info, erro = consultar_cep(args.cep, ApiJson(cfg))
        if erro:
            print(f"Aviso: CEP não validado ({erro}); mantido como informado.", file=sys.stderr)
        cep = "".join(ch for ch in args.cep if ch.isdigit())
    c = Consulta(texto=args.texto, modelo=args.modelo, excluir=_lista(args.excluir), condicao=args.condicao, gtin=args.gtin,
                 pagamento=args.pagamento, parcelas=args.parcelas, cep=cep, prioridade=args.prioridade, aceita_marketplace=not args.sem_marketplace)
    cid = store.nova_consulta(c)
    print(f"Consulta #{cid} criada: {c.texto} (condição: {c.condicao})")
    if not c.modelo:
        print("Dica: use --modelo \"termos que o título precisa conter\" para evitar confundir variantes (ex.: XL, Pro).")
    return 0


def cmd_consultas(args) -> int:
    _, store, _ = _ctx(args)
    for c in store.consultas():
        n = len(store.ultimas_ofertas(c.id))
        print(f"#{c.id}  {fmt_dt(c.criada_em)}  {c.texto}  [{n} anúncio(s)]")
    return 0


def cmd_candidato(args) -> int:
    _, store, pipe = _ctx(args)
    cid = _cid(args, store)
    for url in args.urls:
        o = pipe.registrar_candidato(cid, url, args.preco, args.origem, args.titulo)
        print(f"candidato registrado (NÃO confirmado): {o.dominio}  {fmt_brl(o.preco_exibido) if o.preco_exibido else 'sem preço'}  identidade: {o.identidade_produto or 'n/d'}")
    return 0


def _linha_oferta(o: Oferta) -> str:
    base = f"[{o.status.value.upper()}] {o.dominio}"
    if o.status in (Status.VERIFICADA, Status.INDISPONIVEL):
        extra = f" {fmt_brl(o.preco_exibido)} ({o.evid('preco_exibido').rotulo})"
        if o.preco_pix:
            extra += f" · à vista {fmt_brl(o.preco_pix)} [{o.meio_vista or 'meio não especificado'}] (estimado)"
        if o.parcelamentos:
            p = max(o.parcelamentos, key=lambda x: x.n)
            extra += f" · {p.n}x {fmt_brl(p.valor)}"
        extra += f" · produto: {o.identidade_produto}"
        return base + extra
    return base + f" — {o.alertas[-1] if o.alertas else ''}"


def cmd_verificar(args) -> int:
    _, store, pipe = _ctx(args)
    cid = _cid(args, store)
    for url in args.urls:
        o = pipe.verificar(cid, url, fresco=args.fresco, renderizar=args.renderizar)
        print(_linha_oferta(o) + f"   (oferta #{o.id}, {fmt_dt(o.coletado_em)})")
        for a in o.alertas[:5]:
            print(f"    ! {a}")
        for n in o.notas[:3]:
            print(f"    · {n}")
    return 0


def cmd_loja(args) -> int:
    _, store, _ = _ctx(args)
    dom = args.dominio.lower().removeprefix("www.")
    loja = store.loja(dom) or Loja(dom)
    try:
        if args.nome:
            loja.nome = args.nome
        if args.tier:
            loja.definir_tier(args.tier, args.fonte)
        if args.cnpj:
            from .util import cnpj_valido, cnpj_digitos
            if not cnpj_valido(args.cnpj):
                raise ValueError("CNPJ inválido")
            loja.cnpj, loja.cnpj_fonte = cnpj_digitos(args.cnpj), args.fonte
        if args.nota is not None:
            loja.definir_reputacao(args.nota, args.escala, args.nota_fonte or args.fonte or "", args.nota_data)
        if args.politicas is not None:
            if not args.politicas_fonte:
                raise ValueError("--politicas exige --politicas-fonte (URL da política de troca/garantia)")
            loja.politicas_ok, loja.politicas_fonte = args.politicas == "sim", args.politicas_fonte
    except ValueError as e:
        print(f"Recusado: {e}", file=sys.stderr)
        return 2
    store.salvar_loja(loja)
    print(f"{dom}: nome={loja.nome} tier={loja.tier} cnpj={fmt_cnpj(loja.cnpj) if loja.cnpj else '—'} "
          f"reputação={loja.reputacao_nota}/{loja.reputacao_escala} políticas={loja.politicas_ok}")
    return 0


def cmd_anotar(args) -> int:
    _, store, _ = _ctx(args)
    base = store.oferta(args.oferta)
    if base is None:
        print("Oferta não encontrada.", file=sys.stderr)
        return 2
    o = copy.deepcopy(base)
    o.id = None
    o.coletado_em = agora_brt()
    quando = f"informado manualmente em {fmt_dt(o.coletado_em)}; fonte: {args.fonte}"
    mudou = []
    if args.frete is not None:
        o.frete_valor = args.frete
        o.frete_nota = args.frete_nota
        o.marcar("frete_valor", Evidencia.CONFIRMADO_DIRETAMENTE)
        mudou.append("frete")
    if args.pix is not None:
        o.preco_pix, o.meio_vista = args.pix, "pix"
        o.marcar("preco_pix", Evidencia.CONFIRMADO_DIRETAMENTE)
        mudou.append("pix")
    for txt in args.parcela or []:
        m = re.fullmatch(r"(\d{1,2})x(?:de)?([\d.,]+)(:sj|:cj)?", txt.replace(" ", ""), re.I)
        if not m:
            print(f"Parcelamento inválido: {txt!r} (use 12x358,25:sj ou 6x760:cj)", file=sys.stderr)
            return 2
        n, v = int(m.group(1)), valor_cli(m.group(2))
        sj = None if not m.group(3) else m.group(3).lower() == ":sj"
        o.parcelamentos = [p for p in o.parcelamentos if p.n != n] + [Parcelamento(n, v, sj)]
        o.marcar("parcelamentos", Evidencia.CONFIRMADO_DIRETAMENTE)
        mudou.append(f"{n}x")
    if args.garantia:
        o.garantia = args.garantia
        o.marcar("garantia", Evidencia.CONFIRMADO_DIRETAMENTE)
        mudou.append("garantia")
    if args.vendedor:
        o.vendedor = args.vendedor
        o.marcar("vendedor", Evidencia.CONFIRMADO_DIRETAMENTE)
        mudou.append("vendedor")
    if not mudou:
        print("Nada para anotar.", file=sys.stderr)
        return 2
    o.notas.append(f"anotação ({', '.join(mudou)}): {quando}")
    store.salvar_oferta(o)
    print(f"Nova observação #{o.id} da oferta #{args.oferta}: {', '.join(mudou)} registrados como confirmados com fonte '{args.fonte}'.")
    return 0


def cmd_cnpj(args) -> int:
    cfg = Config.from_env()
    i, erro = consultar_cnpj(args.cnpj, ApiJson(cfg))
    if erro:
        print(erro, file=sys.stderr)
        return 1
    print(f"{fmt_cnpj(i.cnpj)} — {i.razao_social} ({i.nome_fantasia or 'sem fantasia'})\n  situação: {i.situacao} · aberta em {i.abertura} ({i.anos_atividade:.1f} anos)"
          f"\n  {i.cnae} · {i.municipio}/{i.uf}\n  fonte: {i.fonte} (consultado em {fmt_dt(agora_brt())})")
    return 0


def cmd_cep(args) -> int:
    info, erro = consultar_cep(args.cep, ApiJson(Config.from_env()))
    if erro:
        print(erro, file=sys.stderr)
        return 1
    print(f"{info.get('logradouro')}, {info.get('bairro')} — {info.get('localidade')}/{info.get('uf')}")
    return 0


def cmd_comparar(args) -> int:
    _, store, pipe = _ctx(args)
    cid = _cid(args, store)
    comp = pipe.comparar(cid)
    for modo, rank in comp.ranking.items():
        print(f"\n== Ranking ({modo}) — consulta #{cid}: {comp.consulta.texto}")
        if not rank:
            print("  (nenhuma oferta verificada)")
        for i, p in enumerate(rank, 1):
            marca = f"{i:>2}." if p.elegivel else " ✗ "
            tot = fmt_brl(p.custo.total) + ("" if p.custo.frete_incluido else " s/frete") if p.custo else "—"
            print(f" {marca} {p.pontos:5.1f}  {nome_loja(p.oferta, comp.lojas):<46} total {tot:<22} confiança da oferta: {p.confianca:<5} | loja: {p.confiab.rotulo}")
            if not p.elegivel:
                print(f"        excluída: {'; '.join(p.motivos_exclusao)}")
    principal = melhor_elegivel(comp.ranking.get("vista") or next(iter(comp.ranking.values()), []))
    nivel, razoes = confianca_geral(comp, principal)
    print(f"\nConfiança da PESQUISA: {nivel.upper()} — " + "; ".join(razoes))
    if comp.nao_verificadas:
        print(f"{len(comp.nao_verificadas)} anúncio(s) fora do veredito (bloqueados/sem preço/sem estoque/candidatos) — veja `relatorio`.")
    return 0


def cmd_relatorio(args) -> int:
    _, store, pipe = _ctx(args)
    cid = _cid(args, store)
    texto = relatorio(pipe.comparar(cid))
    if args.saida:
        with open(args.saida, "w", encoding="utf-8") as f:
            f.write(texto + "\n")
        print(f"Relatório salvo em {args.saida}")
    else:
        print(texto)
    return 0


def cmd_descobrir(args) -> int:
    cfg, store, pipe = _ctx(args)
    try:
        achados, erros = buscar(cfg, ApiJson(cfg), args.consulta_texto, _lista(args.dominios), args.limite)
    except SemProvedor as e:
        print(str(e), file=sys.stderr)
        return 3
    for c in achados:
        print(f"- {c.titulo}\n  {c.url}\n  {c.trecho[:140]}")
    for e in erros:
        print(f"erro: {e}", file=sys.stderr)
    if args.registrar and achados:
        cid = _cid(args, store)
        for c in achados:
            pipe.registrar_candidato(cid, c.url, None, f"resultado de busca ({c.provedor})", c.titulo)
        print(f"{len(achados)} candidato(s) registrados como NÃO confirmados na consulta #{cid}.")
    return 0 if achados or not erros else 1


def cmd_monitor(args) -> int:
    cfg, store, pipe = _ctx(args)
    if args.acao == "add":
        cid = _cid(args, store)
        mid = store.novo_monitor(cid, {"preco_alvo": str(args.alvo), "pagamento": args.pagamento, "lojas": _lista(args.lojas), "intervalo_h": args.intervalo_h,
                                       "ate": args.ate, "confiabilidade_min": args.conf_min})
        print(f"Monitor #{mid} criado para a consulta #{cid}: alvo {fmt_brl(args.alvo)} ({args.pagamento}). "
              "ATENÇÃO: nada roda sozinho — agende `python -m precos monitor check` (cron, GitHub Actions ou tarefa agendada) e configure como receber o alerta.")
        return 0
    if args.acao == "list":
        ms = store.monitores(so_ativos=False)
        for m in ms:
            print(f"#{m['id']} consulta {m['consulta_id']} alvo {fmt_brl(Decimal(m['preco_alvo']))} ({m['pagamento']}) {'ATIVO' if m['ativo'] else 'inativo'} "
                  f"último: {m.get('ultimo_preco') or '—'} em {m.get('ultimo_check') or '—'}")
        if not ms:
            print("Nenhum monitor.")
        return 0
    if args.acao == "remove":
        print("Monitor desativado." if store.desativar_monitor(args.id) else "Monitor não encontrado.")
        return 0
    # check
    alertas = 0
    for m in store.monitores():
        if args.id and m["id"] != args.id:
            continue
        if m.get("ate") and date.fromisoformat(m["ate"]) < agora_brt().date():
            store.desativar_monitor(m["id"])
            print(f"Monitor #{m['id']} expirou em {m['ate']} e foi desativado.")
            continue
        if not args.forcar and m.get("ultimo_check"):
            from datetime import datetime
            horas = (agora_brt() - datetime.fromisoformat(m["ultimo_check"])).total_seconds() / 3600
            if horas < m["intervalo_h"]:
                print(f"Monitor #{m['id']}: última checagem há {horas:.1f}h (intervalo {m['intervalo_h']}h); pulado. Use --forcar.")
                continue
        urls = [o.url for o in store.ultimas_ofertas(m["consulta_id"]) if o.status != Status.NAO_CONFIRMADA or True]
        if m.get("lojas"):
            urls = [u for u in urls if any(d in u for d in m["lojas"])]
        for u in urls:
            pipe.verificar(m["consulta_id"], u, fresco=True)
        comp = pipe.comparar(m["consulta_id"])
        modo = "parcelado" if m["pagamento"] == "parcelado" else "vista"
        rank = [p for p in comp.ranking.get(modo, comp.ranking.get("vista", [])) if p.elegivel and p.custo]
        ordem = {"baixa": 0, "média": 1, "alta": 2}
        rank = [p for p in rank if ordem[p.confianca] >= ordem.get(m.get("confiabilidade_min") or "baixa", 0)]
        melhor = min(rank, key=lambda p: p.custo.total, default=None)
        agora = agora_brt().isoformat()
        if melhor is None:
            store.atualizar_monitor(m["id"], ultimo_check=agora)
            print(f"Monitor #{m['id']}: nenhuma oferta elegível na checagem.")
            continue
        preco = melhor.custo.produto
        store.atualizar_monitor(m["id"], ultimo_check=agora, ultimo_preco=str(preco))
        if preco <= Decimal(m["preco_alvo"]):
            alertas += 1
            print(f"ALERTA monitor #{m['id']}: {nome_loja(melhor.oferta)} a {fmt_brl(preco)} atingiu o alvo de {fmt_brl(Decimal(m['preco_alvo']))} — {melhor.oferta.url}")
        else:
            print(f"Monitor #{m['id']}: menor preço elegível {fmt_brl(preco)} (alvo {fmt_brl(Decimal(m['preco_alvo']))}).")
    return 0


# ------------------------------------------------------------------ parser
def montar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="precos", description="Agente de inteligência de preços: verifica, compara e recomenda com evidências.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("doctor", help="diagnóstico real das capacidades do ambiente")
    s.add_argument("--navegador", action="store_true", help="também testa abrir o Chromium")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("nova", help="cria uma consulta (o pedido de compra)")
    s.add_argument("texto")
    s.add_argument("--modelo", help="termos que o título do anúncio PRECISA conter (ex.: 'line 6 hx stomp')")
    s.add_argument("--excluir", help="termos a recusar, separados por vírgula (ex.: 'xl,roxo')")
    s.add_argument("--condicao", default="novo", choices=["novo", "usado", "recondicionado"])
    s.add_argument("--gtin")
    s.add_argument("--pagamento", choices=["vista", "parcelado"])
    s.add_argument("--parcelas", type=int)
    s.add_argument("--cep")
    s.add_argument("--prioridade", choices=["preco", "parcelamento", "oficial", "entrega", "garantia", "seguranca"])
    s.add_argument("--sem-marketplace", action="store_true")
    s.set_defaults(fn=cmd_nova)

    s = sub.add_parser("consultas", help="lista consultas recentes")
    s.set_defaults(fn=cmd_consultas)

    s = sub.add_parser("candidato", help="registra URL vista em busca (NÃO confirmada)")
    s.add_argument("urls", nargs="+")
    s.add_argument("-c", "--consulta", type=int)
    s.add_argument("--preco", type=valor_cli, help="preço visto no resultado de busca (ficará marcado como não confirmado)")
    s.add_argument("--origem", default="resultado de busca (snippet)")
    s.add_argument("--titulo")
    s.set_defaults(fn=cmd_candidato)

    s = sub.add_parser("verificar", help="abre a página da oferta, lê e registra preço/condições (respeita robots.txt)")
    s.add_argument("urls", nargs="+")
    s.add_argument("-c", "--consulta", type=int)
    s.add_argument("--fresco", action="store_true", help="ignora o cache")
    s.add_argument("--renderizar", action="store_true", help="usa Chromium (JavaScript) — mesmas regras de robots/ritmo")
    s.set_defaults(fn=cmd_verificar)

    s = sub.add_parser("loja", help="registra fatos verificados sobre uma loja (classificações exigem URL de fonte)")
    s.add_argument("dominio")
    s.add_argument("--nome")
    s.add_argument("--tier", choices=sorted(TIERS))
    s.add_argument("--fonte", help="URL que comprova o tier/CNPJ")
    s.add_argument("--cnpj")
    s.add_argument("--nota", type=float, help="nota de reputação independente")
    s.add_argument("--escala", type=float, default=10.0)
    s.add_argument("--nota-fonte")
    s.add_argument("--nota-data", help="AAAA-MM-DD da nota")
    s.add_argument("--politicas", choices=["sim", "nao"])
    s.add_argument("--politicas-fonte")
    s.set_defaults(fn=cmd_loja)

    s = sub.add_parser("anotar", help="registra dado lido manualmente (carrinho/checkout) com fonte obrigatória")
    s.add_argument("oferta", type=int)
    s.add_argument("--fonte", required=True, help="de onde veio (ex.: 'carrinho em 24/10 14:02, CEP informado')")
    s.add_argument("--frete", type=valor_cli)
    s.add_argument("--frete-nota", help="prazo/modalidade")
    s.add_argument("--pix", type=valor_cli)
    s.add_argument("--parcela", action="append", help="ex.: 12x358,25:sj (sem juros) ou 6x760:cj")
    s.add_argument("--garantia")
    s.add_argument("--vendedor")
    s.set_defaults(fn=cmd_anotar)

    s = sub.add_parser("cnpj", help="consulta CNPJ na Receita (via BrasilAPI)")
    s.add_argument("cnpj")
    s.set_defaults(fn=cmd_cnpj)
    s = sub.add_parser("cep", help="valida CEP (ViaCEP)")
    s.add_argument("cep")
    s.set_defaults(fn=cmd_cep)

    s = sub.add_parser("comparar", help="pontua e ranqueia as ofertas da consulta")
    s.add_argument("-c", "--consulta", type=int)
    s.set_defaults(fn=cmd_comparar)
    s = sub.add_parser("relatorio", help="relatório completo A–F em Markdown")
    s.add_argument("-c", "--consulta", type=int)
    s.add_argument("--saida")
    s.set_defaults(fn=cmd_relatorio)

    s = sub.add_parser("descobrir", help="busca candidatos via API oficial (exige chave Brave/Google CSE)")
    s.add_argument("consulta_texto")
    s.add_argument("--dominios", help="restringe a domínios, separados por vírgula")
    s.add_argument("--limite", type=int, default=10)
    s.add_argument("--registrar", action="store_true", help="registra os achados como candidatos não confirmados")
    s.add_argument("-c", "--consulta", type=int)
    s.set_defaults(fn=cmd_descobrir)

    s = sub.add_parser("monitor", help="acompanha preço-alvo (NÃO roda sozinho: precisa de agendador)")
    s.add_argument("acao", choices=["add", "list", "check", "remove"])
    s.add_argument("--id", type=int)
    s.add_argument("-c", "--consulta", type=int)
    s.add_argument("--alvo", type=valor_cli)
    s.add_argument("--pagamento", default="vista", choices=["vista", "parcelado"])
    s.add_argument("--lojas", help="domínios preferidos, separados por vírgula")
    s.add_argument("--intervalo-h", type=float, default=24.0)
    s.add_argument("--ate", help="AAAA-MM-DD: data limite")
    s.add_argument("--conf-min", choices=["baixa", "média", "alta"], default="baixa")
    s.add_argument("--forcar", action="store_true")
    s.set_defaults(fn=cmd_monitor)
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = montar_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR, format="%(levelname)s %(name)s: %(message)s")
    if args.cmd == "monitor" and args.acao == "add" and args.alvo is None:
        print("monitor add exige --alvo", file=sys.stderr)
        return 2
    if args.cmd == "monitor" and args.acao == "remove" and not args.id:
        print("monitor remove exige --id", file=sys.stderr)
        return 2
    try:
        return args.fn(args)
    finally:
        while _ABERTOS:
            _ABERTOS.pop().fechar()
