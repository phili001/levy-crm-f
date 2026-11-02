// Herramientas de n8n para Sofía (Conexiones → n8n, Fase 11). El backend es
// `backend/app/api/n8n.py`; el contrato con n8n está en docs/n8n.md.
import { api } from "@/services/api";

export type TipoParametro = "texto" | "numero" | "si_no" | "opciones";

export interface ParametroN8n {
  clave: string;
  tipo: TipoParametro;
  descripcion: string;
  obligatorio: boolean;
  sensible?: boolean;
  opciones?: string[];
}

export interface N8nToolIn {
  nombre: string;
  descripcion: string;
  parametros: ParametroN8n[];
  url: string;
  accion: "lectura" | "escritura";
  espera_s: number;
  datos_contacto: boolean;
  activa: boolean;
}

export interface N8nToolOut extends N8nToolIn {
  id: string;
  secreto_enmascarado: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface ProbarOut {
  ok: boolean;
  estado: number | null;
  ms: number;
  respuesta: unknown;
  error: string | null;
}

export async function listN8nTools(): Promise<N8nToolOut[]> {
  const { data } = await api.get<N8nToolOut[]>("/admin/n8n/tools");
  return data;
}

export async function createN8nTool(body: N8nToolIn): Promise<N8nToolOut> {
  const { data } = await api.post<N8nToolOut>("/admin/n8n/tools", body);
  return data;
}

export async function updateN8nTool(id: string, body: N8nToolIn): Promise<N8nToolOut> {
  const { data } = await api.put<N8nToolOut>(`/admin/n8n/tools/${id}`, body);
  return data;
}

export async function deleteN8nTool(id: string): Promise<void> {
  await api.delete(`/admin/n8n/tools/${id}`);
}

/** El secreto entero, para pegarlo en n8n. Queda apuntado quién lo vio. */
export async function getN8nToolSecret(id: string): Promise<{ secreto: string; cabecera: string }> {
  const { data } = await api.get<{ secreto: string; cabecera: string }>(`/admin/n8n/tools/${id}/secreto`);
  return data;
}

export async function rotateN8nToolSecret(id: string): Promise<N8nToolOut> {
  const { data } = await api.post<N8nToolOut>(`/admin/n8n/tools/${id}/regenerar-secreto`);
  return data;
}

export async function tryN8nTool(id: string, args: Record<string, unknown>): Promise<ProbarOut> {
  const { data } = await api.post<ProbarOut>(`/admin/n8n/tools/${id}/probar`, { args });
  return data;
}
