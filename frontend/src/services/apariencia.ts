import { api } from "@/services/api";
import type { Apariencia } from "@/lib/apariencia";

export type AparienciaEditable = Pick<
  Apariencia,
  "nombre" | "paleta" | "color_marca" | "tipografia" | "trazo_iconos"
>;

export async function guardarApariencia(body: AparienciaEditable): Promise<Apariencia> {
  const { data } = await api.put<Apariencia>("/apariencia", body);
  return data;
}

export async function restablecerApariencia(): Promise<Apariencia> {
  const { data } = await api.delete<Apariencia>("/apariencia");
  return data;
}

export async function subirLogo(fichero: File): Promise<Apariencia> {
  const form = new FormData();
  form.append("fichero", fichero);
  const { data } = await api.put<Apariencia>("/apariencia/logo", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export async function quitarLogo(): Promise<Apariencia> {
  const { data } = await api.delete<Apariencia>("/apariencia/logo");
  return data;
}
