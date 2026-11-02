import { CalendarClock, Contact, Library, MessagesSquare, Send, Sparkles } from "lucide-react";

import { BrandLogo } from "@/components/BrandLogo";
import { appNameParts } from "@/lib/appName";

/**
 * Muestra de las piezas del panel que llevan la marca. No es una maqueta
 * pintada aparte: usa las mismas clases y variables que el resto del panel, y
 * la página aplica la apariencia en edición a TODO el documento mientras se
 * edita. Lo que se ve aquí es lo que se verá en el panel, en claro y en
 * oscuro (el botón de tema del menú cambia también la vista previa).
 */
export function VistaPrevia({ nombre }: { nombre: string }) {
  const partes = appNameParts(nombre);
  return (
    <div className="card overflow-hidden">
      <div className="px-4 py-2 border-b border-line bg-paper2 text-[11px] uppercase tracking-wider text-ink3 font-medium">
        Vista previa
      </div>

      <div className="grid grid-cols-[120px_1fr] min-h-[300px]">
        {/* Menú */}
        <div className="border-r border-line bg-paper p-3 space-y-3">
          <div className="flex items-center gap-2 min-w-0">
            <BrandLogo className="w-6 h-6 shrink-0 text-ink" />
            <div className="min-w-0 leading-none">
              <div className="font-display font-extrabold text-ink text-sm truncate">{partes.main}</div>
              {partes.sub && (
                <div className="text-[8px] uppercase tracking-[0.14em] text-ink3 mt-0.5 truncate">
                  {partes.sub}
                </div>
              )}
            </div>
          </div>
          <nav className="space-y-0.5 text-xs">
            <div className="flex items-center gap-1.5 rounded-coro-sm px-2 py-1.5 bg-brand-soft text-brand-ink font-medium">
              <MessagesSquare className="w-3.5 h-3.5 shrink-0" /> Chats
            </div>
            <div className="flex items-center gap-1.5 rounded-coro-sm px-2 py-1.5 text-ink2">
              <Contact className="w-3.5 h-3.5 shrink-0" /> Clientes
            </div>
            <div className="flex items-center gap-1.5 rounded-coro-sm px-2 py-1.5 text-ink2 min-w-0">
              <Library className="w-3.5 h-3.5 shrink-0" /> <span className="truncate">Conocimiento</span>
            </div>
          </nav>
        </div>

        {/* Contenido */}
        <div className="p-4 space-y-4 min-w-0">
          <div className="flex gap-4 border-b border-line text-xs">
            <span className="border-b-2 border-brand-ink text-ink font-medium pb-1.5">Abiertas</span>
            <span className="border-b-2 border-transparent text-ink3 pb-1.5">Cerradas</span>
          </div>

          <div className="lienzo-chat rounded-coro-sm p-3 space-y-2">
            <div className="max-w-[80%] rounded-coro-sm bg-card border border-line px-3 py-2 text-xs text-ink">
              Hola, ¿tienen cita para limpieza esta semana?
            </div>
            <div className="max-w-[80%] ml-auto rounded-coro-sm bg-brand text-brand-on px-3 py-2 text-xs">
              Claro, el jueves a las 10:00 está libre. ¿Te la aparto?
            </div>
          </div>

          <p className="text-sm text-ink2">
            Tus canales, <span className="accent">una sola voz</span>.
          </p>

          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="btn-primary btn-sm">
              <Send /> Enviar
            </button>
            <button type="button" className="btn btn-sm">
              <CalendarClock /> Agendar
            </button>
            <span className="pill bg-brand-soft text-brand-ink text-[11px]">
              <Sparkles className="w-3 h-3" /> Sugerencia
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
