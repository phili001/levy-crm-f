import { api } from "@/services/api";

// Datos del aviso de privacidad y de los términos (Fase 7). Ver
// backend/app/api/legal.py. La lectura es pública: las páginas /privacidad y
// /terminos no llevan login.

export interface Legal {
  nombre: string | null;
  responsable: string;
  domicilio: string;
  correo: string;
  telefono: string;
  actualizado: string;
  completo: boolean;
}

export type LegalIn = Pick<Legal, "responsable" | "domicilio" | "correo" | "telefono">;

export async function getLegal(): Promise<Legal> {
  const { data } = await api.get<Legal>("/legal");
  return data;
}

export async function updateLegal(body: LegalIn): Promise<Legal> {
  const { data } = await api.put<Legal>("/legal", { ...body, correo: body.correo || null });
  return data;
}
