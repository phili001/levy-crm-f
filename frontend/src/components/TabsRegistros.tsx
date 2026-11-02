/**
 * Pestañas de Registros.
 *
 * "Logs en vivo" y "Audit log" eran dos entradas distintas del menú para la
 * misma pregunta: qué ha pasado aquí. Una cuenta lo que hizo la máquina y la
 * otra lo que hicieron las personas, y quien busca un problema no sabe de
 * antemano en cuál mirar — así que acababa abriendo las dos.
 *
 * Siguen siendo dos rutas (cada página tiene su propia carga, su refresco y
 * sus acciones, y unificarlas en un solo componente no aportaba nada). Lo que
 * se unifica es cómo se llega: una entrada en el menú y estas pestañas.
 */
import { NavLink } from "react-router-dom";
import clsx from "clsx";
import { Activity, ShieldCheck } from "lucide-react";

const PESTANAS = [
  { to: "/admin/system/logs", label: "Actividad", Icon: Activity },
  { to: "/admin/audit", label: "Auditoría", Icon: ShieldCheck },
];

export function TabsRegistros() {
  return (
    <div className="flex items-center gap-1 px-4 md:px-6 pt-3 border-b border-line">
      {PESTANAS.map(({ to, label, Icon }) => (
        <NavLink
          key={to}
          to={to}
          className={({ isActive }) =>
            clsx(
              "inline-flex items-center gap-2 rounded-t-coro-sm px-3.5 py-2 text-sm font-medium",
              "border-b-2 -mb-px transition-colors duration-150",
              isActive
                ? "border-brand-ink text-ink"
                : "border-transparent text-ink3 hover:text-ink2 hover:bg-paper2"
            )
          }
        >
          <Icon className="w-4 h-4" />
          {label}
        </NavLink>
      ))}
    </div>
  );
}
