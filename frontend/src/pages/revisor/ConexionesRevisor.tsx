import { useCallback, useEffect, useRef, useState } from "react";
import { Instagram, RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import {
  getInstagramProfile,
  getInstagramRevisorProfile,
  startInstagramOAuth,
  type InstagramProfile,
} from "@/services/admin";
import { errorDetail } from "@/lib/errors";

/**
 * Conexiones, versión del revisor de Meta.
 *
 * Meta exige dos cosas para aprobar los permisos: que el revisor pueda hacer el
 * Business Login ENTERO con su propia cuenta (con su pantalla de permisos) y
 * que la app le ENSEÑE el dato que obtiene con ese permiso (@usuario, nombre y
 * foto). La pantalla real (/admin/connections) no vale para eso: además de
 * Instagram lleva WhatsApp, Google, Retell, el correo saliente, los tokens de
 * API y todas las claves en claro, y ninguna de esas es cosa suya.
 *
 * Así que el revisor tiene esta, con dos tarjetas y un solo botón:
 *
 *  1. La suya — conecta su cuenta y ve lo que la app obtiene de ella.
 *  2. La del negocio — de solo lectura, la cuenta cuyos DM se contestan.
 *
 * Que su conexión no eche a la de casa NO es cosa de esta pantalla: su token se
 * guarda en una fila aparte (backend/app/services/instagram_oauth.py,
 * REVISOR_NAME) y el runtime solo mira la de casa. Aquí no se pinta ningún dato
 * que no sea público en el propio Instagram.
 */
export default function ConexionesRevisor() {
  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title="Conexiones"
        description="Conecta tu cuenta de Instagram y comprueba qué cuenta usa esta app."
      />
      <div className="flex-1 p-4 md:p-6 max-w-2xl space-y-4 overflow-auto">
        <TarjetaMiCuenta />
        <TarjetaCuentaDelNegocio />
      </div>
    </div>
  );
}

/**
 * La cuenta del propio revisor: botón de Business Login y, al volver, lo que la
 * app obtiene de ella.
 *
 * El OAuth se abre en una ventana aparte y termina en el callback del backend,
 * que la cierra sola: esta pestaña no se entera de nada. Por eso, mientras la
 * ventana está abierta, se pregunta cada pocos segundos si ya hay cuenta
 * conectada — que es también lo que hace que en el vídeo la ficha aparezca sin
 * tocar nada.
 */
function TarjetaMiCuenta() {
  const [perfil, setPerfil] = useState<InstagramProfile | null>(null);
  const [cargando, setCargando] = useState(true);
  const [conectando, setConectando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sondeo = useRef<number | null>(null);

  const cargar = useCallback(async (refrescar = false) => {
    const r = await getInstagramRevisorProfile(refrescar);
    setPerfil(r);
    return r;
  }, []);

  useEffect(() => {
    let vivo = true;
    cargar()
      .catch(() => undefined)
      .finally(() => {
        if (vivo) setCargando(false);
      });
    return () => {
      vivo = false;
      if (sondeo.current) window.clearInterval(sondeo.current);
    };
  }, [cargar]);

  async function conectar() {
    setError(null);
    setConectando(true);
    try {
      const url = await startInstagramOAuth();
      window.open(url, "_blank", "noopener,noreferrer,width=600,height=720");
      // Hasta 3 minutos de sondeo: lo que tarda alguien en entrar en Instagram
      // y aceptar los permisos. Se corta solo en cuanto hay cuenta.
      let intentos = 0;
      if (sondeo.current) window.clearInterval(sondeo.current);
      sondeo.current = window.setInterval(() => {
        intentos += 1;
        void cargar(true)
          .then((r) => {
            if (r.connected || intentos >= 60) {
              if (sondeo.current) window.clearInterval(sondeo.current);
              sondeo.current = null;
              setConectando(false);
            }
          })
          .catch(() => undefined);
      }, 3000);
    } catch (e: unknown) {
      setConectando(false);
      setError(errorDetail(e, "No se pudo iniciar la conexión con Instagram"));
    }
  }

  return (
    <div className="rounded-coro border border-line bg-paper2 p-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Instagram className="w-4 h-4 text-ink3" />
          <div className="text-[13px] font-medium text-ink">Tu cuenta de Instagram</div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void cargar(true)}
            className="btn-ghost text-xs inline-flex items-center gap-1.5"
            title="Volver a pedir los datos de la cuenta"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Actualizar
          </button>
          <button
            type="button"
            onClick={() => void conectar()}
            disabled={conectando}
            className={`${perfil?.connected ? "btn-ghost" : "btn-primary"} text-xs inline-flex items-center gap-1.5`}
          >
            <Instagram className="w-3.5 h-3.5" />
            {perfil?.connected ? "Volver a conectar" : "Conectar con Instagram"}
          </button>
        </div>
      </div>

      {cargando ? (
        <div className="text-[12px] text-ink3">Cargando…</div>
      ) : perfil?.connected ? (
        <FichaCuenta perfil={perfil} pie="Cuenta que has conectado tú" />
      ) : (
        <div className="text-[12px] text-ink3">
          Todavía no has conectado ninguna cuenta. Da clic en «Conectar con Instagram»: se abre el
          Business Login de Instagram, aceptas los permisos y aquí aparecerán tu usuario, tu
          nombre y tu foto.
        </div>
      )}

      {conectando && (
        <div className="text-[11px] text-ink3">
          Esperando a que termines en la ventana de Instagram…
        </div>
      )}
      {error && <div className="text-[11px] text-red-600">{error}</div>}
    </div>
  );
}

