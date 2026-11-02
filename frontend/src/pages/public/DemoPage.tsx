import { useEffect, useState } from "react";

import { useMarca } from "@/store/apariencia";
import { api, apiBaseHttp } from "@/services/api";

// Página pública /demo: una web de prueba con el widget real de la
// instalación, para que Sofía responda en los videos y para que la clínica
// pruebe sin tocar su web. No lleva login: el backend solo entrega la api_key
// del widget si el admin dejó encendida la demo (Conexiones → Chat en tu web,
// `ui.demo_publica`) y `allowed_domains` del canal incluye este mismo dominio.

interface DemoConfig {
  api_key: string;
  api_base: string;
  title: string;
  greeting: string;
}

export default function DemoPage() {
  const { nombre } = useMarca();
  const [estado, setEstado] = useState<"cargando" | "lista" | "apagada">("cargando");

  useEffect(() => {
    let script: HTMLScriptElement | null = null;
    api
      .get<DemoConfig>("/webchat/demo")
      .then(({ data }) => {
        script = document.createElement("script");
        script.src = `${apiBaseHttp()}/widget.js`;
        script.setAttribute("data-api-key", data.api_key);
        script.setAttribute("data-api-base", data.api_base);
        script.setAttribute("data-title", data.title);
        script.setAttribute("data-greeting", data.greeting);
        script.setAttribute("data-subtitle", "Asistente virtual · respuestas automáticas");
        document.body.appendChild(script);
        setEstado("lista");
      })
      .catch(() => setEstado("apagada"));
    return () => {
      if (script) script.remove();
    };
  }, []);

  return (
    <div className="min-h-full bg-paper text-ink">
      <header className="border-b border-line bg-paper2">
        <div className="max-w-3xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="font-semibold">{nombre}</div>
          <div className="text-xs text-ink3">Página de demostración</div>
        </div>
      </header>
      <main className="max-w-3xl mx-auto px-4 py-10 space-y-6">
        <h1 className="text-2xl font-semibold">Así se ve Sofía en tu página web</h1>
        <p className="text-ink2">
          Esta es una web de prueba. Abre el chat de la esquina inferior derecha y pregunta lo
          que preguntaría un paciente: horarios, precios aproximados, cómo agendar. Sofía
          responde con la base de conocimiento de esta instalación.
        </p>
        <ul className="list-disc pl-5 text-ink2 space-y-1">
          <li>«¿Qué horario tienen?»</li>
          <li>«¿Cuánto cuesta una limpieza?»</li>
          <li>«Me duele una muela, ¿me pueden atender hoy?»</li>
        </ul>
        {estado === "apagada" && (
          <div className="text-sm text-state-bad bg-state-bad/10 border border-state-bad/30 rounded-coro-sm px-3 py-2">
            La demo pública está apagada o no hay un canal de chat web activo. Un administrador
            puede encenderla desde Conexiones → Chat en tu web.
          </div>
        )}
        {estado === "cargando" && <div className="text-sm text-ink3">Cargando el chat…</div>}
      </main>
    </div>
  );
}
