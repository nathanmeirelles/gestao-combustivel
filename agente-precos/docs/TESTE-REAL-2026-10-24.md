# Testes reais — Line 6 HX Stomp (24/10/2026) e bateria de extração em 19 páginas (29/10/2026)

Pedido simulado: *"Line 6 HX Stomp, novo, parcelado em até 12x"*, sem CEP (não foi informado e não foi inventado).
Relatório gerado (regenerado em 29/10/2026 com o código final; as leituras de Magalu, Casas Bahia e Mercado Livre são de 24/10): [`exemplo-relatorio-hx-stomp-2026-10-29.md`](exemplo-relatorio-hx-stomp-2026-10-29.md).

## Resultado da pesquisa
**Uma única oferta pôde ser verificada e foi elegível**: Harmonia Musical LTDA — R$ 9.190,00 (preço de lista, confirmado no JSON-LD e no texto),
10x de R$ 919,00 sem juros e R$ 8.730,50 "à vista" (meio de pagamento não especificado na página; *estimado*). Frete não calculado.
Confiança da pesquisa: **baixa** (loja única, sem base de comparação; produto e pagamento "estimados"). O relatório **não** a chama de "melhor compra".

Verificação independente da loja (fontes linkadas no relatório): CNPJ ativo, aberto em 19/08/1992 (Receita via BrasilAPI), domínio `.br` registrado em 2000 (RDAP).
Canal de venda, reputação independente e políticas: **não verificados** (Reclame Aqui bloqueia automação).

| Loja | Resultado | Motivo |
|---|---|---|
| harmoniamusical.com.br | **Verificada** | preço no JSON-LD corroborado no texto |
| pedalboardsupply.com.br | Sem estoque + produto **divergente** | JSON-LD diz "Lacrada"; a URL diz "semi-nova" → condição contraditória |
| magazineluiza.com.br (2 anúncios), casasbahia.com.br | **Bloqueada** | HTTP 403 ao acesso automatizado — não contornado |
| mercadolivre.com.br (2 anúncios) | **Bloqueada** | redirecionamento para verificação/captcha — não contornado |
| amazon.com.br | Não verificada | página carrega, mas sem preço em dados estruturados; sem parser específico (política do projeto) |
| americanas.com.br | Não verificada | redireciona para página genérica, sem produto |
| bestpricesbrasil.com.br | Não verificada | falha de rede do ambiente (proxy 502 no robots.txt) — **não** foi recusa do site |

Referências de preço **NÃO confirmadas** relatadas por resumos de busca no mesmo dia (só contexto; não entram em nenhum cálculo):
anúncios do Mercado Livre entre ~R$ 4.299 e R$ 5.300 (sem link atribuído), Best Prices ~R$ 5.999,90, Amazon ~R$ 9.926,68 —
uma **dispersão de mais de 2×**, que reforça a necessidade de conferir modelo (padrão × XL) e condição. A busca também trouxe vários anúncios do **XL**, que é outro produto.

## O que o teste real revelou (e foi corrigido, com teste de regressão)
1. **Condição contraditória passava como "confere"**: o check olhava só o nome do JSON-LD; agora cruza nome, `<title>`, `og:title`, `h1` e slug da URL.
2. `Location` em minúsculas não era seguido (`dict(resp.headers)` perde a insensibilidade a maiúsculas) → redirecionamentos do Mercado Livre.
3. `robots.txt` com redirecionamento (301) era tratado como "indisponível" e **negava** o acesso por engano.
4. Corpo lido em streaming e depois acessado por `r.text` → robots lido como vazio (**liberaria tudo**). Descoberto por teste simulado.
5. Regex de parcelamento *consumia* o texto seguinte e perdia a segunda opção de parcelas.
6. "R$ 8.730,50 à vista" sem a palavra Pix não era reconhecido; e o relatório rotulava como "Pix" o que a página não diz.
7. `--preco 4.299` virava R$ 4,299 (milhar brasileiro) → parser único `parse_valor_usuario`.
8. Falha de rede do ambiente era rotulada como "bloqueio anti-robô"; relatório recomendava como "melhor compra" uma loja única e sem verificação (confiança "média").
9. RDAP do Registro.br devolve 403 alternado em rajadas (limitador): a API passou a ter intervalo por host e espera+repete **uma vez** — sem trocar de identidade.

## Também registrado com honestidade
- Antes de ler o `robots.txt`, fiz **uma** requisição de diagnóstico em `zoom.com.br/search?q=…` e outra em `buscape.com.br/search?q=…` (caminhos que o robots proíbe); o conteúdo foi apagado e nunca mais usado. O projeto **não** coleta páginas de busca desses sites.
- Registros de `bestpricesbrasil` (atualizado) e `produto.mercadolivre.com.br` (item `MLB-1580457369`, "HTTP 302") foram gravados em partes **antes** de duas correções de rotulagem; o do Mercado Livre não foi refeito para não insistir em site que já redirecionou o robô para verificação.
- Brave/Google CSE, frete, agendamento e notificações **não foram testados ao vivo** (sem chave / não implementados).


