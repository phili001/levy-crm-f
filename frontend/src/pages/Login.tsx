import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Sun, Moon } from "lucide-react";
import { useAuth } from "@/store/auth";
import { useTheme } from "@/store/theme";
import { apiBaseHttp } from "@/services/api";
import { necesitaAdministrador } from "@/services/primerArranque";
import { useMarca } from "@/store/apariencia";
import { CANAL_META, ChannelIcon, type CanalId } from "@/components/ChannelIcon";
import { BrandLogo } from "@/components/BrandLogo";

// Mensajes para los errores que el backend devuelve en el fragmento al volver
// del login con Google (#google_error=...).
const GOOGLE_ERRORS: Record<string, string> = {
  google_no_user:
    "Tu cuenta de Google no está dada de alta en el panel. Pide a un administrador que cree tu usuario con ese correo.",
  google_unverified: "Tu correo de Google no está verificado.",
  google_not_configured: "El acceso con Google todavía no está configurado.",
  google_denied: "Se canceló el acceso con Google.",
  google_state: "La sesión de Google caducó. Inténtalo de nuevo.",
  google_exchange: "No se pudo validar tu cuenta de Google. Inténtalo de nuevo.",
  google_bad_request: "No se pudo completar el acceso con Google.",
};

// Los canales, flotando de fondo. Antes eran burbujas con frases de ejemplo:
// bonitas, pero no decían QUÉ es esto. Los logos sí — quien entra ve de un
// golpe que WhatsApp, Instagram, Messenger, correo, teléfono y web caen en la
// misma bandeja, que es exactamente lo que significa "omnicanal".
//
// Delays NEGATIVOS: cada marca arranca ya a mitad de recorrido, así el fondo
// se ve en movimiento desde el primer frame y no hay un arranque vacío.
const FLOATING_CHANNELS: Array<{
  canal: CanalId;
  x: string;
  size: number;
  delay: number;
  duration: number;
}> = [
  { canal: "whatsapp", x: "7%", size: 46, delay: -2, duration: 24 },
  { canal: "instagram_dm", x: "19%", size: 34, delay: -13, duration: 28 },
  { canal: "email", x: "80%", size: 38, delay: -18, duration: 26 },
  { canal: "messenger", x: "70%", size: 44, delay: -8, duration: 30 },
  { canal: "retell_voice", x: "89%", size: 32, delay: -24, duration: 32 },
  { canal: "webchat", x: "12%", size: 30, delay: -15, duration: 27 },
];

// Cuánto sigue preguntando el login si la API aún no contesta: el primer
// arranque (migraciones y sembrado) tarda de 2 a 5 minutos en un VPS modesto.
const ESPERA_PRIMER_ARRANQUE_MS = 6 * 60_000;

// Destino tras entrar: si venimos de una sesión caducada (?from=/ruta), volvemos
// ahí; si no, al home por rol. Solo aceptamos rutas internas (empiezan por "/").
function postLoginTarget(role: string | undefined): string {
  const from = new URLSearchParams(window.location.search).get("from");
  if (from && from.startsWith("/") && !from.startsWith("//")) return from;
  return role === "admin" ? "/admin" : "/inbox";
}

