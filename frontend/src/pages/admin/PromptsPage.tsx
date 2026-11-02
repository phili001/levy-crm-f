// Prompts: el catálogo «Copiar prompt» (Fase 10.2). Guiones para pegar en
// Claude Code, que es quien instala, conecta y actualiza. Salen de
// backend/prompts/ y viajan con la versión instalada, como las novedades.
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { CopyPromptButton, textoParaPegar } from "@/components/CopyPromptButton";
import { PageHeader } from "@/components/PageHeader";
import { getPromptsCatalogo, type PromptCatalogo } from "@/services/admin";

const GRUPOS = [
  { grupo: "principal", titulo: "Los de siempre" },
  { grupo: "avanzado", titulo: "Rutas avanzadas" },
] as const;

export default function PromptsPage() {
  const [prompts, setPrompts] = useState<PromptCatalogo[] | null>(null);
  const [error, setError] = useState("");
  const [abierto, setAbierto] = useState<string | null>(null);

  useEffect(() => {
    getPromptsCatalogo()
      .then(setPrompts)
      .catch(() => setError("No se pudo cargar el catálogo de prompts."));
  }, []);

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title="Prompts"
        eyebrow="Plataforma"
        description="Guiones para pegar en Claude Code, abierto en la carpeta de tu copia del CRM. Al copiarlos ya llevan el dominio de esta instalación."
      />
      <div className="flex-1 overflow-auto p-4 md:p-6 space-y-6">
        {!prompts && !error && (
          <div className="flex items-center gap-2 text-sm text-ink3">
            <Loader2 className="h-4 w-4 animate-spin" /> Cargando…
          </div>
        )}
        {error && <p className="text-sm text-state-bad">{error}</p>}
        {prompts && prompts.length === 0 && (
          <p className="text-sm text-ink3">Esta instalación no trae el catálogo de prompts.</p>
        )}

        {prompts &&
          GRUPOS.map(({ grupo, titulo }) => {
            const delGrupo = prompts.filter((p) => p.grupo === grupo);
            if (delGrupo.length === 0) return null;
            return (
              <section key={grupo} className="max-w-3xl space-y-2">
                <h2 className="eyebrow">{titulo}</h2>
                <ul className="space-y-2">
                  {delGrupo.map((p) => (
                    <li key={p.id} className="rounded-coro border border-line bg-card p-4">
                      <div className="flex items-start gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="font-medium text-ink">{p.titulo}</div>
                          <div className="mt-0.5 text-xs text-ink3">{p.resumen}</div>
                        </div>
                        <CopyPromptButton prompt={p} label="Copiar" className="shrink-0" />
                      </div>
                      <button
                        type="button"
                        className="mt-2 text-xs text-ink3 hover:text-ink"
                        aria-expanded={abierto === p.id}
                        onClick={() => setAbierto(abierto === p.id ? null : p.id)}
                      >
                        {abierto === p.id ? "Ocultar el texto" : "Ver el texto"}
                      </button>
                      {abierto === p.id && (
                        <pre className="mt-2 whitespace-pre-wrap rounded-coro-sm bg-paper2 p-3 font-mono text-xs text-ink2">
                          {textoParaPegar(p)}
                        </pre>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
      </div>
    </div>
  );
}
