# Build
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
# npm ci: instala EXACTAMENTE el lockfile (builds reproducibles).
RUN npm ci
COPY . .
# URL de la API que Vite hornea en el build del panel. Vacía (lo normal, y lo
# que usan las imágenes de GHCR) = la API vive en el mismo dominio que el panel,
# detrás del nginx de esta imagen (nginx.conf). Solo hace falta si la API está
# en otro dominio:
#   --build-arg VITE_API_BASE_URL=https://api.tudominio.com
# Nunca se hornea el dominio de una instalación concreta: vacía, la imagen
# sirve igual en cualquiera.
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# Serve
FROM nginx:1.30-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
# Override en RUNTIME de la URL del backend: la imagen oficial de nginx ejecuta
# los scripts de /docker-entrypoint.d/ antes de arrancar, y este genera
# /config.js a partir de API_BASE_URL. Si API_BASE_URL no se define, config.js
# queda vacío y el frontend usa el valor horneado arriba o, si también está
# vacío, su propio origen.
COPY docker-entrypoint.d/40-app-config.sh /docker-entrypoint.d/40-app-config.sh
RUN chmod +x /docker-entrypoint.d/40-app-config.sh
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