---

# Rodada de 29/10/2026 — bateria de extração em 19 páginas reais de 18 lojas

Objetivo: **testar o extrator**, não achar preço (os valores abaixo não são uma pesquisa). Produtos variados (pedais, fones, cordas), plataformas variadas.
A bateria rodou em 4 rodadas (antes e depois de cada correção; uma requisição por página em cada rodada), com User-Agent próprio, 2 s entre requisições ao mesmo host e `robots.txt` obedecido; as 3 que responderam 403 **não foram repetidas**.

| Resultado | Quantidade | Detalhe |
|---|---|---|
| Verificadas | 10 | Roland Store, The Gear, Cheiro de Música, Truedata, Music Center, Look Music, Izzo, Ponto do Músico, Megasom, KaBuM |
| Sem estoque (preço lido) | 2 | Ninja Som, Teclacenter (usado) |
| Sem preço legível | 4 | Serenata e Joinville Games (não publicam preço estruturado — **nem renderizadas com Chromium**, testado), Bruno Rogério (falha de TLS do servidor deles), 1 página da Roland (HTTP 500) |
| Recusadas (HTTP 403) | 3 | Tango Music, Made in Brazil, Harman — respeitado, sem nova tentativa |
| Quebras do programa | **0** | |

## O que a bateria revelou (todos corrigidos, com teste de regressão)
1. **Pix falso pego de produto relacionado** (Truedata): lia a página inteira e escolhia o *menor* valor — R$ 152,91, de um scanner — para um fone de R$ 289,90. Agora a leitura é ancorada no preço principal (±400 caracteres), escolhe o candidato **mais próximo**, não o menor.
2. **Preço de atacado** (R$ 231,92, mínimo 5 unidades) poderia virar Pix. Agora valores sujeitos a “revendedores/mínimo/unidades” são excluídos — mas **sem** excluir pela palavra solta “atacado” (aba “VAREJO | ATACADO”) nem por “kit”/“combo”, que podem ser o próprio item. O primeiro conserto excluiu o Pix correto; os testes pegaram.
3. **Preço estruturado que já era o do Pix** (Look Music, KaBuM) era rotulado “cartão à vista”. Agora é detectado; o preço de lista vem do texto (“R$ 325,00 até 6x…”) ou é **derivado das parcelas sem juros** (n × parcela), sempre marcado como estimado.
4. **“s/ juros” lido como “com juros”** (KaBuM): penalizaria o parcelamento no ranking.
5. **Valores escritos “R$ 220 , 41”** (uma plataforma usada por 3 lojas) deixavam a loja inteira sem Pix e sem parcelas.
6. **Parcelas de outro produto** (“10x R$ 31,99 sem juros” de um item relacionado): parcelas sem juros agora precisam **fechar com o preço de lista (±1%)**.
7. **`ProductGroup` com variantes** (cor/tamanho, comum em e-commerce) virava “listagem” e perdia o preço — achado por teste de borda.
8. **Redirecionamento para rede interna** (`169.254.169.254`, `localhost`, `ftp://`) era seguido: a checagem de host interno só valia para a URL inicial. Agora vale em **cada salto** (página, `robots.txt` e sub-recursos do Chromium).
9. **`monitor check` reverificava sites que já bloqueavam** (`or True` esquecido) — apontado pelo `ruff`.
10. **O `anotar` não conseguia registrar o preço base**, então o fluxo “você lê no navegador e eu registro” para lojas bloqueadas não funcionava. Agora `anotar --preco` promove a oferta e **refaz a checagem do modelo**.
11. Falhas de rede sem causa (“robots.txt indisponível”): agora dizem se foi certificado/TLS, tempo esgotado, proxy do ambiente ou conexão.
12. **Sem cache negativo**: com o RDAP do Registro.br limitando taxa (403 persistente observado em 29/10), cada `comparar` repetiria a consulta por loja (~12 s cada) e reforçaria o bloqueio. Agora uma consulta que falhou só é repetida depois de 6 h.
13. Cadastro de loja editado à mão (tier inválido, escala de nota ausente) derrubaria a pontuação; limite de 3 MB passou a truncar de verdade; `lastrowid` do banco é checado.

## Qualidade medida (29/10/2026)
- **328 testes**, cobertura **97%** (linhas e ramos) — a CLI, que estava em 64%, foi a 91%.
- `mypy` (21 arquivos) e `ruff` sem achados.
- **Teste de mutação**: 22 bugs injetados de propósito nos pontos mais críticos (robots, SSRF, 403, confiabilidade, frete, milhar BR, slug da URL, Pix, parcelas, avisos…) — **todos detectados** (`python3 scripts/mutacao.py`).
- O caminho que leva à **confiança “alta”** só era testado nos casos negativos; agora há teste positivo (3 lojas com tudo confirmado).

## Continua sem teste ao vivo (por falta de chave/credencial, ou por publicar algo)
Brave e Google CSE; envio real de ntfy/Telegram/webhook; frete; agendamento em máquina real.
