import { api } from "@/services/api";
import type { User } from "@/types";

// Primer arranque (instalación en un clic, Fase 10.3b): el CRM arranca sin
// administrador y el dueño lo crea desde el panel con el código de arranque que
// generó Coolify. Los dos endpoints son públicos: `api/primer_arranque.py`.

export interface AltaPrimerArranque {
  codigo: string;
  nombre?: string;
  email: string;
  password: string;
}

export interface RespuestaPrimerArranque {
  access_token: string;
  token_type: string;
  user: User;
  /** La llave de cifrado. Es la única vez que el CRM la enseña. */
  llave_cifrado: string;
}

/** ¿Falta el administrador? El backend solo dice eso: ni el código ni nada más. */
export async function necesitaAdministrador(): Promise<boolean> {
  const { data } = await api.get<{ necesita_admin: boolean }>("/primer-arranque");
  return data.necesita_admin;
}

export async function crearAdministrador(
  alta: AltaPrimerArranque,
): Promise<RespuestaPrimerArranque> {
  const { data } = await api.post<RespuestaPrimerArranque>("/primer-arranque", alta);
  return data;
}