export default function Login() {
  const { nombre } = useMarca();
  const navigate = useNavigate();
  const login = useAuth((s) => s.login);
  const loginWithToken = useAuth((s) => s.loginWithToken);
  const isLoading = useAuth((s) => s.isLoading);
  const theme = useTheme((s) => s.theme);
  const toggleTheme = useTheme((s) => s.toggle);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Vuelta del login con Google: el backend redirige a /login con el token (o un
  // error) en el fragmento (#...). Lo procesamos y limpiamos la URL.
  useEffect(() => {
    const hash = window.location.hash;
    if (hash.length < 2) return;
    const params = new URLSearchParams(hash.slice(1));
    const token = params.get("token");
    const gerror = params.get("google_error");
    if (!token && !gerror) return;
    // Quita el hash para no dejar el token en la barra de direcciones.
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    if (token) {
      loginWithToken(token)
        .then(() => {
          const user = useAuth.getState().user;
          navigate(postLoginTarget(user?.role));
        })
        .catch(() => setError("No se pudo completar el acceso con Google."));
    } else if (gerror) {
      setError(GOOGLE_ERRORS[gerror] || "No se pudo iniciar sesión con Google.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Instalación en un clic recién desplegada: aún no hay nadie que pueda
  // entrar, así que se manda a crear el administrador. Justo después del Deploy
  // la API pasa un par de minutos migrando la base y no contesta: se avisa y se
  // vuelve a preguntar, en vez de dejar un login con el que no entra nadie (le
  // pasó al ensayo de crm2). Una versión sin primer arranque contesta 404, y
  // ahí el login sigue como siempre.
  const [arrancando, setArrancando] = useState(false);
  useEffect(() => {
    let vigente = true;
    let reintento: number | undefined;
    const hasta = Date.now() + ESPERA_PRIMER_ARRANQUE_MS;
    const preguntar = () => {
      necesitaAdministrador()
        .then((falta) => {
          if (!vigente) return;
          setArrancando(false);
          if (falta) navigate("/primer-arranque", { replace: true });
        })
        .catch((err: unknown) => {
          if (!vigente) return;
          const estado = (err as { response?: { status?: number } } | null)?.response?.status;
          const noResponde = estado === undefined || estado === 502 || estado === 503 || estado === 504;
          if (noResponde && Date.now() < hasta) {
            setArrancando(true);
            reintento = window.setTimeout(preguntar, 4000);
          } else {
            setArrancando(false);
          }
        });
    };
    preguntar();
    return () => {
      vigente = false;
      window.clearTimeout(reintento);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await login(email, password);
      const user = useAuth.getState().user;
      navigate(postLoginTarget(user?.role));
    } catch (err: unknown) {
      // OJO: no todo fallo del login es una contraseña mal puesta. Si el panel
      // no llega al backend (dominio mal configurado, CORS, backend caído) axios
      // lanza un error SIN `response`, y decir ahí "Credenciales incorrectas" es
      // mentir: manda a quien instala a probar contraseñas durante media hora
      // cuando lo que falla es el despliegue. Solo se habla de credenciales
      // cuando el backend ha contestado de verdad.
      const res =
        err && typeof err === "object" && "response" in err
          ? (err as { response?: { data?: { detail?: string } } }).response
          : undefined;
      if (!res) {
        setError(
          "No se ha podido conectar con el servidor. Comprueba que la API responde " +
            `(${apiBaseHttp()}/health) y que ese dominio acepta llamadas desde este panel.`,
        );
        return;
      }
      setError(res.data?.detail || "Credenciales incorrectas");
    }
  }

  return (
    <div className="min-h-full flex items-center justify-center bg-paper p-4 sm:p-6 relative overflow-hidden">
      {/* Keyframes inline para no tocar el CSS global. */}
      <style>{`
        @keyframes coro-float-up {
          0%   { transform: translateY(20vh); opacity: 0; }
          12%  { opacity: 1; }
          88%  { opacity: 1; }
          100% { transform: translateY(-70vh); opacity: 0; }
        }
      `}</style>

      {/* Degradado suave en las esquinas */}
      <div
        className="absolute inset-0 pointer-events-none"
        aria-hidden="true"
        style={{
          background:
            "radial-gradient(680px circle at 12% 18%, rgb(var(--brand-rgb) / 0.10), transparent 62%), radial-gradient(560px circle at 88% 82%, rgb(var(--brand-rgb) / 0.07), transparent 62%)",
        }}
      />

      {/* Toggle claro/oscuro — arriba derecha, solo icono */}
      <button
        type="button"
        onClick={toggleTheme}
        aria-label={theme === "dark" ? "Modo claro" : "Modo oscuro"}
        title={theme === "dark" ? "Modo claro" : "Modo oscuro"}
        className="absolute right-4 top-4 z-20 grid h-9 w-9 place-items-center rounded-coro border border-line bg-card text-ink2 transition hover:text-ink"
      >
        {theme === "dark" ? <Sun className="w-[17px] h-[17px]" /> : <Moon className="w-[17px] h-[17px]" />}
      </button>

      {/* Canales flotando: hidden en mobile para no saturar. */}
      <div className="hidden md:block absolute inset-0 pointer-events-none" aria-hidden="true">
        {FLOATING_CHANNELS.map((c, i) => {
          const meta = CANAL_META[c.canal];
          return (
            <div
              key={i}
              className="absolute bottom-0"
              style={{
                left: c.x,
                animation: `coro-float-up ${c.duration}s linear ${c.delay}s infinite`,
                willChange: "transform, opacity",
              }}
            >
              {/* La marca en su color, muy rebajada: es fondo, no contenido.
                  Sin tarjeta detrás — envolver cada logo en una card era parte
                  de lo que hacía que todo pareciera lo mismo.
                  El tamaño va en estilo porque son seis valores distintos y
                  generar seis clases arbitrarias no aporta nada. */}
              <span
                className="block opacity-[0.18]"
                style={{ width: c.size, height: c.size, color: meta.color }}
              >
                <ChannelIcon canal={c.canal} className="w-full h-full" />
              </span>
              <span className="sr-only">{meta.label}</span>
            </div>
          );
        })}
      </div>

      {/* Dos columnas ESPEJADAS: acceso (izq) + marca (der). El formulario
          entra por donde empieza la lectura, y el bloque editorial cierra. */}
      <div className="relative z-10 grid w-full max-w-[1040px] items-center gap-10 lg:grid-cols-2 lg:gap-16">
        {/* Lado editorial — solo en pantallas grandes. `order-2` lo manda a la
            derecha en desktop; en móvil sigue oculto, así que el orden del DOM
            no cambia nada para quien entra con lector de pantalla. */}
        <div className="hidden flex-col gap-5 lg:flex lg:order-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-ink3">
            {nombre}
          </p>
          <h1 className="text-[clamp(38px,4.4vw,54px)] font-extrabold leading-[1.04] tracking-[-0.03em] text-ink">
            Tus canales, <span className="accent">una sola voz</span>.
          </h1>
          <p className="max-w-[42ch] text-[15px] leading-relaxed text-ink2">
            WhatsApp, web, Instagram, Messenger, correo y teléfono en una sola bandeja, con IA y paso a
            persona cuando hace falta.
          </p>
        </div>

        {/* Card del login */}
        <div className="card w-full max-w-md p-7 sm:p-9 lg:order-1 lg:justify-self-start">
        <div className="mb-7 text-center">
          <BrandLogo className="inline-block w-12 h-12 mb-4" />
          <h2 className="text-3xl font-extrabold leading-none tracking-[-0.02em] text-ink">
            {nombre}
          </h2>
          <p className="text-sm text-ink3 mt-2">
            CRM omnicanal con agente de IA
          </p>
        </div>

        {arrancando && (
          <div
            role="status"
            className="mb-4 rounded-coro-sm border border-line bg-paper2 px-3 py-2 text-sm text-ink2"
          >
            El servidor todavía no responde. Si acabas de desplegar, está terminando de arrancar
            (la primera vez tarda unos minutos): esta página sigue sola en cuanto conteste.
          </div>
        )}

        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="label">Email</label>
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="input w-full"
              placeholder="tu@email.com"
            />
          </div>
          <div>
            <label className="label">Contraseña</label>
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="input w-full"
            />
          </div>
          {error && (
            <div className="rounded-coro-sm bg-state-bad/10 border border-state-bad/30 px-3 py-2 text-sm text-state-bad">
              {error}
            </div>
          )}
          <button type="submit" className="btn btn-primary w-full justify-center" disabled={isLoading}>
            {isLoading ? "Entrando…" : "Entrar"}
          </button>
          <Link to="/forgot-password" className="block text-center text-xs text-ink3 hover:text-ink">
            ¿Olvidaste tu contraseña?
          </Link>
        </form>

        {/* Separador */}
        <div className="relative my-5" aria-hidden="true">
          <div className="absolute inset-0 flex items-center">
            <div className="w-full border-t border-line" />
          </div>
          <div className="relative flex justify-center">
            <span className="bg-card px-3 text-xs text-ink3">o</span>
          </div>
        </div>

        {/* Entrar con Google: navega al backend, que redirige a Google y vuelve
            a /login con el token en el fragmento (#token=...). */}
        <a
          href={`${apiBaseHttp()}/api/v1/auth/google/login`}
          className="btn w-full justify-center gap-2"
        >
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
            <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.71-1.57 2.68-3.89 2.68-6.62z" />
            <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z" />
            <path fill="#FBBC05" d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z" />
            <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.47.89 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z" />
          </svg>
          Entrar con Google
        </a>

        <div className="mt-6 pt-5 border-t border-line text-center">
          <p className="text-xs text-ink3">
            <span className="font-medium text-ink2">{nombre}</span> · CRM omnicanal
          </p>
        </div>
        </div>
      </div>
    </div>
  );
}
