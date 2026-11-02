// Canales → Plantillas: crear plantillas de WhatsApp desde el panel (SPEC-18).
// El backend es `backend/app/api/plantillas_whatsapp.py`; el listado sigue en
// `listWhatsappTemplates` (services/admin.ts).
import { api } from "@/services/api";

export type CategoriaPlantilla = "UTILITY" | "MARKETING";
export type TipoBoton = "respuesta" | "enlace" | "telefono";

export interface VariablePlantilla {
  n: number;
  etiqueta: string;
  ejemplo: string;
  campo: string | null;
}

export interface BotonPlantilla {
  tipo: TipoBoton;
  texto: string;
  valor: string;
}

export interface PlantillaIn {
  nombre: string;
  categoria: CategoriaPlantilla;
  idioma: string;
  cabecera: string;
  cuerpo: string;
  variables: VariablePlantilla[];
  pie: string;
  botones: BotonPlantilla[];
}

export interface Sugerida {
  clave: string;
  titulo: string;
  para_que: string;
  plantilla: PlantillaIn;
}

export interface PlantillasInfo {
  proveedor: string;
  puede_crear: boolean;
  idiomas: string[];
  campos: string[];
  sugeridas: Sugerida[];
}

export async function getPlantillasInfo(): Promise<PlantillasInfo> {
  const { data } = await api.get<PlantillasInfo>("/admin/whatsapp/plantillas");
  return data;
}

export async function generarPlantilla(
  descripcion: string,
  categoria: CategoriaPlantilla | null,
  idioma: string,
): Promise<{ plantilla: PlantillaIn; avisos: string[] }> {
  const { data } = await api.post<{ plantilla: PlantillaIn; avisos: string[] }>(
    "/admin/whatsapp/plantillas/generar",
    { descripcion, categoria, idioma },
  );
  return data;
}

export async function crearPlantilla(
  plantilla: PlantillaIn,
): Promise<{ nombre: string; idioma: string; estado: string }> {
  const { data } = await api.post<{ nombre: string; idioma: string; estado: string }>(
    "/admin/whatsapp/plantillas",
    plantilla,
  );
  return data;
}
