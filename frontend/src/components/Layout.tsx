import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Archive,
  AudioLines,
  Ban,
  BellRing,
  BrainCircuit,
  Cable,
  CalendarDays,
  ChevronUp,
  Contact,
  Eye,
  EyeOff,
  FileClock,
  Gauge,
  HeartPulse,
  History,
  SquareTerminal,
  Library,
  Lightbulb,
  LogOut,
  Menu,
  MessagesSquare,
  Moon,
  Palette,
  PanelLeftClose,
  PanelLeftOpen,
  Radio,
  Send,
  SlidersHorizontal,
  Sun,
  TrendingUp,
  UserCog,
  UsersRound,
  X,
} from "lucide-react";
import { useAuth } from "@/store/auth";
import { useTheme } from "@/store/theme";
import { usePrivacy } from "@/store/privacy";
import { FLAG_DE_RUTA, useFlags } from "@/store/flags";
import clsx from "clsx";
import { InternalAgentWidget } from "@/components/InternalAgentWidget";
import { PushButton } from "@/components/PushButton";
import { listConversations } from "@/services/conversations";
import { useMarca } from "@/store/apariencia";
import { BrandLogo } from "@/components/BrandLogo";

type NavItem = {
  to: string;
  label: string;
  icon: typeof MessagesSquare;
  badge?: number;
  end?: boolean;
};

const adminHomeNav: NavItem[] = [{ to: "/admin", label: "Resumen", icon: Gauge, end: true }];

const clientNav: NavItem[] = [
  { to: "/inbox", label: "Chats", icon: MessagesSquare },
  // Registro de llamadas del canal de voz (Retell), solo lectura. Justo debajo
  // de Chats, tanto para operador como para admin (que hereda clientNav).
  //
  // La entrada se pinta SIEMPRE, también con Retell sin conectar. Se valoró
  // ocultarla consultando GET /voice/status desde aquí y se descartó:
  //   1. La página ya distingue las dos situaciones — pinta "Retell no está
  //      conectado" (con dónde conectarlo) en vez de "aún no hay llamadas".
  //      Ese era el problema real, y está resuelto en su sitio.
  //   2. Sondear el estado desde el menú añade una petición en cada carga y
  //      hace que la entrada aparezca/desaparezca a mitad del render.
  //   3. Ocultarla esconde justo el sitio donde se explica que falta conectar
  //      Retell: quien no lo ha conectado nunca descubriría que existe.
  { to: "/calls", label: "Voz", icon: AudioLines },
  // Agenda propia (Fase 9): las citas de todos los canales.
  { to: "/agenda", label: "Agenda", icon: CalendarDays },
  { to: "/contacts", label: "Clientes", icon: Contact },
];

// Menú del OPERADOR (no admin): solo el día a día. El Conocimiento NO va aquí:
// la base de conocimiento es de admin entera, también lo que solo lee (ver el
// mapa de roles en backend/app/api/deps.py), así que la entrada le llevaba a
// una pantalla que no cargaba nada.
const operatorNav: NavItem[] = [...clientNav];

// Revisor de Meta: la bandeja y una pantalla de Canales recortada a la
// tarjeta de Instagram (Meta pide ver DÓNDE se conecta la cuenta, no solo que
// esté conectada). Clientes, voz y conocimiento no son suyos, y el servidor
// tampoco se los daría (lista blanca en deps.py).
const revisorNav: NavItem[] = [
  { to: "/inbox", label: "Chats", icon: MessagesSquare },
  { to: "/conexiones", label: "Canales", icon: Cable },
];

// Atención para admin = lo del operador + campañas.
const adminOperativaNav: NavItem[] = [
  ...clientNav,
  { to: "/admin/outbound", label: "Campañas", icon: Send },
];

const adminUsersNav: NavItem[] = [
  { to: "/admin/users", label: "Equipo", icon: UsersRound },
  { to: "/admin/connections", label: "Canales", icon: Cable },
  // Bloqueados va justo debajo de Canales: se gestiona junto a ellos.
  { to: "/admin/blocklist", label: "Bloqueados", icon: Ban },
  // Ajustes generales de la app (zona horaria, etc.).
  { to: "/admin/settings", label: "Preferencias", icon: SlidersHorizontal },
  // Marca blanca: nombre, logotipo, color, letra e iconos de la instalación.
  { to: "/admin/apariencia", label: "Apariencia", icon: Palette },
];

