# Teste real — Line 6 HX Stomp (24/10/2026, ~12:25–12:55 Brasília)

Pedido simulado: *"Line 6 HX Stomp, novo, parcelado em até 12x"*, sem CEP (não foi informado e não foi inventado).
Relatório gerado: [`exemplo-relatorio-hx-stomp-2026-10-24.md`](exemplo-relatorio-hx-stomp-2026-10-24.md).

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
