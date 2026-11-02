// La plantilla como le llega al paciente: cabecera, mensaje con los ejemplos
// en lugar de las variables, pie y los botones debajo, como los pinta WhatsApp.
// Con los colores del panel y no con los de WhatsApp (marca blanca, ver R1).

import { Fragment } from "react";
import { ExternalLink, Phone, Reply } from "lucide-react";
import type { PlantillaIn } from "@/services/plantillas";

const PARTES_RE = /(\{\{\s*\d+\s*\}\})/g;

export function VistaPrevia({ p }: { p: PlantillaIn }) {
  const partes = p.cuerpo.split(PARTES_RE);
  return (
    <div className="rounded-coro-sm bg-paper2 p-3 space-y-1">
      <div className="rounded-coro-sm bg-card border border-line px-3 py-2">
        {p.cabecera.trim() && <div className="text-sm font-semibold text-ink mb-1">{p.cabecera}</div>}
        <div className="text-sm text-ink2 whitespace-pre-wrap break-words leading-relaxed">
          {p.cuerpo.trim() ? (
            partes.map((parte, i) => {
              const m = parte.match(/^\{\{\s*(\d+)\s*\}\}$/);
              if (!m) return <Fragment key={i}>{parte}</Fragment>;
              const ejemplo = p.variables.find((v) => v.n === Number(m[1]))?.ejemplo.trim();
              return ejemplo ? (
                <span key={i} className="font-medium text-ink">{ejemplo}</span>
              ) : (
                <span key={i} className="font-medium text-state-warn">{parte}</span>
              );
            })
          ) : (
            <span className="text-ink3">El mensaje aparece aquí.</span>
          )}
        </div>
        {p.pie.trim() && <div className="text-[11px] text-ink3 mt-1">{p.pie}</div>}
        <div className="text-[10px] text-ink4 text-right mt-0.5">10:30</div>
      </div>
      {p.botones.map((b, i) => (
        <div
          key={i}
          className="rounded-coro-sm bg-card border border-line py-1.5 text-center text-sm text-brand-ink flex items-center justify-center gap-1.5"
        >
          {b.tipo === "enlace" ? (
            <ExternalLink className="w-3.5 h-3.5" />
          ) : b.tipo === "telefono" ? (
            <Phone className="w-3.5 h-3.5" />
          ) : (
            <Reply className="w-3.5 h-3.5" />
          )}
          {b.texto || "Botón"}
        </div>
      ))}
    </div>
  );
}
