import { create } from "zustand";

import { getFeatureFlags } from "@/services/admin";

// Banderas de módulos (tabla feature_flags del backend). Se cargan una vez por
// sesión de admin y sirven para esconder rutas y entradas del menú de los
// módulos que el curso no enseña. Hasta que llegan, `loaded` es false y los
// guardias no redirigen: mejor un parpadeo que mandar al admin a Inicio por
// una carrera con la red.

interface FlagsState {
  flags: Record<string, boolean>;
  loaded: boolean;
  load: () => Promise<void>;
  isOn: (clave: string) => boolean;
  setLocal: (flags: Record<string, boolean>) => void;
}

export const useFlags = create<FlagsState>((set, get) => ({
  flags: {},
  loaded: false,
  load: async () => {
    try {
      const list = await getFeatureFlags();
      set({ flags: Object.fromEntries(list.map((f) => [f.clave, f.activa])), loaded: true });
    } catch {
      // Sin respuesta (o sin permiso) todo queda apagado, que es el default.
      set({ loaded: true });
    }
  },
  isOn: (clave) => get().flags[clave] === true,
  setLocal: (flags) => set({ flags: { ...get().flags, ...flags } }),
}));

// Ruta del panel → bandera que la enciende. Lo usan el menú y el router.
export const FLAG_DE_RUTA: Record<string, string> = {
  "/admin/outbound": "envio_masivo",
  "/admin/agent/flow": "flow_dashboard",
  "/admin/internal-agent/prompt": "agente_interno",
};
