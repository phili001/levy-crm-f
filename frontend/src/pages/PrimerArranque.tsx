import { FormEvent, useEffect, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { Check, ClipboardCopy } from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";
import { PASSWORD_HINT, PASSWORD_MIN_LENGTH, PASSWORD_RULE_ERROR } from "@/lib/password";
import { apiBaseHttp } from "@/services/api";
import {
  crearAdministrador,
  necesitaAdministrador,
  type RespuestaPrimerArranque,
} from "@/services/primerArranque";
import { useMarca } from "@/store/apariencia";
import { useAuth } from "@/store/auth";

// Primer arranque de la instalación en un clic (Fase 10.3b). Coolify despliega
// el CRM sin administrador y el dueño lo crea aquí, con el código de arranque
// que solo él ve en su panel de Coolify. Después se le enseña la llave de
// cifrado UNA vez: sin ella, lo cifrado no se recupera si se pierde el servidor.

// El mismo texto que devuelve el backend si el código no es el bueno
// (DONDE_ESTA_EL_CODIGO en api/primer_arranque.py).
const DONDE_ESTA_EL_CODIGO =
  "Está en Coolify: abre el service del CRM, pestaña Environment Variables, " +
  "y copia el valor de SERVICE_PASSWORD_ARRANQUE.";

type Paso = "comprobando" | "alta" | "llave" | "no_hace_falta";

interface Fallo {
  texto: string;
  // 409: ya hay administrador (o ese correo ya tiene cuenta). Se ofrece entrar.
  irAEntrar: boolean;
}

type ErrorDeAxios = { response?: { status?: number; data?: { detail?: unknown } } };

function falloDe(e: unknown): Fallo {
  const res = (e as ErrorDeAxios | null)?.response;
  // Sin respuesta no es un dato mal puesto: el panel no llega a la API. Decir
  // otra cosa manda a quien instala a repasar el formulario en vez del servidor.
  if (!res) {
    return {
      texto:
        "No se ha podido conectar con el servidor. Comprueba que la API responde " +
        `(${apiBaseHttp()}/health) y vuelve a intentarlo.`,
      irAEntrar: false,
    };
  }
  if (res.status === 429) {
    return { texto: "Demasiados intentos seguidos. Espera un minuto y vuelve a probar.", irAEntrar: false };
  }
  const detalle = res.data?.detail;
  if (typeof detalle === "string" && detalle.trim()) {
    return { texto: detalle, irAEntrar: res.status === 409 };
  }
  // El 422 de validación de FastAPI trae una lista con el campo que falla.
  if (Array.isArray(detalle) && JSON.stringify(detalle).includes('"email"')) {
    return { texto: "Ese correo no parece válido. Revísalo.", irAEntrar: false };
  }
  return { texto: "No se pudo crear el administrador. Revisa los datos y vuelve a intentarlo.", irAEntrar: false };
}

export default function PrimerArranque() {
  const navigate = useNavigate();
  const loginWithToken = useAuth((s) => s.loginWithToken);
  const [paso, setPaso] = useState<Paso>("comprobando");
  const [respuesta, setRespuesta] = useState<RespuestaPrimerArranque | null>(null);
  const [errorAlEntrar, setErrorAlEntrar] = useState<string | null>(null);
  const [entrando, setEntrando] = useState(false);

  // Solo al llegar: después de crear el administrador el backend ya dice que no
  // hace falta, y volver a preguntar mandaría al login justo con la llave en
  // pantalla.
  useEffect(() => {
    necesitaAdministrador()
      .then((falta) => setPaso(falta ? "alta" : "no_hace_falta"))
      .catch((e: unknown) => {
        // Una versión sin primer arranque responde 404: ahí se entra como siempre.
        // Cualquier otro fallo deja el formulario, que explica lo que pase al enviar.
        const estado = (e as ErrorDeAxios | null)?.response?.status;
        setPaso(estado === 404 ? "no_hace_falta" : "alta");
      });
  }, []);

  // La llave no se vuelve a enseñar: si se cierra o recarga la pestaña antes
  // de confirmar que está guardada, el navegador pregunta.
  useEffect(() => {
    if (paso !== "llave") return;
    const avisar = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", avisar);
    return () => window.removeEventListener("beforeunload", avisar);
  }, [paso]);

  async function entrar() {
    if (!respuesta) return;
    setEntrando(true);
    setErrorAlEntrar(null);
    await loginWithToken(respuesta.access_token);
    if (!useAuth.getState().user) {
      setEntrando(false);
      setErrorAlEntrar(
        "La cuenta ya está creada, pero no se pudo abrir la sesión. Entra con tu correo y tu contraseña.",
      );
      return;
    }
    navigate("/admin", { replace: true });
  }

  if (paso === "no_hace_falta") return <Navigate to="/login" replace />;

  return (
    <div className="min-h-full flex items-center justify-center bg-paper p-4 sm:p-6">
      <div className="card w-full max-w-md p-7 sm:p-9">
        {paso === "comprobando" && (
          <p className="text-center text-sm text-ink3">Comprobando la instalación…</p>
        )}
        {paso === "alta" && (
          <FormularioAlta
            onCreado={(r) => {
              setRespuesta(r);
              setPaso("llave");
            }}
          />
        )}
        {paso === "llave" && respuesta && (
          <GuardarLlave
            llave={respuesta.llave_cifrado}
            entrando={entrando}
            error={errorAlEntrar}
            onEntrar={() => void entrar()}
          />
        )}
      </div>
    </div>
  );
}

function Cabecera({ titulo, texto }: { titulo: string; texto: string }) {
  const { nombre } = useMarca();
  return (
    <div className="mb-6 text-center">
      <BrandLogo className="inline-block w-12 h-12 mb-4" />
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-ink3">{nombre}</p>
      <h1 className="mt-2 text-2xl font-extrabold leading-tight tracking-[-0.02em] text-ink">
        {titulo}
      </h1>
      <p className="mt-2 text-sm leading-relaxed text-ink2">{texto}</p>
    </div>
  );
}

function FormularioAlta({ onCreado }: { onCreado: (r: RespuestaPrimerArranque) => void }) {
  const [codigo, setCodigo] = useState("");
  const [nombre, setNombre] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [fallo, setFallo] = useState<Fallo | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setFallo(null);
    if (password.length < PASSWORD_MIN_LENGTH) {
      setFallo({ texto: PASSWORD_RULE_ERROR, irAEntrar: false });
      return;
    }
    if (password !== password2) {
      setFallo({ texto: "Las contraseñas no coinciden.", irAEntrar: false });
      return;
    }
    setEnviando(true);
    try {
      onCreado(
        await crearAdministrador({
          codigo: codigo.trim(),
          nombre: nombre.trim() || undefined,
          email: email.trim(),
          password,
        }),
      );
    } catch (err) {
      setFallo(falloDe(err));
      setEnviando(false);
    }
  }

  return (
    <>
      <Cabecera
        titulo="Crea el administrador"
        texto="Es el primer arranque de este CRM. Esta cuenta es la del dueño: con ella entras y configuras todo lo demás."
      />
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label className="label" htmlFor="pa-codigo">Código de arranque</label>
          <input
            id="pa-codigo"
            required
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            value={codigo}
            onChange={(e) => setCodigo(e.target.value)}
            className="input w-full font-mono"
            aria-describedby="pa-codigo-ayuda"
          />
          <p id="pa-codigo-ayuda" className="mt-1.5 text-xs leading-relaxed text-ink3">
            {DONDE_ESTA_EL_CODIGO}
          </p>
        </div>
        <div>
          <label className="label" htmlFor="pa-nombre">Tu nombre</label>
          <input
            id="pa-nombre"
            autoComplete="name"
            maxLength={120}
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            className="input w-full"
            placeholder="Opcional"
          />
        </div>
        <div>
          <label className="label" htmlFor="pa-email">Correo</label>
          <input
            id="pa-email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="input w-full"
            placeholder="tu@clinica.mx"
            aria-describedby="pa-email-ayuda"
          />
          <p id="pa-email-ayuda" className="mt-1.5 text-xs text-ink3">
            Con él entras y recuperas la contraseña si la olvidas.
          </p>
        </div>
        <div>
          <label className="label" htmlFor="pa-password">Contraseña</label>
          <input
            id="pa-password"
            type="password"
            required
            autoComplete="new-password"
            minLength={PASSWORD_MIN_LENGTH}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="input w-full"
            placeholder={PASSWORD_HINT}
          />
        </div>
        <div>
          <label className="label" htmlFor="pa-password2">Repite la contraseña</label>
          <input
            id="pa-password2"
            type="password"
            required
            autoComplete="new-password"
            minLength={PASSWORD_MIN_LENGTH}
            value={password2}
            onChange={(e) => setPassword2(e.target.value)}
            className="input w-full"
          />
        </div>
        {fallo && (
          <div
            role="alert"
            className="rounded-coro-sm bg-state-bad/10 border border-state-bad/30 px-3 py-2 text-sm text-state-bad"
          >
            {fallo.texto}
            {fallo.irAEntrar && (
              <Link to="/login" className="mt-1 block font-medium underline">
                Ir a entrar
              </Link>
            )}
          </div>
        )}
        <button type="submit" className="btn btn-primary w-full justify-center" disabled={enviando}>
          {enviando ? "Creando…" : "Crear administrador"}
        </button>
      </form>
    </>
  );
}

