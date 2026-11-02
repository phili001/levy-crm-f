import type { ReactNode } from "react";
import { Navigate, Outlet, createBrowserRouter } from "react-router-dom";
import { Layout } from "@/components/Layout";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { useAuth } from "@/store/auth";
import Login from "@/pages/Login";
import NotFound from "@/pages/NotFound";
import ProfilePage from "@/pages/ProfilePage";
import ForgotPasswordPage from "@/pages/ForgotPasswordPage";
import ResetPasswordPage from "@/pages/ResetPasswordPage";
import PrimerArranque from "@/pages/PrimerArranque";
import Inbox from "@/pages/client/Inbox";
import Calls from "@/pages/client/Calls";
import Agenda from "@/pages/client/Agenda";
import Contacts from "@/pages/client/Contacts";
import ContactDetail from "@/pages/client/ContactDetail";
import KnowledgeBase from "@/pages/client/KnowledgeBase";
import AdminHome from "@/pages/admin/AdminHome";
import UsersPage from "@/pages/admin/UsersPage";
import BackupsPage from "@/pages/admin/BackupsPage";
import ConnectionsPage from "@/pages/admin/ConnectionsPage";
import AgentDashboard from "@/pages/admin/AgentDashboard";
import AgentsPage from "@/pages/admin/AgentsPage";
import FlowDashboard from "@/pages/admin/FlowDashboard";
import OutboundPage from "@/pages/admin/OutboundPage";
import SeguimientosPage from "@/pages/admin/SeguimientosPage";
import SystemLogs from "@/pages/admin/SystemLogs";
import ChangelogPage from "@/pages/admin/ChangelogPage";
import PromptsPage from "@/pages/admin/PromptsPage";
import AuditPage from "@/pages/admin/AuditPage";
import HealthPage from "@/pages/admin/HealthPage";
import BlocklistPage from "@/pages/admin/BlocklistPage";
import SettingsPage from "@/pages/admin/SettingsPage";
import AparienciaPage from "@/pages/admin/AparienciaPage";
import LearningPage from "@/pages/admin/LearningPage";
import InternalAgentPromptPage from "@/pages/admin/InternalAgentPromptPage";
import ConexionesRevisor from "@/pages/revisor/ConexionesRevisor";
import { useFlags } from "@/store/flags";
import DemoPage from "@/pages/public/DemoPage";
import LegalPage from "@/pages/public/LegalPage";

// El admin aterriza en la Home (dashboard); el operador y el revisor, en el inbox.
function HomeRedirect() {
  const role = useAuth((s) => s.user?.role);
  return <Navigate to={role === "admin" ? "/admin" : "/inbox"} replace />;
}

/**
 * Puerta para las pantallas de operador (llamadas, agenda, contactos). El revisor de Meta no pinta nada ahí: el servidor ya se lo
 * niega, pero sin esto vería la pantalla montada y llena de errores. Se le
 * devuelve a su bandeja.
 */
function SoloOperadores({ children }: { children: ReactNode }) {
  const role = useAuth((s) => s.user?.role);
  if (role === "revisor") return <Navigate to="/inbox" replace />;
  return <>{children}</>;
}

/**
 * La base de conocimiento vive fuera de /admin (su ruta es /knowledge-base) pero
 * es solo del admin: el servidor le niega todos sus endpoints a los demás. Sin
 * esta puerta, quien llegara por un enlace guardado vería la pantalla vacía y
 * llena de errores.
 */
function SoloAdmin({ children }: { children: ReactNode }) {
  const role = useAuth((s) => s.user?.role);
  if (role !== "admin") return <Navigate to="/inbox" replace />;
  return <>{children}</>;
}

/**
 * Al revés que la anterior: /conexiones es la versión recortada de Conexiones
 * que solo existe para el revisor de Meta (una tarjeta, sin controles). Quien
 * tiene la pantalla de verdad va a la de verdad, y el operador —que no manda
 * en las conexiones de nadie— a su bandeja.
 */
function SoloRevisor({ children }: { children: ReactNode }) {
  const role = useAuth((s) => s.user?.role);
  if (role === "admin") return <Navigate to="/admin/connections" replace />;
  if (role !== "revisor") return <Navigate to="/inbox" replace />;
  return <>{children}</>;
}

// Rutas de módulos apagables (Ajustes → Módulos). Con la bandera apagada el
// backend ya responde 404; aquí se evita pintar una pantalla vacía y se manda
// al admin a Inicio. Hasta que las banderas cargan no se decide nada.
function SoloConModulo({ clave, children }: { clave: string; children: ReactNode }) {
  const loaded = useFlags((s) => s.loaded);
  const on = useFlags((s) => s.flags[clave] === true);
  if (!loaded) return null;
  if (!on) return <Navigate to="/admin" replace />;
  return <>{children}</>;
}

