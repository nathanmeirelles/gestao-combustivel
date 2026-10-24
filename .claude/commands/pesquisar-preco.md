---
description: Pesquisa o melhor preço de um produto com verificação na fonte, comparação de pagamento e relatório com evidências
argument-hint: <produto> [novo|usado] [à vista|Nx] [CEP]
---

Siga rigorosamente o protocolo em `agente-precos/PROTOCOLO.md` (leia-o agora) para pesquisar: $ARGUMENTS

Resumo do que você deve fazer: rodar `python -m precos doctor` dentro de `agente-precos/`; identificar o produto (perguntando
só o que muda a recomendação); criar a consulta; descobrir candidatos com WebSearch + `allowed_domains`; verificar cada página
na fonte; completar dados de loja com fontes; comparar; entregar o relatório A–F.

Regras: não invente nada; preço de busca é "não confirmado"; não contorne bloqueios; não compre nem cadastre nada; diga a
confiança da pesquisa e por quê.
