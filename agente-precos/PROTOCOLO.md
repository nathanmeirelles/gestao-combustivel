# Protocolo do agente de preços (para sessões do Claude Code)

Este arquivo transforma o briefing "Agente autônomo de inteligência de preços" em procedimento operacional
usando as ferramentas **reais** deste projeto. Quem conduz a pesquisa é o Claude Code; o pacote `precos`
faz o trabalho determinístico (ler páginas, calcular, pontuar, registrar, gerar relatório).

> Objetivo: comprar o produto **correto**, pelo menor **custo real**, em loja **confiável**, na melhor condição de pagamento.
> Prefira uma resposta honesta e incompleta a uma completa e inventada.

## Regras que não se negociam
1. Nunca inventar preço, link, cupom, avaliação, estoque, prazo ou condição de pagamento. Só registre o que apareceu em uma saída de ferramenta, com a URL.
2. Preço visto em resultado de busca é **não confirmado** até a página da oferta ser aberta e lida (`verificar`).
3. **Nunca atribuir um preço a uma URL** se a ferramenta de busca não disse explicitamente qual preço é de qual link. Resumos de busca misturam lojas.
4. **Não contornar bloqueios.** Se a página responder 403/captcha/"verificação" ou o `robots.txt` proibir, a oferta fica como *bloqueada*. Não troque de User-Agent, não use WebFetch/Playwright/outro cliente *para furar* a recusa, não use proxies. Peça ao usuário para conferir no navegador e registre com `anotar --fonte`.
5. Não escrever parser específico de HTML para sites cujos termos vedem coleta automatizada (ex.: Amazon). O agente lê só dados estruturados públicos (JSON-LD/meta) e texto visível.
6. Nunca comprar, pagar, cadastrar, assinar ou alterar conta. Nunca digitar dados de pagamento.
7. Nunca chamar uma loja de segura/oficial sem URL que comprove (`loja --tier ... --fonte URL` recusa sem fonte).
8. Não somar cupom/cashback a nenhum total sem verificação. Cashback é sempre apresentado à parte.
9. Datas/horas sempre em Brasília. Dado antigo é sinalizado, nunca apresentado como atual.
10. Não afirmar que executou algo (pesquisa, teste, validação) sem ter executado.

## Passo a passo de uma pesquisa
**0. Diagnóstico (uma vez por sessão):** `python -m precos doctor`. Informe ao usuário o que está indisponível (busca por API, frete, agendamento).

**1. Identificar o produto.** Pergunte **somente** o que muda a recomendação e ainda não foi dito: novo/usado, à vista ou parcelado (quantas vezes), CEP, aceita marketplace, modelo obrigatório. Depois:
```
python -m precos nova "Line 6 HX Stomp" --modelo "line 6 hx stomp" --condicao novo --parcelas 12 [--cep ...] [--excluir "xl"] [--sem-marketplace] [--prioridade preco|parcelamento|oficial|entrega|garantia|seguranca]
```
`--modelo` define os termos que o título **precisa** conter; variantes (XL, Pro, Plus...) e itens usados/semi-novos são marcados como *divergentes* e saem da recomendação.

**2. Descobrir candidatos** (sem tratar nada como validado):
- Com chave configurada: `python -m precos descobrir "..." --dominios a.com.br,b.com.br --registrar`.
- Sem chave (caso padrão): use a ferramenta **WebSearch** com `allowed_domains`, em lotes (varejistas grandes; lojas especializadas; marketplaces). Varie nome exato, marca+modelo, código do fabricante/EAN, grafias. Registre cada URL:
  `python -m precos candidato URL --preco 4299 --titulo "..." --origem "WebSearch 24/10/2026"` (o preço fica **não confirmado**; só informe `--preco` se o resultado atribuiu aquele valor àquele link).
- Descarte listagens/buscas (páginas `/busca/`, `?q=`): são proibidas pelo robots de vários sites e não são a ficha do produto.
- Resumos de busca misturam preços de lojas diferentes: **só atribua preço a um link se a saída disser explicitamente qual preço é daquele link**.

