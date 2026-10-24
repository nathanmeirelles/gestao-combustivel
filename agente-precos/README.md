# agente-precos — inteligência de preços com evidências

Ferramenta + protocolo para pesquisar o **melhor negócio** de um produto no Brasil sem cair em oferta falsa, preço velho
ou parcelamento ruim. Nasceu de um briefing que pede: verificar na fonte, calcular o custo real, avaliar a loja e **dizer
o que não foi verificado**. Ela **não compra nada**.

- `precos/` — pacote Python (CLI `python -m precos`): coleta educada, extração, cálculo, pontuação, relatório, banco SQLite.
- `PROTOCOLO.md` — como o Claude Code conduz uma pesquisa (descoberta, verificação, regras). Slash command: `/pesquisar-preco <produto>`.
- `docs/` — relatório real de teste e resultados da validação.

> Pasta independente do app de combustível que está na raiz do repositório.

## Estado real (o que foi e o que não foi validado)

Validado em 24/10/2026 (ambiente de nuvem do Claude Code, Python 3.13): **181 testes automatizados passando** e uma pesquisa real (Line 6 HX Stomp) — ver [`docs/TESTE-REAL-2026-10-24.md`](docs/TESTE-REAL-2026-10-24.md).

| Capacidade | Situação |
|---|---|
| Leitura de página de produto (JSON-LD/meta/texto), robots.txt com curingas, ritmo, cache, detecção de bloqueio | **Testado contra sites reais** |
| CNPJ (BrasilAPI→Receita), CEP (ViaCEP), idade de domínio `.br` (RDAP Registro.br) | **Testado ao vivo** (RDAP limita taxa; o código espera e repete) |
| Renderização com Chromium (preço via JavaScript) | **Testado** numa página local com JS e em `example.com` (venv com Playwright 1.64.1) — opcional |
| Cálculo de totais, juros implícitos, pontuação, relatório A–F | **Testado** (unidade + ponta a ponta com páginas simuladas) |
| Descoberta via Brave/Google CSE | **Só com respostas simuladas** — nenhuma chave disponível para testar ao vivo |
| Monitoramento (`monitor check`) | **Testado com leitura simulada**; **não há agendador nem notificação** |
| Frete / prazo | **Não automatizado** (sem API); entra via `anotar` quando você lê no carrinho |
| Histórico de preços de terceiros | **Indisponível** (Wayback e API de comparadores são bloqueados aqui); o histórico é o que o próprio agente coletou |
| Mercado Livre, Magalu, Casas Bahia, Americanas, Amazon, Reclame Aqui | **Recusam automação** (403/captcha/sem preço estruturado). O agente **não contorna**; a conferência é manual |

## Instalação

```bash
cd agente-precos
pip install -r requirements.txt            # requests, beautifulsoup4, lxml
python -m precos doctor                    # diagnóstico real do ambiente
python -m unittest discover -s tests -t .  # testes
```
Opcional (renderização JS): `pip install playwright` e, se o Playwright não achar seu Chromium, `PRECOS_CHROMIUM_PATH=/caminho/chrome`.
Chaves de busca (opcionais): copie `.env.example` para `.env` (ignorado pelo git) e preencha `BRAVE_API_KEY` ou `GOOGLE_CSE_KEY`+`GOOGLE_CSE_CX`.

## Uso rápido (exemplo real)

```bash
python -m precos nova "Line 6 HX Stomp" --modelo "line 6 hx stomp" --condicao novo --parcelas 12   # sem CEP: frete não é calculado
python -m precos candidato "https://loja.com.br/hx-stomp" --titulo "Line 6 HX Stomp"               # visto numa busca: NÃO confirmado
python -m precos verificar "https://loja.com.br/hx-stomp"                                          # abre a página e lê o preço na fonte
python -m precos anotar 4 --fonte "carrinho 24/10 14:02, CEP 01001-000" --frete 49,90              # dado lido por você, com fonte
python -m precos loja loja.com.br --tier autorizado --fonte https://marca.com/revendedores         # classificação exige URL
python -m precos comparar                                                                          # ranking no terminal
python -m precos relatorio --saida relatorio.md                                                    # formato A–F
```
Sem `-c`, os comandos usam a **última** consulta.

| Comando | Para quê |
|---|---|
| `doctor [--navegador]` | O que funciona/não funciona neste ambiente (testa de verdade) |
| `nova` | Cria a consulta: `--modelo`, `--excluir`, `--condicao`, `--gtin`, `--pagamento vista\|parcelado`, `--parcelas`, `--cep`, `--prioridade`, `--sem-marketplace` |
| `candidato` | Registra URL/preço vistos em busca como **não confirmados** |
| `descobrir` | Busca via API oficial (exige chave); `--registrar` salva como candidatos |
| `verificar [--fresco] [--renderizar]` | Lê a página da oferta (robots, ritmo, cache) |
| `anotar` | Registra frete/Pix/parcelas/garantia/vendedor lidos manualmente — `--fonte` obrigatório |
| `loja` | Registra fatos sobre a loja; tier/reputação/políticas **exigem URL de fonte** |
| `cnpj`, `cep` | Consultas avulsas (Receita via BrasilAPI, ViaCEP) |
| `comparar`, `relatorio` | Ranking e relatório completo |
| `monitor add\|list\|check\|remove` | Preço-alvo (ver Monitoramento) |

## Como o agente decide