const adminAgentNav: NavItem[] = [
  { to: "/admin/agent/dashboard", label: "Rendimiento", icon: TrendingUp },
  { to: "/admin/agent/flow", label: "Actividad", icon: Radio },
  // La antigua "Configuración modelo" es la pestaña "Tarifas y límites"
  // dentro de Asistentes (la ruta vieja /admin/agent/config redirige allí).
  { to: "/admin/agent/agents", label: "Asistentes", icon: BrainCircuit },
  // Lo que Sofía manda sola: recordatorio de cita y «no llegó» (SPEC-18).
  { to: "/admin/agent/seguimientos", label: "Seguimientos", icon: BellRing },
  // Conocimiento vive SOLO aquí. Antes estaba también en el grupo de arriba:
  // la misma pantalla en dos sitios del menú, que es la forma más rápida de
  // que nadie sepa cuál es "el bueno".
  { to: "/knowledge-base", label: "Conocimiento", icon: Library },
  { to: "/admin/agent/learning", label: "Mejoras", icon: Lightbulb },
];

const adminSystemNav: NavItem[] = [
  // Actividad del runtime y auditoría de personas eran DOS entradas para la
  // misma pregunta: qué ha pasado aquí. Quien busca un problema no sabe en
  // cuál mirar, así que abría las dos. Ahora una entrada y dos pestañas.
  { to: "/admin/system/logs", label: "Registros", icon: FileClock },
  { to: "/admin/health", label: "Estado", icon: HeartPulse },
  // Las copias viven en Plataforma (junto a estado/retención), no en Cuenta.
  { to: "/admin/backups", label: "Respaldos", icon: Archive },
  // Qué trae la versión instalada, en cristiano. Sale del CHANGELOG.md que
  // viaja dentro de la imagen.
  { to: "/admin/system/novedades", label: "Cambios", icon: History },
  // Los guiones para pegar en Claude Code (Fase 10.2), también de la versión
  // instalada: salen de backend/prompts/.
  { to: "/admin/system/prompts", label: "Prompts", icon: SquareTerminal },
];

// Avatar del sidebar — el color de marca, con las iniciales encima. No cambia
// con el tema ni con el usuario, pero sí con la marca de la instalación.
const AVATAR_BG = "var(--brand)";
// `--brand-on` es el token de "tinta sobre superficie de marca": el mismo
// valor en claro y oscuro, así que se mantiene legible en ambos temas (el
// fondo del avatar no cambia con el tema).
const AVATAR_INK = "var(--brand-on)";

function initialsOf(name: string | null | undefined, email: string | null | undefined): string {
  const source = (name || email || "?").trim();
  if (!source) return "?";
  const parts = source.split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return source.slice(0, 2).toUpperCase();
  const first = parts[0]?.[0] ?? "";
  const second = parts.length > 1 ? parts[1]?.[0] ?? "" : parts[0]?.[1] ?? "";
  return (first + second).toUpperCase();
}