**3. Verificar na fonte.** `python -m precos verificar URL1 URL2 ...` (respeita robots.txt, 2 s entre requisições ao mesmo host, cache de 30 min; `--fresco` ignora o cache; `--renderizar` usa Chromium só para páginas que exigem JavaScript, com as mesmas regras).
Leia a saída: `VERIFICADA`, `INDISPONIVEL`, `BLOQUEADA`, `ERRO`. Para cada alerta, decida: reverificar, pedir conferência manual ou descartar.

**4. Completar com dados que o robô não alcança** (somente se você os viu de fato):
- **Loja que bloqueou o robô ou não publica preço estruturado** (status `BLOQUEADA`/`ERRO`): peça ao usuário para abrir o link e informar preço, disponibilidade e nome do anúncio; então `python -m precos anotar OFERTA_ID --fonte "navegador dd/mm hh:mm" --preco 4.299 --titulo "nome do anúncio" --disponibilidade em_estoque`. Isso promove a oferta a verificada-manual e **refaz a checagem do modelo** (um “XL” ou “usado” digitado continua sendo excluído). Nunca invente esses valores.
- Frete/CEP, Pix/parcelas do carrinho: `python -m precos anotar OFERTA_ID --fonte "carrinho em dd/mm hh:mm, CEP ..." --frete 49,90 --parcela 12x358,25:sj`.
- Loja: CNPJ e idade do domínio são consultados automaticamente (Receita via BrasilAPI; RDAP do Registro.br) quando o CNPJ aparece na página. Reputação independente e classificação (oficial/autorizado/varejista) só entram com URL de fonte: `python -m precos loja DOMINIO --tier autorizado --fonte URL` / `--nota 8.2 --escala 10 --nota-fonte URL`. Reclame Aqui bloqueia automação: use WebSearch para ver o perfil público e registre **o que apareceu**, com a URL e a data.
- Marketplaces: o CNPJ/domínio **não** descreve o vendedor. Identifique o vendedor real do anúncio ou deixe "não verificado".

**5. Comparar e recomendar.** `python -m precos comparar` (resumo) e `python -m precos relatorio --saida arquivo.md` (formato A–F). Quando não houver preferência de pagamento, entregue a melhor opção à vista **e** a melhor parcelada.

**6. Entregar.** Traga para o usuário: veredito, tabela (só dados reais), links diretos com data/hora, comparação de pagamento (valor total e economia em R$), análise de confiabilidade com fontes, conclusão curta. Declare a confiança da pesquisa e **por quê**. Se só uma loja foi verificada, diga que **não há base de comparação** — não chame de "melhor compra".

**7. Monitoramento** (só se o usuário pedir): `python -m precos monitor add --alvo 3.500 --pagamento vista --intervalo-h 24 --ate 2026-12-31`. Avise que **nada roda sozinho**: o agendamento (cron/systemd) e o canal de aviso (`NTFY_TOPIC`, `TELEGRAM_*` ou `ALERT_WEBHOOK_URL`) são do usuário — ver `docs/AGENDAMENTO.md`. Só use `monitor check --notificar` se o usuário pediu avisos; nunca envie notificação por iniciativa própria.

## Quando a confiança pode ser "alta"
Somente se, na oferta vencedora, **preço, produto, vendedor e condição de pagamento** estiverem confirmados (não "estimado"), a loja tiver ≥ 40% de verificação independente, os dados estiverem frescos (< 6 h) e houver **≥ 3 lojas** verificadas e elegíveis. Pix/parcelas lidos do texto da página são sempre "estimado" até serem confirmados no checkout (`anotar`).

## O que pedir ao usuário quando o robô não consegue
- "Abra este link e me diga o preço no Pix, o parcelamento e o frete para o seu CEP" → depois `anotar`.
- "Confirme se o vendedor deste anúncio é X e se o produto é novo/lacrado."
