import { api } from "@/services/api";

// Buzón de correo por IMAP/SMTP (Fase 5). Ver backend/app/api/correo_imap.py.

export interface ServidorCorreo {
  host: string;
  port: number;
  seguridad: "ssl" | "starttls";
}

export interface PresetCorreo {
  id: string;
  nombre: string;
  imap: ServidorCorreo;
  smtp: ServidorCorreo;
  ayuda: string;
}

export interface BuzonIn {
  preset: string; // id de preset o "otro"
  usuario: string;
  // Vacía al editar = se usa la guardada (solo con el mismo buzón y servidores).
  password?: string;
  imap?: ServidorCorreo;
  smtp?: ServidorCorreo;
  carpeta: string;
}

export interface ConectarBuzonIn extends BuzonIn {
  auto_send: boolean;
  no_responder: string[];
}

export interface PruebaBuzon {
  imap_ok: boolean;
  smtp_ok: boolean;
  errores: string[];
  carpeta_enviados: string | null;
}

const BASE = "/admin/channels/email/imap";

export async function listPresetsCorreo(): Promise<PresetCorreo[]> {
  const { data } = await api.get<PresetCorreo[]>(`${BASE}/presets`);
  return data;
}

export async function probarBuzon(body: BuzonIn): Promise<PruebaBuzon> {
  const { data } = await api.post<PruebaBuzon>(`${BASE}/probar`, body);
  return data;
}

export async function conectarBuzon(body: ConectarBuzonIn): Promise<{ channel_id: string }> {
  const { data } = await api.post<{ channel_id: string }>(BASE, body);
  return data;
}
