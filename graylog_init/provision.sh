#!/bin/sh
# Attend que l'API Graylog soit disponible, puis crée l'input Syslog UDP
# si il n'existe pas déjà.

GRAYLOG_URL="http://graylog:9000"
AUTH="admin:${GRAYLOG_ROOT_PASSWORD:-admin}"
MAX_WAIT=120
WAITED=0

echo "[provision] Attente de Graylog sur ${GRAYLOG_URL}..."
until curl -sf -u "${AUTH}" "${GRAYLOG_URL}/api/system/inputs" -o /dev/null 2>&1; do
  sleep 5
  WAITED=$((WAITED + 5))
  if [ $WAITED -ge $MAX_WAIT ]; then
    echo "[provision] Timeout après ${MAX_WAIT}s — Graylog non disponible."
    exit 1
  fi
  echo "[provision] En attente... (${WAITED}s)"
done

echo "[provision] Graylog disponible. Vérification de l'input Syslog UDP..."

# Vérifie si un input Syslog UDP existe déjà sur le port 5140
EXISTING=$(curl -sf -u "${AUTH}" \
  -H "Accept: application/json" \
  "${GRAYLOG_URL}/api/system/inputs" | \
  grep -c '"org.graylog2.inputs.syslog.udp.SyslogUDPInput"' || true)

if [ "${EXISTING}" -gt 0 ]; then
  echo "[provision] Input Syslog UDP déjà présent — rien à faire."
  exit 0
fi

echo "[provision] Création de l'input Syslog UDP (port 5140)..."

curl -sf -u "${AUTH}" \
  -X POST \
  -H "Content-Type: application/json" \
  -H "X-Requested-By: provision-script" \
  "${GRAYLOG_URL}/api/system/inputs" \
  -d '{
    "title":  "EnergyMe Syslog UDP",
    "type":   "org.graylog2.inputs.syslog.udp.SyslogUDPInput",
    "global": true,
    "configuration": {
      "bind_address": "0.0.0.0",
      "port":         5140,
      "recv_buffer_size": 262144,
      "number_worker_threads": 2,
      "override_source": null,
      "force_rdns": false,
      "allow_override_date": true,
      "store_full_message": false,
      "expand_structured_data": false
    }
  }'

echo ""
echo "[provision] Input Syslog UDP créé sur le port 5140 (hôte: 514/udp)."
