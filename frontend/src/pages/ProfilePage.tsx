import { useState } from "react";
import { Save } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { PASSWORD_HINT, PASSWORD_MIN_LENGTH, PASSWORD_RULE_ERROR } from "@/lib/password";
import { useAuth } from "@/store/auth";

function errText(e: unknown): string {
  const ax = e as { response?: { data?: { detail?: string } } };
  return ax.response?.data?.detail || "Error al guardar.";
}

export default function ProfilePage() {
  const user = useAuth((s) => s.user);
  const updateProfile = useAuth((s) => s.updateProfile);

  const [nombre, setNombre] = useState(user?.nombre || "");
  const [email, setEmail] = useState(user?.email || "");
  const [savingInfo, setSavingInfo] = useState(false);
  const [infoMsg, setInfoMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const [curPass, setCurPass] = useState("");
  const [newPass, setNewPass] = useState("");
  const [savingPass, setSavingPass] = useState(false);
  const [passMsg, setPassMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function saveInfo() {
    setSavingInfo(true);
    setInfoMsg(null);
    try {
      await updateProfile({ nombre: nombre.trim() || null, email: email.trim().toLowerCase() });
      setInfoMsg({ ok: true, text: "Perfil actualizado." });
    } catch (e) {
      setInfoMsg({ ok: false, text: errText(e) });
    } finally {
      setSavingInfo(false);
    }
  }

  async function savePass() {
    if (newPass.length < PASSWORD_MIN_LENGTH) {
      setPassMsg({ ok: false, text: PASSWORD_RULE_ERROR });
      return;
    }
    setSavingPass(true);
    setPassMsg(null);
    try {
      await updateProfile({ current_password: curPass, new_password: newPass });
      setCurPass("");
      setNewPass("");
      setPassMsg({ ok: true, text: "Contraseña actualizada." });
    } catch (e) {
      setPassMsg({ ok: false, text: errText(e) });
    } finally {
      setSavingPass(false);
    }
  }

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        eyebrow="Tu cuenta"
        title="Mi perfil"
        description="Edita tus datos y tu contraseña. El rol solo lo puede cambiar un administrador."
      />
      <div className="flex-1 overflow-auto p-4 md:p-6 max-w-xl space-y-4">
        {user?.debe_cambiar_password && (
          <div className="card border-amber-500/60 bg-amber-500/10 p-5 space-y-1">
            <div className="font-semibold">Cambia tu contraseña para empezar</div>
            <p className="text-sm text-muted-foreground">
              Entraste con la contraseña que se puso al instalar el sistema. Esa
              la puede ver cualquiera que tenga acceso al servidor, así que el
              resto del panel espera hasta que la cambies aquí abajo.
            </p>
          </div>
        )}
        <div className="card p-5 space-y-3">
          <div className="eyebrow">Datos</div>
          <div>
            <label className="label">Nombre</label>
            <input className="input" value={nombre} onChange={(e) => setNombre(e.target.value)} placeholder="Tu nombre" />
          </div>
          <div>
            <label className="label">Email</label>
            <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div>
            <label className="label">Rol</label>
            <div className="text-ink2 text-sm">{user?.role}</div>
          </div>
          {infoMsg && (
            <div className={infoMsg.ok ? "text-state-ok text-sm" : "text-state-bad text-sm"}>{infoMsg.text}</div>
          )}
          <div className="flex justify-end">
            <button className="btn-primary" onClick={saveInfo} disabled={savingInfo}>
              <Save /> {savingInfo ? "Guardando…" : "Guardar"}
            </button>
          </div>
        </div>

        <div className="card p-5 space-y-3">
          <div className="eyebrow">Cambiar contraseña</div>
          <div>
            <label className="label">Contraseña actual</label>
            <input className="input" type="password" autoComplete="current-password" value={curPass} onChange={(e) => setCurPass(e.target.value)} />
          </div>
          <div>
            <label className="label">Nueva contraseña</label>
            <input
              className="input"
              type="password"
              autoComplete="new-password"
              minLength={PASSWORD_MIN_LENGTH}
              value={newPass}
              onChange={(e) => setNewPass(e.target.value)}
              placeholder={PASSWORD_HINT}
            />
          </div>
          {passMsg && (
            <div className={passMsg.ok ? "text-state-ok text-sm" : "text-state-bad text-sm"}>{passMsg.text}</div>
          )}
          <div className="flex justify-end">
            <button className="btn-primary" onClick={savePass} disabled={savingPass || !curPass || !newPass}>
              <Save /> {savingPass ? "Guardando…" : "Cambiar contraseña"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