/**
 * La cuenta del negocio: la que recibe los DM que se leen y se contestan en la
 * bandeja. De solo lectura — conectarla o cambiarla es cosa del administrador.
 */
function TarjetaCuentaDelNegocio() {
  const [perfil, setPerfil] = useState<InstagramProfile | null>(null);
  const [cargando, setCargando] = useState(true);

  useEffect(() => {
    let vivo = true;
    getInstagramProfile()
      .then((r) => {
        if (vivo) setPerfil(r);
      })
      .catch(() => undefined)
      .finally(() => {
        if (vivo) setCargando(false);
      });
    return () => {
      vivo = false;
    };
  }, []);

  return (
    <div className="rounded-coro border border-line bg-paper2 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Instagram className="w-4 h-4 text-ink3" />
        <div className="text-[13px] font-medium text-ink">Cuenta de la empresa</div>
      </div>

      {cargando ? (
        <div className="text-[12px] text-ink3">Cargando…</div>
      ) : !perfil?.connected ? (
        <div className="text-[12px] text-ink3">No hay ninguna cuenta de Instagram conectada.</div>
      ) : (
        <FichaCuenta perfil={perfil} pie="Cuenta de Instagram conectada" />
      )}

      <div className="text-[11px] text-ink3">
        Los mensajes directos de esta cuenta son los que se leen y se contestan desde la bandeja.
      </div>
    </div>
  );
}

/**
 * La foto y el @usuario salen del CDN de Meta con una URL que caduca, por eso
 * se piden al abrir la pantalla en vez de guardarse. Si la foto no carga queda
 * el icono.
 */
function FichaCuenta({ perfil, pie }: { perfil: InstagramProfile; pie: string }) {
  const [fotoRota, setFotoRota] = useState(false);
  const arroba = perfil.username ? `@${perfil.username}` : "Cuenta conectada";
  const foto = perfil.profile_picture_url && !fotoRota ? perfil.profile_picture_url : null;

  return (
    <div className="flex items-center gap-2.5 rounded-coro-sm border border-line2 bg-paper px-2.5 py-2">
      {foto ? (
        <img
          src={foto}
          alt={arroba}
          className="w-10 h-10 rounded-full object-cover shrink-0"
          onError={() => setFotoRota(true)}
        />
      ) : (
        <div className="w-10 h-10 rounded-full bg-paper3 flex items-center justify-center shrink-0">
          <Instagram className="w-4 h-4 text-ink3" />
        </div>
      )}
      <div className="min-w-0">
        <div className="text-[13px] font-medium text-ink truncate">{arroba}</div>
        <div className="text-[11px] text-ink3 truncate">
          {perfil.name ? `${perfil.name} · ` : ""}
          {pie}
        </div>
      </div>
    </div>
  );
}