interface GuardarLlaveProps {
  llave: string;
  entrando: boolean;
  error: string | null;
  onEntrar: () => void;
}

function GuardarLlave({ llave, entrando, error, onEntrar }: GuardarLlaveProps) {
  const [copia, setCopia] = useState<"lista" | "copiada" | "error">("lista");
  const [guardada, setGuardada] = useState(false);

  async function copiar() {
    try {
      await navigator.clipboard.writeText(llave);
      setCopia("copiada");
    } catch {
      setCopia("error");
    }
    window.setTimeout(() => setCopia("lista"), 2500);
  }

  return (
    <>
      <Cabecera
        titulo="Guarda la llave de cifrado"
        texto="Con ella se guardan cifradas las claves de tus canales, las notas internas y las transcripciones. Si se pierde, eso no se recupera nunca."
      />
      <div className="rounded-coro-sm border border-line bg-paper2 p-3">
        <code className="block select-all break-all font-mono text-[13px] leading-relaxed text-ink">
          {llave}
        </code>
        <button type="button" onClick={() => void copiar()} className="btn btn-sm mt-3">
          {copia === "copiada" ? <Check className="text-state-ok" /> : <ClipboardCopy />}
          {copia === "copiada"
            ? "Copiada"
            : copia === "error"
              ? "No se pudo copiar: selecciónala a mano"
              : "Copiar la llave"}
        </button>
      </div>
      <p className="mt-4 text-xs leading-relaxed text-ink3">
        Guárdala en tu gestor de contraseñas. Coolify también la tiene
        (SERVICE_PASSWORD_64_CIFRADO), pero si pierdes el servidor, esa copia se va con él.
        Aquí no se vuelve a enseñar.
      </p>
      <label className="mt-5 flex cursor-pointer items-start gap-2.5 text-sm text-ink">
        <input
          type="checkbox"
          checked={guardada}
          onChange={(e) => setGuardada(e.target.checked)}
          className="mt-0.5 h-4 w-4 shrink-0 accent-brand"
        />
        Ya la guardé en mi gestor de contraseñas
      </label>
      {error && (
        <div
          role="alert"
          className="mt-4 rounded-coro-sm bg-state-bad/10 border border-state-bad/30 px-3 py-2 text-sm text-state-bad"
        >
          {error}
          <Link to="/login" className="mt-1 block font-medium underline">
            Ir a entrar
          </Link>
        </div>
      )}
      <button
        type="button"
        onClick={onEntrar}
        disabled={!guardada || entrando}
        className="btn btn-primary mt-5 w-full justify-center"
      >
        {entrando ? "Entrando…" : "Entrar al panel"}
      </button>
    </>
  );
}