export function Layout() {
  const { partes: marca } = useMarca();
  const user = useAuth((s) => s.user);
  const logout = useAuth((s) => s.logout);
  const navigate = useNavigate();
  const location = useLocation();

  const theme = useTheme((s) => s.theme);
  const toggleTheme = useTheme((s) => s.toggle);

  const privacy = usePrivacy((s) => s.enabled);
  const togglePrivacy = usePrivacy((s) => s.toggle);

  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("chatbot:sidebar-collapsed") === "1"
  );
  const [attention, setAttention] = useState(0);

  useEffect(() => {
    localStorage.setItem("chatbot:sidebar-collapsed", collapsed ? "1" : "0");
  }, [collapsed]);

  // Badge del Inbox: nº de conversaciones PENDIENTES de una persona (derivadas
  // a humano + sugerencias/borradores sin resolver + sin contestar) — el mismo
  // criterio que la pestaña "Pendientes" del inbox. Refresco ligero cada 30s.
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const p = await listConversations({ pending_only: true, active_only: true, page_size: 1 });
        if (alive) setAttention(p.total);
      } catch {
        /* ignore */
      }
    }
    void load();
    const t = setInterval(load, 30000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  // Cierra el drawer al cambiar de ruta.
  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  // Bloquea scroll del body cuando el drawer está abierto.
  useEffect(() => {
    if (mobileOpen) document.body.style.overflow = "hidden";
    else document.body.style.overflow = "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [mobileOpen]);

  // Los módulos apagados (Ajustes → Módulos) no aparecen en el menú. Se cargan
  // una vez por sesión de admin; mientras llegan, la entrada no se pinta.
  const flags = useFlags((s) => s.flags);
  const loadFlags = useFlags((s) => s.load);
  useEffect(() => {
    if (user?.role === "admin") void loadFlags();
  }, [user?.role, loadFlags]);

  const sections = useMemo(() => {
    const visible = (items: NavItem[]) =>
      items.filter(({ to }) => {
        const flag = FLAG_DE_RUTA[to];
        return !flag || flags[flag] === true;
      });
    if (user?.role === "admin") {
      return [
        { title: "", items: adminHomeNav },
        { title: "Atención", items: visible(adminOperativaNav) },
        { title: "Asistente", items: visible(adminAgentNav) },
        { title: "Cuenta", items: adminUsersNav },
        { title: "Plataforma", items: adminSystemNav },
      ];
    }
    if (user?.role === "revisor") return [{ title: "", items: revisorNav }];
    return [{ title: "", items: operatorNav }];
  }, [user?.role, flags]);

  async function onLogout() {
    await logout();
    navigate("/login");
  }

  function onNewConversation() {
    navigate("/inbox");
  }

  const initials = initialsOf(user?.nombre, user?.email);

  return (
    // Desktop en GRID, no en flex: colapsar el menú anima
    // `grid-template-columns`, que es la técnica que existe justamente para
    // esto. Antes se animaba el `width` del <aside>, y animar una propiedad
    // de layout dispara un reflow en CADA frame: se nota en portátiles y con
    // el Inbox abierto, que es la pantalla con más nodos.
    <div
      className={clsx(
        "flex h-full bg-paper text-ink",
        "lg:grid lg:transition-[grid-template-columns] lg:duration-200 lg:ease-[cubic-bezier(0.16,1,0.3,1)]",
        collapsed ? "lg:grid-cols-[72px_1fr]" : "lg:grid-cols-[240px_1fr]"
      )}
    >
      {/* Backdrop móvil */}
      {mobileOpen && (
        <div
          className="lg:hidden fixed inset-0 z-40 bg-ink/40 backdrop-blur-[1px]"
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Sidebar */}
      <aside
        className={clsx(
          "z-50 bg-paper2 border-r border-line flex flex-col",
          // Mobile: drawer full-screen width 100% (max 320). Desktop: 220px estático.
          // Móvil: cajón que entra con `transform` (no toca layout).
          "fixed inset-y-0 left-0 w-[86vw] max-w-[320px] transform transition-transform duration-200 ease-[cubic-bezier(0.16,1,0.3,1)]",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
          // Desktop: el ancho lo decide la columna del grid de arriba.
          "lg:static lg:translate-x-0 lg:w-auto lg:max-w-none lg:overflow-hidden"
        )}
        style={{ padding: "18px 14px" }}
      >
        {/* Header */}
        <div className={clsx("flex items-center mb-4", collapsed ? "lg:justify-center" : "justify-between")}>
          {/* Marca: icono line-art en currentColor (sin tile),
              wordmark serif itálica y subtítulo uppercase atenuado. El nombre
              sale de Apariencia o, si no, de APP_NAME: primera palabra
              en grande y el resto como subtítulo, si lo hay. */}
          <div className={clsx("flex items-center gap-2.5 min-w-0", collapsed && "lg:hidden")}>
            <BrandLogo className="w-7 h-7 shrink-0 text-ink" />
            <div className="leading-none min-w-0">
              <div
                className="font-display font-extrabold tracking-[-0.02em] text-ink"
                style={{ fontSize: 20, letterSpacing: "-0.01em" }}
              >
                {marca.main}
              </div>
              {marca.sub && (
                <div
                  className="text-ink3 mt-0.5"
                  style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.14em" }}
                >
                  {marca.sub}
                </div>
              )}
            </div>
          </div>
          <button
            type="button"
            aria-label="Cerrar menú"
            onClick={() => setMobileOpen(false)}
            className="lg:hidden inline-flex items-center justify-center w-9 h-9 rounded-coro-sm hover:bg-paper3"
          >
            <X className="w-4 h-4 text-ink2" />
          </button>
          <button
            type="button"
            aria-label={collapsed ? "Expandir menú" : "Colapsar menú"}
            title={collapsed ? "Expandir menú" : "Colapsar menú"}
            onClick={() => setCollapsed((c) => !c)}
            className="hidden lg:inline-flex items-center justify-center w-9 h-9 rounded-coro-sm hover:bg-paper3 text-ink2 shrink-0"
          >
            {collapsed ? <PanelLeftOpen className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
          </button>
        </div>


        {/* Nav */}
        <nav className="flex-1 overflow-auto -mx-1 px-1 space-y-4">
          {sections.map(({ title, items }, idx) => (
            <div key={idx} className="space-y-0.5">
              {title && (
                <div
                  className={clsx("px-2 pt-2 pb-1 text-ink3 font-medium", collapsed && "lg:hidden")}
                  style={{
                    fontSize: 10,
                    textTransform: "uppercase",
                    letterSpacing: "0.08em",
                  }}
                >
                  {title}
                </div>
              )}
              {items.map(({ to, label, icon: Icon, badge, end }) => {
                const badgeVal = to === "/inbox" ? attention : badge;
                return (
                  <NavLink
                    key={to}
                    to={to}
                    end={end}
                    title={collapsed ? label : undefined}
                    className={({ isActive }) =>
                      clsx(
                        "flex items-center gap-2.5 rounded-coro-sm px-2.5 py-2 text-sm transition-colors",
                        collapsed && "lg:justify-center lg:px-0",
                        isActive
                          ? "bg-brand/15 text-brand-ink font-semibold"
                          : "text-ink2 hover:bg-paper3 font-medium"
                      )
                    }
                  >
                    <Icon className="w-4 h-4 shrink-0" />
                    <span className={clsx("flex-1 truncate", collapsed && "lg:hidden")}>{label}</span>
                    {typeof badgeVal === "number" && badgeVal > 0 && (
                      <span
                        className={clsx("bg-brand text-brand-on rounded-full font-semibold", collapsed && "lg:hidden")}
                        style={{
                          fontSize: 10,
                          padding: "1px 7px",
                          lineHeight: "16px",
                        }}
                      >
                        {badgeVal}
                      </span>
                    )}
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>

        {/* Footer: una sola fila. Antes eran dos —usuario arriba y una tira
            de cuatro iconos debajo— y esa tira era lo que más delataba el
            origen del panel: cuatro botones sueltos sin jerarquía, siempre a
            la vista, compitiendo con la navegación.
            Ahora el bloque de usuario ES el disparador y las acciones viven
            en un menú que sale de él. La navegación se queda sola en el
            sidebar, que es de lo que va un sidebar. */}
        <div className="pt-3 mt-3 border-t border-line relative group/usuario">
          <div
            className={clsx(
              "flex items-center gap-2.5 min-w-0 rounded-coro-sm p-1.5 -m-0.5 cursor-pointer hover:bg-paper3 transition-colors",
              collapsed && "lg:justify-center"
            )}
            tabIndex={0}
            role="button"
            aria-haspopup="menu"
            aria-label="Cuenta y preferencias"
          >
            <div
              className="w-8 h-8 rounded-full flex items-center justify-center text-[12px] font-bold shrink-0"
              style={{ background: AVATAR_BG, color: AVATAR_INK }}
              aria-hidden="true"
            >
              {initials}
            </div>
            <div className={clsx("flex-1 min-w-0 leading-tight", collapsed && "lg:hidden")}>
              <div className="text-[12px] font-semibold text-ink truncate">
                {user?.nombre || user?.email || "—"}
              </div>
              <div className="text-[11px] text-ink3 truncate">{user?.email}</div>
            </div>
            <ChevronUp
              className={clsx(
                "w-3.5 h-3.5 text-ink4 shrink-0 transition-transform duration-200",
                "group-hover/usuario:-translate-y-0.5",
                collapsed && "lg:hidden"
              )}
            />
          </div>

          {/* Menú. Se abre al pasar el ratón y al enfocar con el teclado; se
              anima con opacity+translate, nunca con height. */}
          <div
            className="absolute bottom-full left-0 right-0 z-30 mb-2 rounded-coro border border-line bg-card p-1.5 shadow-coro-3
                       opacity-0 translate-y-1 pointer-events-none
                       transition-[opacity,transform] duration-200 ease-[cubic-bezier(0.16,1,0.3,1)]
                       group-hover/usuario:opacity-100 group-hover/usuario:translate-y-0 group-hover/usuario:pointer-events-auto
                       group-focus-within/usuario:opacity-100 group-focus-within/usuario:translate-y-0 group-focus-within/usuario:pointer-events-auto"
            role="menu"
          >
            <NavLink
              to="/profile"
              role="menuitem"
              className="flex items-center gap-2.5 rounded-coro-sm px-2.5 py-2 text-sm text-ink2 hover:bg-paper3 transition-colors"
            >
              <UserCog className="w-4 h-4 shrink-0" />
              Mi perfil
              <span className="ml-auto text-[10px] uppercase tracking-wider text-ink4">
                {user?.role}
              </span>
            </NavLink>

            <button
              type="button"
              role="menuitem"
              aria-pressed={privacy}
              onClick={togglePrivacy}
              className="w-full flex items-center gap-2.5 rounded-coro-sm px-2.5 py-2 text-sm text-ink2 hover:bg-paper3 transition-colors"
            >
              {privacy ? <EyeOff className="w-4 h-4 shrink-0" /> : <Eye className="w-4 h-4 shrink-0" />}
              Modo privacidad
              <span
                className={clsx(
                  "ml-auto text-[10px] font-semibold uppercase tracking-wider",
                  privacy ? "text-brand-ink" : "text-ink4"
                )}
              >
                {privacy ? "activo" : "off"}
              </span>
            </button>

            <button
              type="button"
              role="menuitem"
              onClick={toggleTheme}
              className="w-full flex items-center gap-2.5 rounded-coro-sm px-2.5 py-2 text-sm text-ink2 hover:bg-paper3 transition-colors"
            >
              {theme === "dark" ? <Sun className="w-4 h-4 shrink-0" /> : <Moon className="w-4 h-4 shrink-0" />}
              {theme === "dark" ? "Modo claro" : "Modo oscuro"}
            </button>

            {/* Las notificaciones traen su propio permiso del navegador, así
                que el botón se monta tal cual dentro de su fila. */}
            <div className="flex items-center gap-2.5 rounded-coro-sm px-2.5 py-1.5 text-sm text-ink2">
              <PushButton />
              <span className="text-ink3 text-[13px]">Notificaciones</span>
            </div>

            <div className="my-1 border-t border-line2" />

            <button
              type="button"
              role="menuitem"
              onClick={onLogout}
              className="w-full flex items-center gap-2.5 rounded-coro-sm px-2.5 py-2 text-sm text-state-bad hover:bg-state-bad/10 transition-colors"
            >
              <LogOut className="w-4 h-4 shrink-0" />
              Cerrar sesión
            </button>
          </div>
        </div>
      </aside>

      {/* Columna de contenido: cabecera móvil + main */}
      <div className="flex flex-col flex-1 min-w-0 h-full overflow-hidden">
        {/* Cabecera móvil: marca a la izquierda, menú a la derecha (no se pisan) */}
        <header className="lg:hidden flex items-center justify-between gap-3 shrink-0 px-4 py-2.5 border-b border-line bg-paper">
          <div className="flex items-center gap-2.5 min-w-0">
            <BrandLogo className="w-6 h-6 shrink-0 text-ink" />
            <span
              className="font-display font-extrabold tracking-[-0.02em] text-ink truncate"
              style={{ fontSize: 19, letterSpacing: "-0.01em" }}
            >
              {marca.main}
            </span>
          </div>
          <button
            type="button"
            aria-label="Abrir menú"
            onClick={() => setMobileOpen(true)}
            className="inline-flex items-center justify-center w-10 h-10 rounded-coro-sm hover:bg-paper3 shrink-0"
          >
            <Menu className="w-5 h-5 text-ink" />
          </button>
        </header>

        {/* Main */}
        <main className="flex-1 overflow-auto bg-paper">
          <Outlet />
        </main>
      </div>

      {/* Indicador del modo privacidad: inconfundible durante un directo y
          clicable para desactivarlo. Esquina inferior izquierda para no chocar
          con el agente interno (inferior derecha). */}
      {privacy && (
        <button
          type="button"
          onClick={togglePrivacy}
          title="Modo privacidad activo — clic para desactivar"
          className="fixed bottom-3 left-3 z-30 inline-flex items-center gap-1.5 rounded-full bg-brand text-brand-on shadow-coro-1 text-[11px] font-semibold px-3 py-1.5"
        >
          <EyeOff className="w-3.5 h-3.5" /> Modo privacidad
        </button>
      )}

      {/* Agente interno: chat flotante read-only solo para admin. */}
      {user?.role === "admin" && flags["agente_interno"] === true && <InternalAgentWidget />}
    </div>
  );
}