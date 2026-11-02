import { ReactNode, useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/store/auth";
import type { UserRole } from "@/types";

interface Props {
  children: ReactNode;
  role?: UserRole;
}

export function ProtectedRoute({ children, role }: Props) {
  const token = useAuth((s) => s.token);
  const user = useAuth((s) => s.user);
  const fetchMe = useAuth((s) => s.fetchMe);
  const location = useLocation();

  useEffect(() => {
    if (token && !user) {
      void fetchMe();
    }
  }, [token, user, fetchMe]);

  if (!token) return <Navigate to="/login" replace />;
  // Contraseña de la instalación todavía puesta: al perfil, y de ahí no se
  // sale. No se le manda al login (entraría con la misma y volvería aquí en
  // bucle) ni se le deja navegar: el backend le contestaría 403 en cada
  // pantalla y parecería que el panel está roto.
  if (user?.debe_cambiar_password && location.pathname !== "/profile") {
    return <Navigate to="/profile" replace />;
  }
  if (role && user && user.role !== role) {
    return <Navigate to="/inbox" replace />;
  }
  return <>{children}</>;
}
