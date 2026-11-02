import axios, { AxiosInstance } from "axios";

import { clearStoredSession, getToken } from "@/lib/session";

// La URL del backend se resuelve en RUNTIME primero: el contenedor genera
// /config.js (window.__APP_CONFIG__) desde la variable API_BASE_URL. Respaldos:
// el valor de build (VITE_API_BASE_URL) y localhost. Así el MISMO build sirve en
// varios servidores — cada uno pone su API_BASE_URL sin recompilar — y si no se
// define, se cae al default de build (compatibilidad con el despliegue actual).
// Se quita la barra final (igual que en mcpEndpoint, más abajo): con una
// API_BASE_URL escrita como "https://api.dominio.com/" el baseURL quedaba
// ".../api/v1" con doble barra y todas las llamadas —empezando por el login—
// respondían "Not Found" sin pista de por qué.
//
// Sin ninguna de las dos, la API está en el MISMO origen que el panel: el nginx
// del frontend reenvía /api, /widget.js, /audios y /health (frontend/nginx.conf).
// Es el caso de las imágenes publicadas en GHCR. Solo `npm run dev` (Vite suelto,
// sin ese nginx delante) sigue cayendo a la API local en el 8000.
export function apiBaseHttp(): string {
  return (
    window.__APP_CONFIG__?.apiBaseUrl ||
    import.meta.env.VITE_API_BASE_URL ||
    (import.meta.env.DEV ? "http://localhost:8000" : window.location.origin)
  ).replace(/\/+$/, "");
}

export function apiBaseWs(): string {
  return apiBaseHttp().replace(/^http/, "ws");
}

// Endpoint del servidor MCP (transporte Streamable HTTP, ruta /mcp). Lo sirve la
// propia API (backend/app/mcp_server.py), así que lo normal es derivarlo de ella:
// con la API en el mismo dominio que el panel (Coolify), el nginx del panel
// reenvía /mcp y queda https://<dominio-del-crm>/mcp. MCP_BASE_URL (runtime) solo
// hace falta si el MCP se publica en otro dominio que el de la API.
export function mcpEndpoint(): string {
  const base = window.__APP_CONFIG__?.mcpBaseUrl || import.meta.env.VITE_MCP_BASE_URL;
  if (base) return base.replace(/\/+$/, "") + "/mcp";
  return apiBaseHttp().replace(/\/+$/, "") + "/mcp";
}

const baseURL = apiBaseHttp() + "/api/v1";

export const api: AxiosInstance = axios.create({
  baseURL,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (error) => {
    if (error.response?.status === 401) {
      // Limpieza COMPLETA. Antes solo se borraba la clave `token` y el JWT se
      // quedaba escrito dentro del estado persistido del store: al recargar,
      // la app se rehidrataba con una sesión muerta. Y el historial del
      // Agente Interno seguía ahí para el siguiente usuario del equipo.
      clearStoredSession();
      window.dispatchEvent(new Event("app:session-cleared"));
      if (!window.location.pathname.endsWith("/login")) {
        // Conserva dónde estabas: tras volver a entrar, Login te devuelve ahí.
        const from = window.location.pathname + window.location.search;
        window.location.href = "/login?from=" + encodeURIComponent(from);
      }
    }
    return Promise.reject(error);
  }
);
