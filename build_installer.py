"""Gera install.sh com todos os módulos embutidos (rode: python3 build_installer.py)."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["geo.py", "catalog.py", "store.py", "analysis.py", "mil_watch.py", "dashboard.py"]

HEAD = r'''#!/usr/bin/env bash
# Instalador do mil-watch v2 (coleta global + painel) para VPS Linux com systemd.
# Uso:     sudo bash install.sh            (instala ou atualiza; mantém .env e histórico)
#          sudo bash install.sh --uninstall
set -euo pipefail

APP_DIR=/opt/mil-watch-br
APP_USER=milwatch
SERVICE=mil-watch-br

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERRO:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "rode como root: sudo bash install.sh"
command -v systemctl >/dev/null || die "systemd não encontrado nesta VM"

if [ "${1:-}" = "--uninstall" ]; then
  say "Removendo serviços (os dados em $APP_DIR/data são mantidos)"
  systemctl disable --now "$SERVICE" "$SERVICE-dashboard" 2>/dev/null || true
  rm -f "/etc/systemd/system/$SERVICE.service" "/etc/systemd/system/$SERVICE-dashboard.service"
  systemctl daemon-reload
  say "Removido. Para apagar tudo: rm -rf $APP_DIR && userdel $APP_USER"
  exit 0
fi

if ! command -v python3 >/dev/null || ! python3 -c 'import sys; sys.exit(sys.version_info < (3,9))'; then
  say "Instalando Python 3"
  if command -v apt-get >/dev/null; then apt-get update -qq && apt-get install -y -qq python3
  elif command -v dnf >/dev/null; then dnf install -y -q python3
  else die "instale python3 (3.9+) manualmente e rode de novo"; fi
fi
say "Python: $(python3 --version)"

id "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR/data"

# Backup do banco antes de atualizar (a migração para v2 é automática)
if [ -f "$APP_DIR/data/milwatch.db" ]; then
  systemctl stop "$SERVICE" "$SERVICE-dashboard" 2>/dev/null || true
  cp "$APP_DIR/data/milwatch.db" "$APP_DIR/data/milwatch.db.bak-$(date +%Y%m%d-%H%M%S)"
  ls -1t "$APP_DIR"/data/milwatch.db.bak-* 2>/dev/null | tail -n +4 | xargs -r rm -f
  say "Backup do banco feito em $APP_DIR/data/ (mantidos os 3 mais recentes)"
fi
'''

TAIL = r'''python3 -m py_compile "$APP_DIR"/*.py || die "falha ao validar o programa"
rm -rf "$APP_DIR/__pycache__"

# ---------------------------------------------------------------- configuração
if [ ! -f "$APP_DIR/.env" ]; then
  TOKEN="${TELEGRAM_TOKEN:-}"; CHAT="${TELEGRAM_CHAT_ID:-}"
  if [ -z "$TOKEN" ] && [ -r /dev/tty ]; then read -r -p "Token do bot do Telegram (Enter para pular): " TOKEN < /dev/tty || true; fi
  if [ -n "$TOKEN" ] && [ -z "$CHAT" ] && [ -r /dev/tty ]; then read -r -p "Chat ID do Telegram: " CHAT < /dev/tty || true; fi
  [ -n "$TOKEN" ] || warn "Sem Telegram: alertas só no log. Edite $APP_DIR/.env depois."
  printf 'TELEGRAM_TOKEN=%s\nTELEGRAM_CHAT_ID=%s\n' "$TOKEN" "$CHAT" > "$APP_DIR/.env"
else
  say "Mantendo configuração existente em $APP_DIR/.env"
fi
NEW_PASS=""
add_default() {  # chave, valor, comentário
  if ! grep -q "^$1=" "$APP_DIR/.env"; then
    [ -n "${3:-}" ] && printf '\n# %s\n' "$3" >> "$APP_DIR/.env"
    printf '%s=%s\n' "$1" "$2" >> "$APP_DIR/.env"
  fi
}
add_default INTERVAL_SECONDS 90 "Coleta"
add_default API_SOURCES "https://api.adsb.lol/v2/mil,https://api.airplanes.live/v2/mil"
add_default DB_PATH data/milwatch.db
add_default LOG_PATH data/milwatch.log
add_default SESSION_GAP_MINUTES 30
add_default RAW_RETENTION_DAYS 30 "Dias de pontos brutos guardados (rotas resumidas e estatísticas ficam para sempre)"
add_default BRAZIL_ALERT_SCOPE US "Alerta de entrada no Brasil: US | NATO | FOREIGN (todos menos BR) | ALL"
add_default ALERT_COOLDOWN_HOURS 6
add_default ALERT_EVENTS emergencia,anomalia "Eventos que vão para o Telegram: emergencia,anomalia,orbita,mudanca_rota,descida_rapida,sinal_perdido"
add_default ALERT_EVENT_REGIONS "" "Limita alertas de evento (exceto emergência) a estas regiões; vazio = todas"
add_default ALERT_IGNORE_REGIONS "América do Norte,Alasca" "Regiões cujos eventos e anomalias ficam só no painel (sem Telegram)"
add_default ANOMALY_ALERT_Z 3.0 "Índice de Atividade Anômala"
add_default ANOMALY_MIN_DAYS 14
add_default ANOMALY_BASELINE_DAYS 28
add_default DIGEST_HOUR 8 "Resumo diário no Telegram (hora local; -1 desliga)"
add_default TIMEZONE America/Sao_Paulo
add_default REPORT_EVERY_DAYS 7
if ! grep -q '^DASHBOARD_PASSWORD=' "$APP_DIR/.env"; then
  NEW_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
fi
add_default DASHBOARD_PORT "${DASHBOARD_PORT:-8088}" "Painel web"
add_default DASHBOARD_BIND 0.0.0.0
add_default DASHBOARD_USER admin
[ -n "$NEW_PASS" ] && add_default DASHBOARD_PASSWORD "$NEW_PASS"
DASH_PORT="$(grep '^DASHBOARD_PORT=' "$APP_DIR/.env" | tail -1 | cut -d= -f2)"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"

if python3 -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('https://api.adsb.lol/v2/mil', headers={'User-Agent':'mil-watch-br/2.0'}), timeout=20)" 2>/dev/null; then
  say "API adsb.lol acessível"
else
  warn "Não consegui acessar api.adsb.lol agora (o serviço tenta a fonte reserva e repete sozinho)"
fi

# ---------------------------------------------------------------- serviços
unit() {  # nome, descrição, script, restart
cat > "/etc/systemd/system/$1.service" <<EOF
[Unit]
Description=$2
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$(command -v python3) -u $APP_DIR/$3
Restart=always
RestartSec=$4
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$APP_DIR/data

[Install]
WantedBy=multi-user.target
EOF
}
say "Criando serviços"
unit "$SERVICE" "mil-watch - coletor global de aeronaves militares" mil_watch.py 30
unit "$SERVICE-dashboard" "mil-watch - painel web" dashboard.py 10
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "$DASH_PORT/tcp" >/dev/null && say "Porta $DASH_PORT liberada no ufw"
fi
systemctl daemon-reload
systemctl enable "$SERVICE" "$SERVICE-dashboard" >/dev/null 2>&1
systemctl restart "$SERVICE"
sleep 4
systemctl restart "$SERVICE-dashboard"
sleep 3

systemctl is-active --quiet "$SERVICE" || { journalctl -u "$SERVICE" -n 40 --no-pager; die "o coletor não subiu; veja o log acima"; }
say "Coletor rodando. Últimas linhas do log:"
journalctl -u "$SERVICE" -n 4 --no-pager -o cat || true
IP_PUB="$(hostname -I 2>/dev/null | awk '{print $1}')"
if systemctl is-active --quiet "$SERVICE-dashboard"; then
  say "Painel: http://${IP_PUB:-IP_DA_VM}:$DASH_PORT   (usuário: admin)"
  if [ -n "$NEW_PASS" ]; then say "Senha do painel (anote!): $NEW_PASS"
  else say "Senha do painel: a que está em $APP_DIR/.env (DASHBOARD_PASSWORD)"; fi
else
  journalctl -u "$SERVICE-dashboard" -n 20 --no-pager || true
  warn "o painel não subiu (porta $DASH_PORT ocupada? mude DASHBOARD_PORT no .env e rode: systemctl restart $SERVICE-dashboard)"
fi
cat <<EOF

Pronto. Deve chegar no Telegram: "🟢 mil-watch-br v2 iniciado: monitoramento global ativo".
  Coletor ao vivo:  journalctl -u $SERVICE -f
  Painel ao vivo:   journalctl -u $SERVICE-dashboard -f
  Configuração:     nano $APP_DIR/.env   (depois: systemctl restart $SERVICE $SERVICE-dashboard)
  Banco de dados:   $APP_DIR/data/milwatch.db  (backup anterior ao lado)
EOF
'''


def main():
    parts = [HEAD]
    for name in FILES:
        src = open(os.path.join(HERE, name), encoding="utf-8").read()
        tag = "MILWATCH_EOF_" + name.replace(".", "_").upper()
        assert tag not in src
        parts.append(f'say "Gravando $APP_DIR/{name}"\ncat > "$APP_DIR/{name}" <<\'{tag}\'\n{src}'
                     f'{"" if src.endswith(chr(10)) else chr(10)}{tag}\n')
    parts.append(TAIL)
    out = os.path.join(HERE, "install.sh")
    with open(out, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    os.chmod(out, 0o755)
    print("gerado:", out)


if __name__ == "__main__":
    main()
