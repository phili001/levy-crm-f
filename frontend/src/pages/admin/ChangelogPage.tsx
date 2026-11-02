// Novedades: enseña el CHANGELOG.md de la versión instalada.
//
// El markdown se pinta a mano, sin librería: el archivo solo usa títulos,
// listas y negritas, y meter un renderizador entero en el bundle por esto no
// se paga. Nada de HTML crudo — el texto se inserta como texto.
import { useEffect, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { getChangelog } from "@/services/admin";

type Bloque =
  | { tipo: "titulo"; nivel: 1 | 2 | 3; texto: string }
  | { tipo: "lista"; puntos: string[] }
  | { tipo: "parrafo"; texto: string };

function partir(markdown: string): Bloque[] {
  const bloques: Bloque[] = [];
  let lista: string[] = [];
  const cerrarLista = () => {
    if (lista.length) {
      bloques.push({ tipo: "lista", puntos: lista });
      lista = [];
    }
  };
  for (const linea of markdown.split("\n")) {
    const l = linea.trimEnd();
    const titulo = /^(#{1,3})\s+(.*)$/.exec(l);
    if (titulo) {
      cerrarLista();
      bloques.push({
        tipo: "titulo",
        nivel: titulo[1].length as 1 | 2 | 3,
        texto: titulo[2],
      });
      continue;
    }
    if (/^[-*]\s+/.test(l)) {
      lista.push(l.replace(/^[-*]\s+/, ""));
      continue;
    }
    if (!l.trim()) {
      cerrarLista();
      continue;
    }
    cerrarLista();
    bloques.push({ tipo: "parrafo", texto: l });
  }
  cerrarLista();
  return bloques;
}

export default function ChangelogPage() {
  const [contenido, setContenido] = useState("");
  const [version, setVersion] = useState("");
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    getChangelog()
      .then((d) => {
        setContenido(d.contenido);
        setVersion(d.version);
      })
      .catch(() => setError("No se pudieron cargar las novedades."))
      .finally(() => setCargando(false));
  }, []);

  const bloques = partir(contenido);

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title="Novedades"
        eyebrow="Sistema"
        description={
          version ? `Versión instalada: ${version}` : "Qué cambió en cada versión"
        }
      />
      <div className="flex-1 overflow-auto p-4 md:p-6 space-y-6">
        {cargando && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Cargando…
          </div>
        )}

        {!cargando && error && <p className="text-sm text-destructive">{error}</p>}

        {!cargando && !error && !contenido && (
          <p className="text-sm text-muted-foreground">
            Esta instalación no trae el archivo de novedades.
          </p>
        )}

        {!cargando && !error && contenido && (
          <article className="max-w-3xl space-y-4 rounded-coro-sm border bg-card p-6">
            {bloques.map((b, i) => {
              if (b.tipo === "titulo") {
                if (b.nivel === 1) {
                  return (
                    <h2 key={i} className="flex items-center gap-2 text-xl font-semibold">
                      <Sparkles className="h-5 w-5" /> {b.texto}
                    </h2>
                  );
                }
                if (b.nivel === 2) {
                  return (
                    <h3 key={i} className="border-t pt-4 text-lg font-semibold">
                      {b.texto}
                    </h3>
                  );
                }
                return (
                  <h4 key={i} className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                    {b.texto}
                  </h4>
                );
              }
              if (b.tipo === "lista") {
                return (
                  <ul key={i} className="list-disc space-y-1 pl-5 text-sm">
                    {b.puntos.map((p, j) => (
                      <li key={j}>{p}</li>
                    ))}
                  </ul>
                );
              }
              return (
                <p key={i} className="text-sm text-muted-foreground">
                  {b.texto}
                </p>
              );
            })}
          </article>
        )}
      </div>
    </div>
  );
}
