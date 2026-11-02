#!/usr/bin/env bash
# Instala el CRM en un VPS pelado, sin panel de control (camino C).
#
# Qué hace: genera los tres secretos, pregunta el dominio y el administrador,
# escribe un .env con permisos 600 y levanta el stack con Caddy delante.
#
# Qué NO hace: tocar un .env que ya exista. Si vuelves a ejecutarlo sobre una
# instalación hecha, reutiliza el que hay. Copiar una plantilla encima borraría
# la ENCRYPTION_KEY y dejaría ilegibles para siempre las credenciales, las notas
# internas y las transcripciones ya cifradas.
set -euo pipefail

cd "$(dirname "$0")"
ENV_FILE=".env"

echo "== CRM Omnicanal — instalación =="

if [ -f "$ENV_FILE" ]; then
  echo "Ya hay un $ENV_FILE: se reutiliza tal cual (no se toca ni una línea)."
else
  read -rp "Dominio del CRM (ej. crm.miclinica.mx): " CRM_DOMAIN
  # Se limpia https:// y la barra final: es el error de copiar y pegar de
  # siempre, y deja el certificado sin emitir sin decir por qué.
  CRM_DOMAIN="${CRM_DOMAIN#http://}"; CRM_DOMAIN="${CRM_DOMAIN#https://}"; CRM_DOMAIN="${CRM_DOMAIN%%/*}"
  read -rp "Correo del administrador: " ADMIN_EMAIL
  read -rsp "Contraseña del administrador (12+ caracteres, letras y números): " ADMIN_PASS; echo
  if [ "${#ADMIN_PASS}" -lt 12 ]; then
    echo "ERROR: la contraseña necesita 12 caracteres o más; el panel no aceptaría una más corta." >&2
    exit 1
  fi
  read -rp "Perfil de instalación [dental-mx]: " SEED_PROFILE
  SEED_PROFILE="${SEED_PROFILE:-dental-mx}"

  umask 077
  cat > "$ENV_FILE" <<EOF
# Generado por install.sh el $(date -u +%Y-%m-%dT%H:%M:%SZ). NO lo subas a git.
# La ENCRYPTION_KEY no se cambia nunca: lo ya cifrado quedaría ilegible.
CRM_DOMAIN=$CRM_DOMAIN
INITIAL_ADMIN_EMAIL=$ADMIN_EMAIL
INITIAL_ADMIN_PASSWORD=$ADMIN_PASS
SEED_PROFILE=$SEED_PROFILE
POSTGRES_PASSWORD=$(openssl rand -hex 24)
JWT_SECRET=$(openssl rand -base64 48)
ENCRYPTION_KEY=$(openssl rand -base64 32 | tr '+/' '-_')
APP_VERSION=${APP_VERSION:-latest}
WORKER_CONCURRENCY=2
TRUSTED_PROXY_COUNT=2
BUSINESS_TZ=America/Cancun
EOF
  chmod 600 "$ENV_FILE"
  echo "Escrito $ENV_FILE (permisos 600). Guarda una copia fuera del servidor."
fi

echo "Levantando el stack…"
docker compose -f compose.yml --env-file "$ENV_FILE" up -d

DOMINIO="$(grep '^CRM_DOMAIN=' "$ENV_FILE" | cut -d= -f2-)"
echo "Esperando a que la API responda (el primer arranque migra y siembra; puede tardar un par de minutos)…"
for _ in $(seq 1 60); do
  # Por dentro, para no depender de que el DNS o el certificado ya estén listos.
  if docker compose -f compose.yml --env-file "$ENV_FILE" exec -T app \
       curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    echo "API en verde."
    echo
    echo "Panel: https://$DOMINIO"
    echo "Entra con $(grep '^INITIAL_ADMIN_EMAIL=' "$ENV_FILE" | cut -d= -f2-) y la contraseña que pusiste."
    echo "Si el navegador se queja del certificado, dale un minuto a Caddy y recarga."
    exit 0
  fi
  sleep 5
done

echo "ERROR: la API no respondió en 5 minutos. Mira los registros:" >&2
echo "  docker compose -f compose.yml --env-file $ENV_FILE logs app | tail -50" >&2
exit 1
