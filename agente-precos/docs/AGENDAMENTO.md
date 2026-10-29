# Monitoramento de preço-alvo: agendamento e avisos

`python -m precos monitor check` reverifica as ofertas de uma consulta e compara o **menor preço elegível** com o seu alvo.
**Ele não roda sozinho**: alguém precisa chamá-lo periodicamente, e os avisos só saem se um canal estiver configurado.

## 1. Crie a regra
```bash
python -m precos monitor add --alvo 3.500 --pagamento vista --intervalo-h 24 --ate 2026-12-31 [--lojas a.com.br,b.com.br] [--conf-min média]
python -m precos monitor list
```
- `--alvo` compara com o **preço do produto na forma de pagamento escolhida** (sem frete).
- `--conf-min` ignora ofertas cuja confiança seja menor que a informada (`baixa`, `média`, `alta`).
- O monitor expira em `--ate` e desativa a si mesmo.
- O que ele **não** faz: reabrir páginas que o site bloqueou ou que não mostram preço estruturado (não insistimos); essas ofertas só entram se você as registrar com `anotar --preco`.

## 2. Escolha um canal de aviso (opcional)
Preencha só o que for usar no `.env` (veja `.env.example`). Nada é enviado sem `--notificar`.

| Canal | Como configurar | Observação |
|---|---|---|
| **ntfy** | Instale o app ntfy, assine um tópico **longo e aleatório** e coloque em `NTFY_TOPIC` | Em `ntfy.sh` o nome do tópico funciona como senha. Servidor próprio: `NTFY_SERVER` (+ `NTFY_TOKEN`) |
| **Telegram** | Crie um bot com `@BotFather` → `TELEGRAM_BOT_TOKEN`; descubra seu chat id → `TELEGRAM_CHAT_ID` | O token é segredo; nunca vai para log |
| **Webhook** | `ALERT_WEBHOOK_URL` | Recebe `POST` JSON: `{"origem":"agente-precos","titulo":"…","mensagem":"…"}` |

A mensagem leva: produto, loja, preço, alvo, link e horário. **Não** leva CEP nem dados pessoais.
Não repete o mesmo aviso para o mesmo preço; se o envio falhar, tenta de novo na próxima execução.
Teste: `python -m precos doctor` lista os canais configurados (sem mostrar segredos).

> Estado de validação: o formato das mensagens e o tratamento de erro estão cobertos por testes com respostas simuladas.
> **Nenhum envio real foi feito** (isso publicaria uma mensagem num serviço externo); confirme com um alvo alto, por exemplo `--alvo 99999`, na primeira vez.

## 3. Agende
`scripts/monitor.sh` roda `monitor check --notificar`, grava em `data/monitor.log`, impede execuções sobrepostas (código 75) e mantém o log pequeno.

**cron** (a cada 6 horas):
```
0 */6 * * * /caminho/agente-precos/scripts/monitor.sh
```
**systemd** (`~/.config/systemd/user/precos-monitor.service` e `.timer`):
```ini
[Service]
Type=oneshot
ExecStart=/caminho/agente-precos/scripts/monitor.sh

[Timer]
OnCalendar=*-*-* 00/6:00:00
Persistent=true

[Install]
WantedBy=timers.target
```
Ative com `systemctl --user enable --now precos-monitor.timer`.

## 4. Limites que você precisa conhecer
- A máquina precisa estar **ligada** e manter `data/precos.db` (é onde ficam regras, histórico e o último aviso enviado).
- Sessões do Claude Code na nuvem têm container efêmero: o banco some. Use sua máquina ou um servidor.
- GitHub Actions não preserva `data/` entre execuções sem configuração extra, e o histórico de preços não deve ir para um repositório público.
- Respeite o ritmo: intervalos menores que algumas horas só aumentam a chance de o site bloquear o robô; o padrão é 24 h por regra.
