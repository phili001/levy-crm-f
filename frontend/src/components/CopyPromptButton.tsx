import { useState } from "react";
import clsx from "clsx";
import { Check, ClipboardCopy } from "lucide-react";

import { getPromptsCatalogo, type PromptCatalogo } from "@/services/admin";

/** El prompt listo para pegar: con el dominio de ESTA instalación puesto. */
export function textoParaPegar(p: PromptCatalogo): string {
  return p.texto.replaceAll("<dominio>", window.location.hostname);
}

interface Props {
  /** El prompt ya cargado (página Prompts)… */
  prompt?: PromptCatalogo;
  /** …o el de una tarjeta de Canales, por su `canal` en el catálogo. */
  canal?: string;
  label?: string;
  className?: string;
}

/**
 * «Copiar prompt» (Fase 10.2): copia el guion para pegarlo en Claude Code, que
 * es quien instala y conecta. Pide el catálogo solo al pulsar, y una vez por
 * sesión aunque haya un botón en cada tarjeta.
 */
export function CopyPromptButton({ prompt, canal, label = "Copiar prompt", className }: Props) {
  const [estado, setEstado] = useState<"listo" | "copiado" | "error">("listo");

  async function copiar() {
    try {
      const p = prompt ?? (await getPromptsCatalogo()).find((x) => x.canal === canal);
      if (!p) throw new Error("sin prompt");
      await navigator.clipboard.writeText(textoParaPegar(p));
      setEstado("copiado");
    } catch {
      setEstado("error");
    }
    window.setTimeout(() => setEstado("listo"), 2500);
  }

  return (
    <button
      type="button"
      onClick={() => void copiar()}
      className={clsx("btn-ghost btn-sm", className)}
      title="Copia el guion para pegarlo en Claude Code"
    >
      {estado === "copiado" ? <Check className="text-state-ok" /> : <ClipboardCopy />}
      {estado === "copiado" ? "Copiado" : estado === "error" ? "No se pudo copiar" : label}
    </button>
  );
}
