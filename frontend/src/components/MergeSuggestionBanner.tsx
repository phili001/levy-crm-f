import { useEffect, useState } from "react";
import clsx from "clsx";
import { UsersRound } from "lucide-react";

import { ConfirmModal } from "@/components/ConfirmModal";
import { errorDetail } from "@/lib/errors";
import { maskEmail, maskHandle, maskName, maskPhone } from "@/lib/mask";
import {
  descartarPropuestaFusion,
  getPropuestasFusion,
  mergeContacts,
} from "@/services/contacts";
import { useAuth } from "@/store/auth";
import { usePrivacy } from "@/store/privacy";
import type { FichaDePropuesta, PropuestaFusion } from "@/types";

interface Props {
  contactId: string | undefined;
  // Súbelo para volver a pedir las propuestas (evento `fusion.changed`).
  version?: number;
  // Tras unir: `queda` es la ficha que se quedó con todo; `absorbida`, la que
  // desapareció (si era la que estaba abierta, hay que salir de ella).
  onUnidas?: (queda: string, absorbida: string) => void;
  // "franja" bajo la cabecera del chat; "tarjeta" en la ficha del contacto.
  como?: "franja" | "tarjeta";
}

/**
 * «¿Es el mismo paciente?» (Fase 10.1). Sale cuando alguien dio por Instagram,
 * Messenger, el chat web o el correo un teléfono (o escribe desde un correo)
 * que ya es de otra ficha. El CRM nunca las une solo: un teléfono escrito en
 * un chat no está verificado. Unir o descartar es de un administrador; el
 * resto lo ve para avisarle.
 */
export function MergeSuggestionBanner({ contactId, version = 0, onUnidas, como = "franja" }: Props) {
  const priv = usePrivacy((s) => s.enabled);
  const isAdmin = useAuth((s) => s.user?.role) === "admin";
  const [propuestas, setPropuestas] = useState<PropuestaFusion[]>([]);
  const [recarga, setRecarga] = useState(0);
  const [confirmando, setConfirmando] = useState<PropuestaFusion | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPropuestas([]);
    setError(null);
    if (!contactId) return;
    let vigente = true;
    getPropuestasFusion(contactId)
      .then((ps) => vigente && setPropuestas(ps))
      .catch(() => undefined);
    return () => {
      vigente = false;
    };
  }, [contactId, version, recarga]);

  if (!contactId || propuestas.length === 0) return null;

  const nombreDe = (f: FichaDePropuesta) =>
    maskName(f.nombre, priv) ||
    (f.social_handle ? maskHandle(`@${f.social_handle.replace(/^@/, "")}`, priv) : null) ||
    maskPhone(f.telefono, priv);

  const descartar = async (p: PropuestaFusion) => {
    setOcupado(true);
    setError(null);
    try {
      await descartarPropuestaFusion(p.id);
      setRecarga((v) => v + 1);
    } catch (e) {
      setError(errorDetail(e, "No se pudo descartar la propuesta."));
    } finally {
      setOcupado(false);
    }
  };

  const unir = async (p: PropuestaFusion) => {
    setOcupado(true);
    setError(null);
    try {
      await mergeContacts(p.queda.id, p.absorbe.id, p.id);
      setConfirmando(null);
      onUnidas?.(p.queda.id, p.absorbe.id);
      setRecarga((v) => v + 1);
    } catch (e) {
      setConfirmando(null);
      setError(errorDetail(e, "No se pudieron unir las fichas."));
    } finally {
      setOcupado(false);
    }
  };

  return (
    <>
      {propuestas.map((p) => {
        const soyLaQueSeVa = p.absorbe.id === contactId;
        const otra = soyLaQueSeVa ? p.queda : p.absorbe;
        const quien = `«${nombreDe(otra)}» (${otra.canal})`;
        const dato = p.motivo === "telefono" ? maskPhone(p.dato, priv) : maskEmail(p.dato, priv);
        const queDijo =
          p.motivo === "telefono"
            ? soyLaQueSeVa
              ? `Dio el teléfono ${dato}, que ya es de ${quien}.`
              : `${quien} dio el teléfono ${dato}, el de esta ficha.`
            : soyLaQueSeVa
              ? `Escribe desde ${dato}, el correo de ${quien}.`
              : `${quien} escribe desde ${dato}, el correo de esta ficha.`;
        const conTelefono = p.telefono_final.startsWith("+")
          ? ` con el ${maskPhone(p.telefono_final, priv)}`
          : "";
        const conCitas =
          p.citas_activas > 0
            ? ` y ${p.citas_activas === 1 ? "su cita" : `sus ${p.citas_activas} citas`} por venir`
            : "";
        return (
          <div
            key={p.id}
            className={clsx(
              "flex flex-wrap items-start gap-x-3 gap-y-2 text-[12px] text-ink",
              como === "franja"
                ? "px-4 py-2 bg-brand-soft border-b border-line"
                : "rounded-coro-sm border border-line bg-brand-soft p-3",
            )}
          >
            <UsersRound className="w-4 h-4 mt-0.5 shrink-0 text-brand-ink" aria-hidden="true" />
            <div className="min-w-0 flex-1 space-y-0.5">
              <div className="font-semibold text-brand-ink">¿Es el mismo paciente?</div>
              <div>{queDijo}</div>
              {p.bloqueo ? (
                <div className="text-state-warn">{p.bloqueo}</div>
              ) : (
                <div className="text-ink2">
                  Al unirlas queda una sola ficha{conTelefono}
                  {conCitas}, con las conversaciones y notas de las dos.
                </div>
              )}
              {!isAdmin && (
                <div className="text-ink3">Un administrador puede unirlas o descartarlo desde aquí.</div>
              )}
              {error && <div className="text-state-bad">{error}</div>}
            </div>
            {isAdmin && (
              <div className="flex items-center gap-1 shrink-0">
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  disabled={ocupado}
                  onClick={() => void descartar(p)}
                  title="No es la misma persona: no se vuelve a proponer"
                >
                  No es la misma
                </button>
                {!p.bloqueo && (
                  <button
                    type="button"
                    className="btn-primary btn-sm"
                    disabled={ocupado}
                    onClick={() => setConfirmando(p)}
                  >
                    Unir fichas
                  </button>
                )}
              </div>
            )}
          </div>
        );
      })}
      <ConfirmModal
        open={confirmando !== null}
        title="¿Unir las dos fichas?"
        description={
          confirmando
            ? `Queda una sola ficha, la de ${nombreDe(confirmando.queda)}, con las conversaciones, notas y citas de las dos. No se puede deshacer.`
            : undefined
        }
        confirmLabel="Unir fichas"
        busy={ocupado}
        onConfirm={() => confirmando && void unir(confirmando)}
        onCancel={() => setConfirmando(null)}
      />
    </>
  );
}