export const router = createBrowserRouter([
  { path: "/login", element: <Login /> },
  { path: "/forgot-password", element: <ForgotPasswordPage /> },
  { path: "/reset-password", element: <ResetPasswordPage /> },
  // Pública: en la instalación en un clic el CRM arranca sin administrador y
  // el dueño lo crea aquí con el código de arranque (ver PrimerArranque).
  { path: "/primer-arranque", element: <PrimerArranque /> },
  // Pública a propósito: la web de prueba con el widget real (ver DemoPage).
  { path: "/demo", element: <DemoPage /> },
  // Públicas: Meta (vía Zernio) pide una URL de privacidad, y la ley de datos
  // obliga a publicar el aviso. Ver LegalPage.
  { path: "/privacidad", element: <LegalPage tipo="privacidad" /> },
  { path: "/terminos", element: <LegalPage tipo="terminos" /> },
  {
    path: "/",
    element: (
      <ProtectedRoute>
        <Layout />
      </ProtectedRoute>
    ),
    children: [
      { index: true, element: <HomeRedirect /> },
      { path: "inbox", element: <Inbox /> },
      // Conexiones del revisor de Meta. Va FUERA de /admin a propósito: esa
      // rama entera está tras ProtectedRoute role="admin", y el revisor no lo es.
      { path: "conexiones", element: <SoloRevisor><ConexionesRevisor /></SoloRevisor> },
      { path: "calls", element: <SoloOperadores><Calls /></SoloOperadores> },
      // Agenda propia (Fase 9): operador y admin. El revisor de Meta no la ve.
      { path: "agenda", element: <SoloOperadores><Agenda /></SoloOperadores> },
      { path: "contacts", element: <SoloOperadores><Contacts /></SoloOperadores> },
      { path: "contacts/:id", element: <SoloOperadores><ContactDetail /></SoloOperadores> },
      {
        path: "knowledge-base",
        element: (
          <SoloAdmin>
            <KnowledgeBase />
          </SoloAdmin>
        ),
      },
      { path: "profile", element: <ProfilePage /> },
      {
        path: "admin",
        element: (
          <ProtectedRoute role="admin">
            <Outlet />
          </ProtectedRoute>
        ),
        children: [
          { index: true, element: <AdminHome /> },
          { path: "users", element: <UsersPage /> },
          // Ya no hay página de credenciales: cada clave se edita dentro de la
          // tarjeta de su servicio, en Conexiones → Servicios. El redirect se
          // queda para no romper marcadores antiguos.
          { path: "credentials", element: <Navigate to="/admin/connections?tab=services" replace /> },
          { path: "connections", element: <ConnectionsPage /> },
          // Ajustes generales de la app (zona horaria, etc.). La zona horaria
          // vivía dentro de Agentes → Modelo; ahora tiene página propia.
          { path: "settings", element: <SettingsPage /> },
          { path: "apariencia", element: <AparienciaPage /> },
          { path: "backups", element: <BackupsPage /> },
          { path: "agent/dashboard", element: <AgentDashboard /> },
          { path: "agent/flow", element: <SoloConModulo clave="flow_dashboard"><FlowDashboard /></SoloConModulo> },
          { path: "agent/agents", element: <AgentsPage /> },
          { path: "agent/seguimientos", element: <SeguimientosPage /> },
          { path: "agent/classifier", element: <Navigate to="/admin/agent/agents?tab=classifier" replace /> },
          { path: "outbound", element: <SoloConModulo clave="envio_masivo"><OutboundPage /></SoloConModulo> },
          // Ruta legacy: la gestión del prompt es ahora por-agente (AgentsPage).
          // Mantenemos un redirect para no romper marcadores antiguos.
          { path: "agent/prompt", element: <Navigate to="/admin/agent/agents" replace /> },
          // La antigua "Configuración modelo" es hoy la pestaña "Tarifas y
          // límites" de Agentes (?tab=model por compatibilidad); redirect
          // para no romper enlaces/marcadores antiguos.
          { path: "agent/config", element: <Navigate to="/admin/agent/agents?tab=model" replace /> },
          { path: "agent/learning", element: <LearningPage /> },
          { path: "audit", element: <AuditPage /> },
          { path: "health", element: <HealthPage /> },
          { path: "blocklist", element: <BlocklistPage /> },
          { path: "system/logs", element: <SystemLogs /> },
          { path: "system/novedades", element: <ChangelogPage /> },
          { path: "system/prompts", element: <PromptsPage /> },
          { path: "internal-agent", element: <Navigate to="/admin/agent/agents?tab=internal" replace /> },
          { path: "internal-agent/prompt", element: <SoloConModulo clave="agente_interno"><InternalAgentPromptPage /></SoloConModulo> },
        ],
      },
    ],
  },
  { path: "*", element: <NotFound /> },
]);
