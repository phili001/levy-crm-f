import { ExternalLink, Megaphone } from "lucide-react";

import type { Anuncio } from "@/lib/anuncios";

/**
 * El anuncio desde el que escribió el cliente, encima de su primer mensaje:
 * así quien atiende sabe de qué oferta le habla sin preguntárselo. La imagen
 * viene del CDN de Meta (*.fbcdn.net, permitido en la CSP del panel); si no
 * carga, la tarjeta se queda con el texto.
 */
export function AnuncioCard({ anuncio }: { anuncio: Anuncio }) {
  return (
    <div className="max-w-[80%] mb-1 rounded-coro-sm border border-line bg-card overflow-hidden text-xs">
      <div className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-soft text-brand-ink font-medium">
        <Megaphone className="w-3.5 h-3.5 shrink-0" />
        Llegó desde un anuncio
      </div>
      <div className="flex gap-3 p-3">
        {anuncio.imagen && (
          <img
            src={anuncio.imagen}
            alt=""
            loading="lazy"
            className="w-14 h-14 rounded-[4px] object-cover shrink-0 bg-paper2"
            onError={(e) => (e.currentTarget.style.display = "none")}
          />
        )}
        <div className="min-w-0 space-y-1">
          {anuncio.titulo && <div className="font-semibold text-ink">{anuncio.titulo}</div>}
          {anuncio.texto && <div className="text-ink2 line-clamp-3">{anuncio.texto}</div>}
          {anuncio.url && (
            <a
              href={anuncio.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-brand-ink hover:underline"
            >
              Ver anuncio <ExternalLink className="w-3 h-3" />
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
