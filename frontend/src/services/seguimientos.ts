// Asistente → Seguimientos (SPEC-18): los avisos que Sofía manda sola. El
// backend es `backend/app/api/seguimientos.py`.
import { api } from "@/services/api";

export type TipoAviso = "recordatorio" | "inasistencia";

export interface AvisoConfig {
  activo: boolean;
  horas: number;
  plantilla: string | null;
  idioma: string;
  variables: Record<string, string>;
}

export interface RetomarConfig {
  activo: boolean;
  horas: number;
}

export interface SeguimientosConfig {
  recordatorio: AvisoConfig;
  inasistencia: AvisoConfig;
  retomar: RetomarConfig;
  negocio: string;
  hora_desde: number;
  hora_hasta: number;
}

export interface PlantillaAviso {
  name: string;
  language: string;
  status: string | null;
  category: string | null;
  body: string | null;
  variable_names: string[];
  problema: string | null;
  sugerencia: Record<string, string>;
}

export interface Seguimientos {
  config: SeguimientosConfig;
  datos: Array<{ clave: string; etiqueta: string }>;
  plantillas: PlantillaAviso[];
  error_plantillas: string | null;
  estado: Partial<Record<TipoAviso, string | null>>;
  zona: string;
  negocio_sugerido: string;
}

export interface RegistroAviso {
  tipo: string;
  contact_id: string;
  contacto: string | null;
  cuando: string;
  canal: string | null;
  motivo: string | null;
}

export async function getSeguimientos(): Promise<Seguimientos> {
  const { data } = await api.get<Seguimientos>("/admin/seguimientos");
  return data;
}

export async function guardarSeguimientos(config: SeguimientosConfig): Promise<Seguimientos> {
  const { data } = await api.put<Seguimientos>("/admin/seguimientos", config);
  return data;
}

export async function getRegistroSeguimientos(): Promise<RegistroAviso[]> {
  const { data } = await api.get<RegistroAviso[]>("/admin/seguimientos/registro");
  return data;
}
