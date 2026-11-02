#!/usr/bin/env bash
# Smoke test E2E del despliegue. Asume docker compose corriendo.
# Uso: bash scripts/smoke.sh
# Contra los simuladores (docker compose --profile dev up):
#   MOCKS=http://localhost MOCKS_DESDE_CRM=http://mocks API_DESDE_MOCKS=http://app:8000 \
#   EMAIL=… PASS=… bash scripts/smoke.sh

set -e

API=${API:-http://localhost:8000}
# Sin valores por defecto a propósito: cada instalación tiene su propio admin.
EMAIL=${EMAIL:?define EMAIL con el admin de tu instalación}
PASS=${PASS:?define PASS con la contraseña de ese admin}

echo "== 1. Healthcheck =="
curl -sf "$API/health" | tee /dev/null
echo

echo "== 2. Login =="
TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
echo "Token OK ($(echo "$TOKEN" | cut -c1-20)…)"

AUTH="Authorization: Bearer $TOKEN"

echo "== 3. /auth/me =="
curl -sf "$API/api/v1/auth/me" -H "$AUTH"
echo

echo "== 4. Admin health =="
curl -sf "$API/api/v1/admin/health" -H "$AUTH"
echo

echo "== 5. Tags =="
curl -sf "$API/api/v1/tags" -H "$AUTH"
echo

echo "== 6. Conversations (vacío al inicio) =="
curl -sf "$API/api/v1/conversations?page=1" -H "$AUTH"
echo

echo "== 7. Agent config =="
curl -sf "$API/api/v1/admin/agent/config" -H "$AUTH" | head -c 300
echo
echo

# El webhook de YCloud verifica la firma SIEMPRE (backend/app/api/webhooks.py):
# no hay modo "sin secret en dev". Sin cabecera válida devuelve 401, y eso es lo
# CORRECTO — antes este paso iba con `curl -sf`, así que el 401 cortaba el script
# por el `set -e` de arriba y "Smoke OK" no se imprimía nunca.
#
# Dos modos, según tengas o no el secreto a mano:
#   - Con YCLOUD_WEBHOOK_SECRET exportado (el MISMO valor que hay guardado en el
#     panel, Conexiones → WhatsApp): se firma el payload como lo firma YCloud
#     (HMAC-SHA256 sobre "<marca de tiempo>.<cuerpo>") y se espera un 200.
#   - Sin él: se manda sin firma y se espera un 401. Comprueba que la puerta está
#     cerrada, que no es poco, pero NO prueba la entrada de mensajes.
PAYLOAD='{"type":"whatsapp.inbound_message.received","whatsappInboundMessage":{"id":"smoke-1","from":"+34600111222","to":"+34900000000","type":"text","text":{"body":"Hola, ¿cuánto cuesta un corte?"}}}'
BODY_FILE=$(mktemp)

if [ -n "${YCLOUD_WEBHOOK_SECRET:-}" ]; then
  echo "== 8. Webhook YCloud (payload FIRMADO — se espera 200) =="
  TS=$(date +%s)
  # awk '{print $NF}': el formato de openssl cambia entre versiones
  # ("(stdin)= abc…" / "SHA2-256(stdin)= abc…"); el hash siempre es lo último.
  SIG=$(printf '%s' "$TS.$PAYLOAD" \
    | openssl dgst -sha256 -hmac "$YCLOUD_WEBHOOK_SECRET" | awk '{print $NF}')
  CODE=$(curl -s -o "$BODY_FILE" -w '%{http_code}' -X POST "$API/api/v1/webhooks/ycloud" \
    -H "Content-Type: application/json" \
    -H "YCloud-Signature: t=$TS,s=$SIG" \
    -d "$PAYLOAD")
  cat "$BODY_FILE"; echo
  if [ "$CODE" != "200" ]; then
    rm -f "$BODY_FILE"
    echo "FALLO: se esperaba 200 y ha respondido $CODE."
    if [ "$CODE" = "401" ]; then
      echo "El backend ha rechazado la firma: el secreto que has exportado no es el que"
      echo "hay guardado en el panel (o no hay ninguno guardado). Míralo en Conexiones →"
      echo "WhatsApp; el motivo exacto sale en Logs en vivo."
    fi
    exit 1
  fi
  echo "Webhook OK: mensaje firmado aceptado."
else
  echo "== 8. Webhook YCloud SIN firma (se espera 401: la firma es obligatoria) =="
  CODE=$(curl -s -o "$BODY_FILE" -w '%{http_code}' -X POST "$API/api/v1/webhooks/ycloud" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")
  cat "$BODY_FILE"; echo
  if [ "$CODE" != "401" ]; then
    rm -f "$BODY_FILE"
    echo "FALLO: se esperaba 401 (sin firma no se acepta nada) y ha respondido $CODE."
    exit 1
  fi
  echo "401 correcto: el webhook rechaza lo que no viene firmado."
  echo "OJO: este paso NO ha probado la entrada de mensajes. Para probarla, exporta"
  echo "YCLOUD_WEBHOOK_SECRET con el secreto del panel y vuelve a lanzar el script."
fi
rm -f "$BODY_FILE"

# Los webhooks de Meta (WhatsApp Cloud API e Instagram) firman distinto que
# YCloud: HMAC-SHA256 del cuerpo CRUDO en `X-Hub-Signature-256: sha256=<hex>`.
# Se comprueban los dos modos, igual que arriba: con secreto exportado se
# espera que el mensaje entre; sin él, que la puerta esté cerrada.
smoke_meta() {
  local nombre="$1" ruta="$2" secreto="$3" payload="$4"
  local archivo code sig
  archivo=$(mktemp)
  if [ -n "$secreto" ]; then
    echo "== Webhook $nombre (FIRMADO — se espera 200) =="
    sig=$(printf '%s' "$payload" | openssl dgst -sha256 -hmac "$secreto" | awk '{print $NF}')
    code=$(curl -s -o "$archivo" -w '%{http_code}' -X POST "$API$ruta" \
      -H "Content-Type: application/json" \
      -H "X-Hub-Signature-256: sha256=$sig" \
      -d "$payload")
  else
    echo "== Webhook $nombre SIN firma (se espera 401) =="
    code=$(curl -s -o "$archivo" -w '%{http_code}' -X POST "$API$ruta" \
      -H "Content-Type: application/json" -d "$payload")
  fi
  head -c 200 "$archivo"; echo
  rm -f "$archivo"
  if [ -n "$secreto" ] && [ "$code" != "200" ]; then
    echo "FALLO: $nombre firmado respondió $code. El secreto exportado no es el que"
    echo "hay guardado en el panel (Conexiones)."
    return 1
  fi
  if [ -z "$secreto" ] && [ "$code" != "401" ]; then
    echo "FALLO: $nombre sin firma respondió $code; debería rechazarlo con 401."
    return 1
  fi
  echo "$nombre: $code, como se esperaba."
}

echo
smoke_meta "WhatsApp (Meta)" "/api/v1/webhooks/whatsapp/meta" "${META_WA_APP_SECRET:-}" \
  '{"object":"whatsapp_business_account","entry":[{"id":"smoke","changes":[{"field":"messages","value":{"messages":[{"from":"5219981234567","id":"smoke-wa-1","type":"text","text":{"body":"Hola"}}]}}]}]}'

echo
smoke_meta "Instagram" "/api/v1/webhooks/instagram" "${IG_APP_SECRET:-}" \
  '{"object":"instagram","entry":[{"id":"smoke","messaging":[{"sender":{"id":"1"},"recipient":{"id":"2"},"message":{"mid":"smoke-ig-1","text":"Hola"}}]}]}'

# Retell: el webhook de llamadas solo está montado si el módulo de voz está
# activo. Un 404 aquí no es un fallo, es que esta instalación no usa voz.
echo
echo "== Webhook de voz (Retell) =="
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/api/v1/voice/retell/webhook" \
  -H "Content-Type: application/json" -d '{"event":"call_ended","call":{"call_id":"smoke"}}')
case "$CODE" in
  401|403) echo "Retell: $CODE, rechaza lo que no viene firmado." ;;
  404) echo "Retell: 404, el módulo de voz no está activo en esta instalación." ;;
  200) echo "AVISO: el webhook de Retell aceptó una llamada sin firmar ($CODE)." ;;
  *) echo "Retell: $CODE." ;;
esac

# Punta a punta contra los simuladores (tests/mocks): un mensaje entra firmado
# como YCloud, Sofía contesta con el LLM de prueba y se comprueba que la
# respuesta SALIÓ por el envío de YCloud. Solo si se pide con MOCKS, porque
# reconfigura WhatsApp y el agente: es para desarrollo y la CI, nunca para una
# instalación real (el script se niega si la API no es local).
if [ -n "${MOCKS:-}" ]; then
  echo
  python3 "$(dirname "$0")/smoke_simuladores.py"
fi

# Pendiente, cuando exista su fase:
#   · Instagram y Messenger por Zernio (fase 7): firma X-Zernio-Signature; hay
#     eventos reales anonimizados en backend/tests/fixtures/zernio/. Su
#     simulador se escribe con el adaptador.

echo
echo "Smoke OK ✓"