**Custo real** (`pricing.py`, Decimal): à vista = preço + frete + taxas; parcelado = soma das parcelas + frete + taxas;
economia = referência − alternativa (R$ e %). Frete só entra se foi lido/informado — senão o total vem marcado **"sem frete"**.
Para parcelas calcula juros embutidos e a taxa mensal implícita (estimativa a partir dos valores lidos). "Sem juros" declarado
pela loja mas com n×parcela ≠ preço vira **divergente**.

**Pontuação 0–100** (`scoring.py`): custo 45 · confiabilidade 25 · pagamento 15 · entrega 10 · garantia 5 (ajustada por
`--prioridade`). Critério **não verificado vale 0** (nunca nota máxima nem "neutra") e reduz a confiança.

**Confiabilidade da loja** (soma de componentes, cada um só pontua com evidência; heurística interna, **não certificação**):
identidade/CNPJ ativo e compatível 25 · tempo de operação 10 · idade do domínio 5 · canal de venda (oficial/autorizado 20, varejista 15,
marketplace verificável 10 — exige URL) · reputação independente 30 (exige URL, vale metade se > 12 meses) · políticas 10 (exige URL).
Em marketplaces o CNPJ/domínio **não** descrevem o vendedor do anúncio.

**Exclusões** (saem da recomendação mesmo se forem as mais baratas): produto divergente (modelo/variante XL/Pro, usado, acessório,
sinais **contraditórios** de condição entre nome, `<title>`, `h1` e a slug da URL), sinal de risco alto (CNPJ baixado/inapta, preço
< 50% da mediana com ≥ 3 lojas, loja marcada como risco), sem estoque, marketplace recusado, prioridade "oficial" sem prova.

**Confiança da pesquisa**: *alta* só com preço, produto, vendedor e condição de pagamento da vencedora **confirmados**, loja com ≥ 40% de
verificação independente, dados < 6 h e **≥ 3 lojas** comparáveis; com 2 lojas no máximo *média*; **com 1 loja é *baixa*** e o relatório
não a chama de "melhor compra". Níveis de evidência por campo: confirmado diretamente · confirmado por fonte independente · estimado ·
não verificado · divergente.

## Salvaguardas embutidas
- `robots.txt` obedecido com curingas (`*`, `$`) — o `urllib.robotparser` não os entende e liberaria `Disallow: *?q=`; se o robots não puder ser lido, **não acessa**.
- Sem burlar nada: 403/429/captcha/redirecionamento para verificação viram *bloqueada*, sem repetir; User-Agent honesto; sem proxies.
- Hosts internos (`localhost`, redes privadas) recusados; limite de 3 MB por página; timeouts; retentativas só para falhas transitórias.
- Chaves só por variável de ambiente/`.env`; `repr` da configuração e logs não as mostram (teste cobre); `data/` e `.env` ignorados pelo git.
- Observações são *append-only*: cada leitura grava horário (Brasília), fonte e evidência.

## Monitoramento
`monitor add --alvo 3.500 --intervalo-h 24 --ate 2026-12-31 [--lojas a.com.br,b.com.br] [--conf-min média]` salva a regra.
`monitor check` reverifica as URLs da consulta, compara o **menor preço elegível** ao alvo e **imprime** `ALERTA`.
**Nada disso roda sozinho e não há canal de notificação.** Para monitorar de verdade você precisa de:
1. um agendador numa máquina **que mantenha `data/precos.db`**: ex. cron `0 */6 * * * cd /caminho/agente-precos && python3 -m precos monitor check >> data/monitor.log 2>&1`;
2. algo que entregue o alerta (e-mail do cron, um script que leia `ALERTA` do log e chame seu serviço de push/Telegram etc.).
Em sessões na nuvem o container é efêmero (o banco some); GitHub Actions também não preserva `data/` sem configuração extra. Tarefas agendadas do Claude Code só devem ser criadas a seu pedido.

## Manutenção
- **Testes**: `python -m unittest discover -s tests -t .` (use `-W error::ResourceWarning` para pegar vazamentos). Todo bug achado em site real ganha teste de regressão (ver `RegressoesReaisTest`).
- **Novos sites**: não escreva parser por loja. O extrator lê JSON-LD → meta → texto visível. Se uma loja não expõe preço estruturado, ela fica "não verificada" (ou use `--renderizar`). Não adicione parser para sites cujos termos vedem coleta automatizada.
- **Banco e cache**: `data/precos.db` (SQLite) e `data/cache/`. Apagar `data/` zera tudo; o cache expira em 30 min e o `robots.txt` em 24 h.
- **Cadastro de lojas** (CNPJ/domínio) é reconsultado após 30 dias. Marketplaces conhecidos ficam em `seller.py` (`MARKETPLACES`).
- **Ritmo/limites**: `PRECOS_INTERVALO_S`, `PRECOS_DESATUALIZADA_H` (variáveis de ambiente); demais padrões em `config.py`.
- **Heurísticas a revisar com o tempo**: pesos/rubricas (`scoring.py`), palavras de condição/variante (`matching.py`), padrões de Pix/parcela (`extract.py`).

## Limitações conhecidas
- Pix/parcelas lidos do **texto** da página são "estimado" até serem confirmados no checkout (`anotar`).
- A checagem de produto é por título/URL/GTIN: ajuda, mas **não é prova**; GTIN igual em ≥ 2 lojas eleva para "confirmado por fonte independente".
- O vínculo entre um CNPJ citado no rodapé e o dono do site é inferido por semelhança de nome (estimado).
- Sem cashback/cupom: nenhum é verificado nem somado.
- Dados de busca (WebSearch) são restritos aos EUA como região, mas com `allowed_domains` retornam fichas brasileiras; os preços nesses resumos são **sempre não confirmados**.
