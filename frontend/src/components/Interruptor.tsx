// Interruptor de encendido/apagado (1.3.2). Antes era una pastilla con el
// texto «Apagado» que no parecía un botón: Carlos no adivinó que había que
// pulsarla para encender los recordatorios. Ahora se ve la palanca y el
// estado va escrito al lado.
//
// Apagado: riel con borde y bolita pequeña en gris (ink3, que pasa el 3:1 de
// los controles en los dos temas). Encendido: riel del color de la marca y
// bolita en `brand-on`, que Apariencia ya calcula para que contraste con el
// color que elija cada cliente.

interface Props {
  encendido: boolean;
  onCambio: (valor: boolean) => void;
  /** Qué se enciende («Recordatorio de cita»): es el nombre para el lector de pantalla. */
  etiqueta: string;
  /** El estado escrito al lado; «Encendida/Apagada» cuando lo que se enciende es femenino. */
  textos?: { si: string; no: string };
  /** En lugar del estado mientras se guarda («…») o se carga («—»). */
  textoMientras?: string;
  disabled?: boolean;
}

const TEXTOS_POR_DEFECTO = { si: "Encendido", no: "Apagado" };

export function Interruptor({
  encendido,
  onCambio,
  etiqueta,
  textos = TEXTOS_POR_DEFECTO,
  textoMientras,
  disabled,
}: Props) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={encendido}
      aria-label={etiqueta}
      onClick={() => onCambio(!encendido)}
      disabled={disabled}
      className="group shrink-0 inline-flex items-center gap-2 rounded-coro-sm px-1 py-0.5 disabled:opacity-60 disabled:cursor-default focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/30"
    >
      <span
        aria-hidden="true"
        className={"text-xs font-semibold " + (encendido ? "text-brand-ink" : "text-ink2")}
      >
        {textoMientras ?? (encendido ? textos.si : textos.no)}
      </span>
      <span
        aria-hidden="true"
        className={
          "relative inline-flex h-5 w-9 items-center rounded-full border-2 transition-colors " +
          (encendido ? "bg-brand border-brand" : "bg-paper3 border-ink3")
        }
      >
        <span
          className={
            "absolute rounded-full transition-all " +
            (encendido ? "left-[16px] h-3.5 w-3.5 bg-brand-on" : "left-[3px] h-2.5 w-2.5 bg-ink3")
          }
        />
      </span>
    </button>
  );
}
