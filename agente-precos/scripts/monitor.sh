#!/usr/bin/env sh
# Roda `monitor check --notificar` e registra a saída em data/monitor.log. Pensado para cron/systemd (ver docs/AGENDAMENTO.md).
#   - não deixa duas execuções se sobreporem (flock, quando disponível; sai com 75 se já houver uma rodando)
#   - mantém o log pequeno (acima de 1 MB guarda só os últimos 256 KB)
#   - PRECOS_DATA_DIR e PRECOS_PYTHON podem ser definidos no ambiente
set -u
RAIZ=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$RAIZ" || exit 1
DADOS=${PRECOS_DATA_DIR:-$RAIZ/data}
PY=${PRECOS_PYTHON:-python3}
mkdir -p "$DADOS" || exit 1
LOG="$DADOS/monitor.log"

if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 1048576 ]; then
  tail -c 262144 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

rc=0
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %z') monitor check ==="
  if command -v flock >/dev/null 2>&1; then
    flock -n -E 75 "$DADOS/monitor.lock" "$PY" -m precos monitor check --notificar
    rc=$?
    [ "$rc" -eq 75 ] && echo "[aviso] outra execução ainda está em andamento; esta foi pulada."
  else
    "$PY" -m precos monitor check --notificar
    rc=$?
  fi
  [ "$rc" -ne 0 ] && [ "$rc" -ne 75 ] && echo "[erro] monitor check terminou com código $rc"
  exit "$rc"
} >> "$LOG" 2>&1
