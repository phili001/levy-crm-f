import { api } from "@/services/api";

// Instagram y Messenger por Zernio (Fase 7). Ver backend/app/api/zernio_admin.py.

const BASE = "/admin/zernio";

export interface CuentaZernio {
  id: string;
  platform: "instagram" | "facebook" | "whatsapp" | string;
  username: string;
  display_name: string;
  profile_id: string;
  needs_reconnection: boolean;
}

export interface CanalZernio {
  channel_id: string;
  tipo: "instagram_dm" | "messenger";
  nombre: string;
  account_id: string;
  enabled: boolean;
  estado: "connected" | "disconnected" | "";
}

export interface EstadoZernio {
  tiene_llave: boolean;
  webhook_id: string;
  webhook_url: string;
  canales: CanalZernio[];
  wa_por_zernio: boolean;
}

export interface ConectarZernioIn {
  api_key?: string;
  instagram_account_id?: string;
  facebook_account_id?: string;
  wa_por_zernio: boolean;
  whatsapp_account_id?: string;
}

export interface ConectarZernioOut extends EstadoZernio {
  webhook_registrado: boolean;
  webhook_secret_manual: string | null;
  aviso: string | null;
  historial_encolado: number;
}

export async function estadoZernio(): Promise<EstadoZernio> {
  const { data } = await api.get<EstadoZernio>(BASE);
  return data;
}

export async function probarZernio(apiKey?: string): Promise<CuentaZernio[]> {
  const { data } = await api.post<CuentaZernio[]>(`${BASE}/probar`, { api_key: apiKey || undefined });
  return data;
}

export async function conectarZernio(body: ConectarZernioIn): Promise<ConectarZernioOut> {
  const { data } = await api.post<ConectarZernioOut>(BASE, body);
  return data;
}

export async function traerHistorialZernio(): Promise<{ canales: number }> {
  const { data } = await api.post<{ ok: boolean; canales: number }>(`${BASE}/historial`);
  return data;
}

export async function probarWebhookZernio(): Promise<void> {
  await api.post(`${BASE}/probar-webhook`);
}
