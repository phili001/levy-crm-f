import { useEffect, useMemo, useRef, useState } from "react";
import {
  Boxes,
  Calendar,
  ChevronDown,
  Instagram,
  KeyRound,
  Loader2,
  Mail,
  MessageCircle,
  MessageSquare,
  Mic,
  Pencil,
  Phone,
  Plug,
  Server,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import { CopyPromptButton } from "@/components/CopyPromptButton";
import { InlineCredentials, CREDENTIAL_LABELS } from "@/components/CredentialFields";
import { apiBaseHttp } from "@/services/api";
import type {
  ChannelOut,
  CredentialOut,
  ExternalAPIOut,
  GoogleProvider,
  InstagramProfile,
} from "@/services/admin";
import { getInstagramProfile } from "@/services/admin";
import {
  CHANNEL_META,
  EstadoPill,
  QueFalta,
  SetupUrlsBlock,
  WHATSAPP_PROVIDERS,
  channelSetupUrls,
  clavesDelServicio,
  estadoDeTarjeta,
  whatsappProviderOf,
  type EstadoServicio,
  type SetupUrl,
  vaPorZernio,
} from "./shared";

/**
 * Servicios: TODO lo que hay que conectar y todas las claves, en un solo sitio.
 *
 * Aquí se conecta y aquí se editan las claves — las de WhatsApp, las de
 * Instagram, las de la voz, las de Google, las de Resend y las del correo
 * saliente. En Canales no hay ni una clave: esa pestaña es solo operación
 * (encendido, modo del bot y qué agente responde).
 *
 * Y dentro de esta pestaña vale la otra regla: un servicio, una tarjeta. Las
 * dos claves de Google llegaron a salir en cinco sitios distintos (Calendar,
 * Gmail, el canal de correo, "apps OAuth" y la lista del final) y no había
 * forma de saber cuál era el bueno. Son una sola app de Google y tienen un
 * solo sitio.
 */

// Claves que gestiona OTRA pantalla y que aquí solo meterían ruido.
const CLAVES_DE_OTRA_PANTALLA = (key: string) => key.startsWith("backup_s3_");

const SMTP_KEYS = ["smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from"];

// Todo lo que alguna tarjeta de Conexiones reclama como suyo. Lo que no esté
// aquí cae en "Otras claves", para que una credencial nueva del backend nunca
// se quede sin sitio donde editarla.
const CLAVES_CON_TARJETA = [
  ...WHATSAPP_PROVIDERS.ycloud.keys,
  ...WHATSAPP_PROVIDERS.meta.keys,
  "instagram_oauth_client_id",
  "instagram_oauth_client_secret",
  "google_oauth_client_id",
  "google_oauth_client_secret",
  "resend_api_key",
  "resend_from_email",
  "smtp_host",
  "smtp_port",
  "smtp_user",
  "smtp_password",
  "smtp_from",
];

export type ServicesTabProps = {
  channels: ChannelOut[];
  apis: ExternalAPIOut[];
  creds: CredentialOut[];
  onReloadCreds: () => void;
  startGoogleAuthFlow: (provider: GoogleProvider) => Promise<void> | void;
  /** Tarjeta a la que saltar al llegar desde Canales. */
  foco: string | null;
  onFocoAtendido: () => void;
  // Conectar cada servicio. Todo esto vivía en Canales y se ha mudado aquí.
  onOpenWhatsapp: () => void;
  onRotateWhatsappSecret: () => Promise<void> | void;
  rotandoWhatsappSecret: boolean;
  onProvisionWebchat: () => Promise<void> | void;
  provisioningWebchat: boolean;
  onShowWebchatSnippet: () => Promise<void> | void;
  onOpenInstagramManual: () => void;
  onInstagramOAuth: () => Promise<void> | void;
  onShowIgWebhookInfo: () => Promise<void> | void;
  /** Deja Instagram como recién instalado: fuera el canal y la autorización. */
  onDisconnectInstagram: () => void;
  onOpenRetell: () => void;
  /** Formulario del buzón de correo por IMAP/SMTP (Fase 5). */
  onOpenCorreo: () => void;
  /** Instagram y Messenger por Zernio (Fase 7). */
  onOpenZernio: () => void;
  onProvisionEmail: () => Promise<void> | void;
  provisioningEmail: boolean;
};

export function ServicesTab(p: ServicesTabProps) {
  const apiBase = apiBaseHttp().replace(/\/$/, "");
  const puestas = useMemo(
    () => new Set(p.creds.filter((c) => c.value_masked).map((c) => c.key)),
    [p.creds],
  );

  const canal = (tipo: string) => p.channels.find((c) => c.type === tipo) ?? null;
  const whatsappChannel = canal("whatsapp");
  const webchatChannel = canal("webchat");
  const instagramChannel = canal("instagram_dm");
  const emailChannel = canal("email");
  const correoImap = emailChannel?.config?.transport === "imap" ? emailChannel : null;
  const errorBuzon = correoImap ? String(correoImap.config?.imap_error ?? "") : "";
  const voiceChannel = canal("retell_voice");

  const calendarOk = p.apis.some((a) => a.provider === "google_calendar" && a.is_active);
  const gmailOk = p.apis.some((a) => a.provider === "google_gmail" && a.is_active);

  const otras = p.creds.filter(
    (c) => !CLAVES_CON_TARJETA.includes(c.key) && !CLAVES_DE_OTRA_PANTALLA(c.key),
  );

  // --- WhatsApp -------------------------------------------------------------
  const waProvider = whatsappProviderOf(whatsappChannel);
  const waKeys = clavesDelServicio("whatsapp", waProvider);
  const waFalta: string[] = [];
  const waSinPoner = waKeys.filter((k) => !puestas.has(k));
  // El secreto de firma de YCloud se cuenta aparte, porque «pégalo abajo, en
  // Claves» es un consejo imposible de seguir: YCloud solo lo enseña al CREAR
  // el aviso de mensajes nuevos, así que quien conectó sobre una cuenta que ya
  // lo tenía nunca lo ha visto. Lo que arregla ese caso es pedir uno nuevo.
  const faltaSecretoYcloud =
    waProvider === "ycloud" &&
    Boolean(whatsappChannel) &&
    !puestas.has("ycloud_webhook_secret");
  const waPorPegar = waSinPoner.filter(
    (k) => !(faltaSecretoYcloud && k === "ycloud_webhook_secret"),
  );
  if (waPorPegar.length) {
    waFalta.push(
      waPorPegar.length === 1
        ? `Falta una clave de ${WHATSAPP_PROVIDERS[waProvider].label}: la tienes abajo, en Claves.`
        : `Faltan ${waPorPegar.length} claves de ${WHATSAPP_PROVIDERS[waProvider].label}: las tienes abajo, en Claves.`,
    );
  }
  if (faltaSecretoYcloud) {
    waFalta.push(
      "Falta la clave con la que se comprueba que los mensajes los manda YCloud y no " +
        "cualquiera. Dale a «Rotar secreto del webhook»: el CRM le pide una nueva y se la " +
        "guarda. No hay que copiar nada de su consola.",
    );
  }
  if (!whatsappChannel) {
    waFalta.push("Da clic en «Conectar WhatsApp» para crear el canal con estas claves.");
  }

  // --- Instagram ------------------------------------------------------------
  const igKeys = clavesDelServicio("instagram_dm");
  // La autorización guardada del Business Login. Puede existir sin canal (y al
  // revés), así que se mira aparte para saber si hay algo que desconectar.
  const igApi = p.apis.find((a) => a.provider === "instagram_business") ?? null;
  const igFalta: string[] = [];
  const igPorZernio = vaPorZernio(instagramChannel);
  // Vía opcional: solo se pide terminarla a quien ya empezó (puso claves).
  if (!instagramChannel && igKeys.some((k) => puestas.has(k))) {
    igFalta.push("Entra con Instagram (o conéctalo a mano) para crear el canal.");
  }

  // --- Zernio (Instagram y Messenger) ---------------------------------------
  const canalesZernio = p.channels.filter((c) => vaPorZernio(c));
  const zernioFalta: string[] = [];
  if (!canalesZernio.length) {
    zernioFalta.push("Da clic en «Conectar con Zernio», pega tu API key y elige las cuentas.");
  } else {
    if (canalesZernio.some((c) => c.config?.zernio_estado === "disconnected")) {
      zernioFalta.push("Una cuenta se desconectó en Zernio (los permisos de Meta caducan): reconéctala allí y vuelve a conectar aquí.");
    }
    if (canalesZernio.some((c) => !c.config?.zernio_webhook_id)) {
      zernioFalta.push("Falta registrar el aviso de mensajes: vuelve a conectar, o pégalo a mano en Zernio.");
    }
  }

  // --- Google ---------------------------------------------------------------
  const googleKeys = ["google_oauth_client_id", "google_oauth_client_secret"];
  const googleUris: SetupUrl[] = [
    {
      label: "Para Calendar y Gmail",
      url: `${apiBase}/api/v1/oauth/google/callback`,
      donde:
        "Google Cloud Console → APIs y servicios → Credenciales → tu cliente de OAuth de tipo Web → URIs de redirección autorizados.",
    },
    {
      label: "Para entrar con Google",
      url: `${apiBase}/api/v1/auth/google/callback`,
      donde: "El mismo sitio y el mismo cliente: hay que agregar las dos, no basta con una.",
    },
  ];
  const googleFalta: string[] = [];
  const googleSinPoner = googleKeys.filter((k) => !puestas.has(k));
  // Si ya está autorizado, las claves no se reclaman: la app funciona y pedirlas
  // solo confunde (pueden venir del entorno de la instalación).
  if (googleSinPoner.length && !(calendarOk && gmailOk)) {
    googleFalta.push("Pega el identificador y el secreto de tu cliente de OAuth de Google Cloud.");
  }
  if (!calendarOk) googleFalta.push("Autoriza el calendario con el botón de abajo.");
  if (!gmailOk) googleFalta.push("Autoriza Gmail con el botón de abajo.");
  if (gmailOk && !emailChannel) {
    googleFalta.push("Crea el canal de correo con el botón de abajo para que entren los correos.");
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-w-4xl items-start">
      {/* ---------------------------------------------------------------- */}
      <TarjetaServicio
        id="whatsapp"
        prompt="whatsapp"
        foco={p.foco}
        onFocoAtendido={p.onFocoAtendido}
        titulo="WhatsApp"
        subtitulo={
          whatsappChannel
            ? String(whatsappChannel.config?.phone_number ?? "").trim() ||
              `Vía ${WHATSAPP_PROVIDERS[waProvider].short}`
            : "Todavía sin conectar"
        }
        descripcion={CHANNEL_META.whatsapp.description}
        Icon={Phone}
        tone={CHANNEL_META.whatsapp.tone}
        estado={estadoDeTarjeta(Boolean(whatsappChannel), waKeys, puestas, waFalta)}
        falta={waFalta}
        claves={waKeys}
        puestas={puestas}
        onReloadCreds={p.onReloadCreds}
        urls={channelSetupUrls("whatsapp", waProvider)}
        tituloUrls={`Pega esto en ${WHATSAPP_PROVIDERS[waProvider].panel}:`}
        preludio={
          <>
            {/* Con quién se conecta manda sobre todo lo demás de la tarjeta: qué
                claves se piden y a qué dirección escribe el proveedor. Por eso
                se lee antes que nada. */}
            <div className="flex items-center gap-2 text-xs">
              <Plug className="w-3.5 h-3.5 text-ink3 flex-none" />
              <span className="text-ink3">Se conecta con:</span>
              <span className="font-medium text-ink2">{WHATSAPP_PROVIDERS[waProvider].label}</span>
            </div>
            <div className="text-[11px] text-ink3 leading-relaxed">
              {WHATSAPP_PROVIDERS[waProvider].description}
            </div>
          </>
        }
      >
        <div className="flex justify-end gap-2 flex-wrap">
          {/* Solo YCloud: en Meta el secreto de firma es el App secret de su
              app, que se cambia en su panel y no por API. Y solo con canal
              creado, porque lo que se rota es el endpoint ya dado de alta. */}
          {whatsappChannel && waProvider === "ycloud" && (
            <button
              type="button"
              onClick={() => void p.onRotateWhatsappSecret()}
              disabled={p.rotandoWhatsappSecret}
              className="btn-ghost text-xs inline-flex items-center gap-1.5"
              title="Pide a YCloud un secreto de firma nuevo. Úsalo si al conectar te dijo que reutilizaba un webhook ya existente, o si crees que el secreto se filtró."
            >
              {p.rotandoWhatsappSecret ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <KeyRound className="w-3.5 h-3.5" />
              )}
              Rotar secreto del webhook
            </button>
          )}
          <button
            type="button"
            onClick={p.onOpenWhatsapp}
            className={`${whatsappChannel ? "btn-ghost" : "btn-primary"} text-xs inline-flex items-center gap-1.5`}
          >
            {whatsappChannel ? (
              <Pencil className="w-3.5 h-3.5" />
            ) : (
              <Plug className="w-3.5 h-3.5" />
            )}
            {whatsappChannel ? "Editar la conexión o cambiar de proveedor" : "Conectar WhatsApp"}
          </button>
        </div>
      </TarjetaServicio>

      {/* ---------------------------------------------------------------- */}
      <TarjetaServicio
        id="webchat"
        prompt="webchat"
        foco={p.foco}
        onFocoAtendido={p.onFocoAtendido}
        titulo="Chat en tu web"
        subtitulo={webchatChannel ? "Código listo para pegar" : "Todavía sin crear"}
        descripcion={CHANNEL_META.webchat.description}
        Icon={MessageSquare}
        tone={CHANNEL_META.webchat.tone}
        estado={webchatChannel ? "ok" : "sin-empezar"}
        falta={
          webchatChannel
            ? []
            : ["Crea el chat para obtener el código que se pega en tu web."]
        }
        claves={[]}
        puestas={puestas}
        onReloadCreds={p.onReloadCreds}
        preludio={
          <div className="text-[11px] text-ink3 leading-relaxed">
            No tiene claves secretas: su clave va dentro del HTML de tu web y es pública por diseño.
            Lo que impide que otra web use tu agente es la lista de dominios permitidos, que se
            gestiona en el mismo sitio que el código.
          </div>
        }
      >
        <div className="flex justify-end gap-2 flex-wrap">
          {!webchatChannel ? (
            <button
              type="button"
              onClick={() => void p.onProvisionWebchat()}
              disabled={p.provisioningWebchat}
              className="btn-primary text-xs inline-flex items-center gap-1.5"
            >
              {p.provisioningWebchat ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Plug className="w-3.5 h-3.5" />
              )}
              Crear el chat web
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={() => void p.onShowWebchatSnippet()}
                className="btn-primary text-xs inline-flex items-center gap-1.5"
              >
                <Pencil className="w-3.5 h-3.5" />
                Código y dominios
              </button>
              {/* ROTACIÓN de la clave, no "por si se filtra": la clave es
                  pública por diseño. Se rota para cortar las sesiones ya
                  abiertas (su huella va dentro del token de sesión). */}
              <button
                type="button"
                onClick={() => void p.onProvisionWebchat()}
                disabled={p.provisioningWebchat}
                className="btn-ghost text-xs inline-flex items-center gap-1.5"
                title="Rotar la clave: corta las sesiones abiertas y obliga a actualizar el código en todas las webs donde esté pegado"
              >
                {p.provisioningWebchat ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <KeyRound className="w-3.5 h-3.5" />
                )}
                Rotar clave
              </button>
            </>
          )}
        </div>
      </TarjetaServicio>

      {/* ---------------------------------------------------------------- */}
      <TarjetaServicio
        id="zernio"
        prompt="social"
        foco={p.foco}
        onFocoAtendido={p.onFocoAtendido}
        titulo="Instagram y Messenger · vía Zernio"
        subtitulo={
          canalesZernio.length
            ? canalesZernio
                .map((c) => `${c.type === "messenger" ? "Facebook" : "Instagram"} ${String(c.config?.zernio_cuenta ?? "")}`.trim())
                .join(" · ")
            : "Recomendada · todavía sin conectar"
        }
        descripcion="Tu cuenta de Zernio habla con Meta por ti: entras con tu usuario de Meta en Zernio, pegas aquí su API key y listo. Sin crear una app de Meta ni pasar su revisión. Gratis con dos cuentas."
        Icon={MessageCircle}
        tone="text-[#0084FF]"
        estado={canalesZernio.length ? (zernioFalta.length ? "medias" : "ok") : "sin-empezar"}
        falta={zernioFalta}
        claves={[]}
        puestas={puestas}
        onReloadCreds={p.onReloadCreds}
        urls={[]}
        preludio={
          <div className="text-[11px] text-ink3 leading-relaxed">
            Es una de las dos formas de conectar Instagram; la otra tarjeta de Instagram es la vía
            oficial, con tu propia app de Meta. Usa solo una: si conectas Instagram aquí, deja de
            entrar por la oficial. Messenger solo se conecta por aquí.
          </div>
        }
      >
        <div className="flex justify-end">
          <button
            type="button"
            onClick={p.onOpenZernio}
            className={`${canalesZernio.length && !zernioFalta.length ? "btn-ghost" : "btn-primary"} text-xs inline-flex items-center gap-1.5`}
          >
            <MessageCircle className="w-3.5 h-3.5" />
            {canalesZernio.length ? "Cambiar cuentas o reconectar" : "Conectar con Zernio"}
          </button>
        </div>
      </TarjetaServicio>

      {/* ---------------------------------------------------------------- */}
      <TarjetaServicio
        id="instagram_dm"
        prompt="meta"
        foco={p.foco}
        onFocoAtendido={p.onFocoAtendido}
        titulo="Instagram · vía oficial de Meta"
        subtitulo={
          igPorZernio
            ? "Avanzada · sin usar: tu Instagram entra vía Zernio"
            : instagramChannel
              ? `Avanzada · ${instagramChannel.name}`
              : "Avanzada · todavía sin conectar"
        }
        descripcion="Con tu propia app en Meta for Developers: claves de la app, el webhook de Meta y su revisión antes de atender a clientes reales. Solo si ya la tienes montada; si no, la vía Zernio es más sencilla."
        Icon={Instagram}
        tone={CHANNEL_META.instagram_dm.tone}
        estado={igPorZernio ? "sin-empezar" : estadoDeTarjeta(Boolean(instagramChannel), igKeys, puestas, igFalta)}
        falta={igPorZernio ? [] : igFalta}
        claves={igKeys}
        puestas={puestas}
        onReloadCreds={p.onReloadCreds}
        urls={igPorZernio ? [] : channelSetupUrls("instagram_dm")}
        tituloUrls="Pega esto en el panel de Meta:"
        urlsExtra={
          instagramChannel ? (
            <button
              type="button"
              onClick={() => void p.onShowIgWebhookInfo()}
              className="text-[11px] text-brand-ink underline hover:no-underline"
            >
              Ver también la palabra de verificación y los pasos en Meta
            </button>
          ) : null
        }
        preludio={
          <div className="space-y-2">
            {!igPorZernio && <CuentaInstagramConectada conectado={Boolean(instagramChannel)} />}
            <div className="text-[11px] text-ink3 leading-relaxed">
              {igPorZernio
                ? "Instagram ya entra vía Zernio, así que esta tarjeta no se usa. Conectar aquí lo devolvería a tu propia app de Meta."
                : "Dentro de esta vía, entrar con Instagram evita pegar el token a mano. Las claves de abajo son las de tu app de Meta."}
            </div>
          </div>
        }
      >
        <div className="flex justify-end gap-2 flex-wrap">
          <button
            type="button"
            onClick={() => void p.onInstagramOAuth()}
            // Vía avanzada: nunca el botón principal, para no competir con Zernio.
            className="btn-ghost text-xs inline-flex items-center gap-1.5"
            title={
              instagramChannel
                ? "Volver a autorizar con Instagram (token nuevo de 60 días)"
                : "Conectar entrando con tu cuenta de Instagram (recomendado)"
            }
          >
            <Instagram className="w-3.5 h-3.5" />
            {instagramChannel && !igPorZernio ? "Volver a autorizar" : "Conectar vía oficial"}
          </button>
          {(!instagramChannel || igPorZernio) && (
            <button
              type="button"
              onClick={p.onOpenInstagramManual}
              className="btn-ghost text-xs inline-flex items-center gap-1.5"
              title="Conectar pegando el token a mano (requiere una app de Meta con página de Facebook)"
            >
              A mano
            </button>
          )}
          {(instagramChannel || igApi) && (
            <button
              type="button"
              onClick={p.onDisconnectInstagram}
              className="btn-ghost text-xs inline-flex items-center gap-1.5 text-state-bad hover:text-state-bad"
              title="Quita el canal y la autorización guardada, para volver a empezar de cero"
            >
              <Trash2 className="w-3.5 h-3.5" />
              Desconectar
            </button>
          )}
        </div>
      </TarjetaServicio>

      {/* ---------------------------------------------------------------- */}
      <TarjetaServicio
        id="retell_voice"
        prompt="voz"
        foco={p.foco}
        onFocoAtendido={p.onFocoAtendido}
        titulo="Llamadas de voz (Retell)"
        subtitulo={
          voiceChannel
            ? [voiceChannel.name, voiceChannel.config?.phone_number].filter(Boolean).join(" · ")
            : "Todavía sin conectar"
        }
        descripcion={CHANNEL_META.retell_voice.description}
        Icon={Mic}
        tone={CHANNEL_META.retell_voice.tone}
        estado={voiceChannel ? "ok" : "sin-empezar"}
        falta={
          voiceChannel
            ? []
            : ["Da clic en «Conectar la voz»: con tu clave de Retell, el CRM crea el agente por ti."]
        }
        claves={[]}
        puestas={puestas}
        onReloadCreds={p.onReloadCreds}
        // Un agente que creó el CRM ya apunta aquí: no hay nada que pegar.
        urls={voiceChannel?.config?.agente_gestionado ? [] : channelSetupUrls("retell_voice")}
        tituloUrls="Pega esto en el panel de Retell:"
        preludio={
          <div className="text-[11px] text-ink3 leading-relaxed">
            Sus claves no se editan de una en una: se guardan todas de golpe en el mismo formulario
            con el que se conecta. Al editar, lo que dejes en blanco se queda como estaba.
          </div>
        }
      >
        <div className="flex justify-end">
          <button
            type="button"
            onClick={p.onOpenRetell}
            className={`${voiceChannel ? "btn-ghost" : "btn-primary"} text-xs inline-flex items-center gap-1.5`}
          >
            {voiceChannel ? <KeyRound className="w-3.5 h-3.5" /> : <Mic className="w-3.5 h-3.5" />}
            {voiceChannel ? "Editar las claves de Retell" : "Conectar la voz"}
          </button>
        </div>
      </TarjetaServicio>

      {/* ---------------------------------------------------------------- */}
      <TarjetaServicio
        id="correo"
        prompt="correo"
        foco={p.foco}
        onFocoAtendido={p.onFocoAtendido}
        titulo="Correo"
        subtitulo={correoImap ? String(correoImap.config?.usuario ?? correoImap.name) : "Todavía sin conectar"}
        descripcion="El buzón de la clínica (Gmail, Hostinger, cPanel, Zoho…) con una contraseña de aplicación. Sofía contesta en el mismo hilo."
        Icon={Mail}
        tone="text-[#EA4335]"
        estado={errorBuzon ? "medias" : correoImap ? "ok" : "sin-empezar"}
        falta={
          errorBuzon
            ? [`No entran correos: ${errorBuzon} → Edita el buzón y vuelve a probar.`]
            : correoImap
              ? []
              : ["Da clic en «Conectar el buzón» y prueba la conexión antes de guardar."]
        }
        claves={[]}
        puestas={puestas}
        onReloadCreds={p.onReloadCreds}
        urls={[]}
      >
        <div className="flex justify-end">
          <button
            type="button"
            onClick={p.onOpenCorreo}
            className={`${correoImap && !errorBuzon ? "btn-ghost" : "btn-primary"} text-xs inline-flex items-center gap-1.5`}
          >
            <Mail className="w-3.5 h-3.5" />
            {correoImap ? "Editar el buzón" : "Conectar el buzón"}
          </button>
        </div>
      </TarjetaServicio>

      {/* ---------------------------------------------------------------- */}
      <TarjetaServicio
        id="google"
        prompt="google"
        foco={p.foco}
        onFocoAtendido={p.onFocoAtendido}
        titulo="Google"
        subtitulo="Calendario, correo y entrar con Google"
        descripcion="Una sola app de Google Cloud sirve para las tres cosas: las citas del agente, leer el correo de Gmail y el botón de entrar con Google."
        Icon={Calendar}
        tone="text-[#4285F4]"
        estado={estadoDeTarjeta(calendarOk || gmailOk, googleKeys, puestas, googleFalta)}
        falta={googleFalta}
        claves={googleKeys}
        puestas={puestas}
        onReloadCreds={p.onReloadCreds}
        urls={googleUris}
        tituloUrls="Pega estas direcciones de vuelta en tu cliente de OAuth:"
      >
        <div className="flex flex-wrap gap-2 justify-end">
          <BotonConectar
            etiqueta="Calendario"
            conectado={calendarOk}
            onClick={() => void p.startGoogleAuthFlow("google_calendar")}
          />
          <BotonConectar
            etiqueta="Gmail"
            conectado={gmailOk}
            onClick={() => void p.startGoogleAuthFlow("google_gmail")}
          />
          {/* El canal de correo no tiene claves propias: se crea desde aquí
              porque lo único que necesita es esta conexión de Google. */}
          <button
            type="button"
            onClick={() => void p.onProvisionEmail()}
            disabled={p.provisioningEmail || !gmailOk || Boolean(emailChannel)}
            className="btn-ghost text-xs inline-flex items-center gap-1.5 disabled:opacity-40"
            title={
              emailChannel
                ? "El canal de correo ya está creado: se opera desde la pestaña Canales"
                : gmailOk
                  ? "Crear el canal de correo entrante con esta cuenta de Gmail"
                  : "Antes hay que autorizar Gmail aquí arriba"
            }
          >
            {p.provisioningEmail ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Mail className="w-3.5 h-3.5" />
            )}
            {correoImap
              ? "El correo va por buzón"
              : emailChannel
                ? "Correo conectado"
                : "Conectar el correo (Gmail, avanzado)"}
          </button>
        </div>
      </TarjetaServicio>

      {/* ---------------------------------------------------------------- */}
      <TarjetaServicio
        id="resend"
        foco={p.foco}
        onFocoAtendido={p.onFocoAtendido}
        titulo="Resend"
        subtitulo="Correos del sistema"
        descripcion="Los avisos que manda la propia app: resúmenes, alertas y recuperar la contraseña. No es el correo del agente."
        Icon={Mail}
        tone="text-[#16A085]"
        estado={estadoDeTarjeta(
          puestas.has("resend_api_key") && puestas.has("resend_from_email"),
          ["resend_api_key", "resend_from_email"],
          puestas,
        )}
        falta={
          puestas.has("resend_api_key") && puestas.has("resend_from_email")
            ? []
            : ["Pega la clave de API de Resend y la dirección desde la que salen los correos."]
        }
        claves={["resend_api_key", "resend_from_email"]}
        puestas={puestas}
        onReloadCreds={p.onReloadCreds}
      />

      {/* ---------------------------------------------------------------- */}
      <TarjetaServicio
        id="smtp"
        foco={p.foco}
        onFocoAtendido={p.onFocoAtendido}
        titulo="Correo saliente (SMTP)"
        subtitulo="Alternativa a Resend"
        descripcion="Mandar los correos por un servidor de correo normal, con una contraseña de aplicación. Sirve si no quieres usar Resend."
        Icon={Server}
        tone="text-[#EA4335]"
        estado={estadoDeTarjeta(
          SMTP_KEYS.every((k) => puestas.has(k)),
          SMTP_KEYS,
          puestas,
        )}
        falta={[]}
        claves={SMTP_KEYS}
        puestas={puestas}
        onReloadCreds={p.onReloadCreds}
      />

      {/* Red de seguridad: si el backend añade una clave y todavía no tiene
          tarjeta, aparece aquí en vez de quedarse sin sitio donde editarla.
          Normalmente esta tarjeta no se ve. */}
      {otras.length > 0 && (
        <TarjetaServicio
          id="otras"
          foco={p.foco}
          onFocoAtendido={p.onFocoAtendido}
          titulo="Otras claves"
          subtitulo="Sin servicio asignado"
          descripcion="Claves guardadas que todavía no pertenecen a ninguna tarjeta de esta pantalla."
          Icon={Boxes}
          tone="text-ink3"
          estado="medias"
          falta={[]}
          claves={otras.map((c) => c.key)}
          puestas={puestas}
          onReloadCreds={p.onReloadCreds}
        />
      )}
    </div>
  );
}

/**
 * Qué cuenta de Instagram está conectada: su foto y su @usuario.
 *
 * Antes la tarjeta solo ponía "Instagram DM" y no había forma de saber a qué
 * cuenta habías entrado. Además Meta lo exige para aprobar la app: el panel
 * tiene que ENSEÑAR el perfil de la cuenta que acaba de conectarse.
 *
 * La foto es una URL del CDN de Meta y caduca, así que se pide al abrir la
 * pantalla en vez de guardarse. Si no llega, queda la inicial del @usuario.
 */
function CuentaInstagramConectada({ conectado }: { conectado: boolean }) {
  const [perfil, setPerfil] = useState<InstagramProfile | null>(null);
  const [fotoRota, setFotoRota] = useState(false);

  useEffect(() => {
    if (!conectado) {
      setPerfil(null);
      return;
    }
    let vivo = true;
    getInstagramProfile()
      .then((r) => {
        if (vivo) {
          setPerfil(r);
          setFotoRota(false);
        }
      })
      // Sin cuenta conectada por OAuth (o Meta sin contestar) no hay ficha que
      // pintar: la tarjeta sigue funcionando igual.
      .catch(() => undefined);
    return () => {
      vivo = false;
    };
  }, [conectado]);

  if (!perfil?.connected) return null;
  const arroba = perfil.username ? `@${perfil.username}` : "Cuenta conectada";
  const foto = perfil.profile_picture_url && !fotoRota ? perfil.profile_picture_url : null;

  return (
    <div className="flex items-center gap-2.5 rounded-coro-sm border border-line2 bg-paper2 px-2.5 py-2">
      {foto ? (
        <img
          src={foto}
          alt={arroba}
          className="w-9 h-9 rounded-full object-cover shrink-0"
          onError={() => setFotoRota(true)}
        />
      ) : (
        <div className="w-9 h-9 rounded-full bg-paper3 flex items-center justify-center shrink-0">
          <Instagram className="w-4 h-4 text-ink3" />
        </div>
      )}
      <div className="min-w-0">
        <div className="text-[13px] font-medium text-ink truncate">{arroba}</div>
        <div className="text-[11px] text-ink3 truncate">
          {perfil.name ? `${perfil.name} · ` : ""}Cuenta conectada
        </div>
      </div>
    </div>
  );
}

function BotonConectar({
  etiqueta,
  conectado,
  onClick,
}: {
  etiqueta: string;
  conectado: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`${conectado ? "btn-ghost" : "btn-primary"} text-xs inline-flex items-center gap-1.5`}
      title={
        conectado
          ? "Ya está autorizado. Da clic para volver a autorizar (por ejemplo, si caducó)"
          : "Autorizar con tu cuenta de Google"
      }
    >
      <Plug className="w-3.5 h-3.5" />
      {conectado ? `${etiqueta} · autorizado` : `Autorizar ${etiqueta}`}
    </button>
  );
}

function TarjetaServicio({
  id,
  foco,
  onFocoAtendido,
  titulo,
  subtitulo,
  descripcion,
  Icon,
  tone,
  estado,
  falta,
  claves,
  puestas,
  onReloadCreds,
  urls,
  tituloUrls,
  urlsExtra,
  preludio,
  prompt,
  children,
}: {
  id: string;
  foco: string | null;
  onFocoAtendido: () => void;
  titulo: string;
  subtitulo: string;
  descripcion: string;
  Icon: LucideIcon;
  tone: string;
  estado: EstadoServicio;
  falta: string[];
  claves: string[];
  puestas: Set<string>;
  onReloadCreds: () => void;
  urls?: SetupUrl[];
  tituloUrls?: string;
  urlsExtra?: React.ReactNode;
  /** Lo que hay que leer ANTES de las claves: con quién se conecta el
   * servicio y qué claves va a pedir por eso. Va arriba porque manda sobre
   * todo lo demás de la tarjeta. */
  preludio?: React.ReactNode;
  /** El prompt del catálogo que conecta este servicio con Claude Code (su
   * `canal` en `backend/prompts/`). Sin él, la tarjeta no lleva el botón. */
  prompt?: string;
  children?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const resaltada = foco === id;

  // Al llegar desde Canales ("aquí se arregla esto"), la tarjeta se trae a la
  // vista y se marca un momento: sin esto, el salto deja al usuario mirando una
  // pantalla de tarjetas iguales sin saber cuál era la suya.
  useEffect(() => {
    if (!resaltada) return;
    ref.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    const t = window.setTimeout(onFocoAtendido, 2500);
    return () => window.clearTimeout(t);
  }, [resaltada, onFocoAtendido]);

  const n = claves.filter((k) => puestas.has(k)).length;
  return (
    <div
      ref={ref}
      id={`servicio-${id}`}
      className={`border rounded-coro bg-card p-4 flex flex-col gap-3 transition-shadow ${
        resaltada ? "border-brand-ink shadow-coro-2" : "border-line"
      }`}
    >
      <div className="flex items-start gap-3">
        <div className={`w-9 h-9 rounded-coro-sm bg-paper2 flex items-center justify-center ${tone}`}>
          <Icon className="w-4 h-4" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-medium text-ink leading-snug">{titulo}</div>
          <div className="text-xs text-ink3 mt-0.5">{subtitulo}</div>
        </div>
        <EstadoPill estado={estado} />
      </div>
      <div className="text-xs text-ink3">{descripcion}</div>
      {prompt && (
        <div className="-mt-1">
          <CopyPromptButton canal={prompt} className="-ml-2" />
        </div>
      )}
      {preludio}
      <QueFalta items={falta} />
      {urls && urls.length > 0 && (
        <SetupUrlsBlock urls={urls} titulo={tituloUrls}>
          {urlsExtra}
        </SetupUrlsBlock>
      )}
      {claves.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="w-full flex items-center gap-1.5 text-xs text-ink2 hover:text-ink py-1"
          >
            <KeyRound className="w-3.5 h-3.5 text-ink3 flex-none" />
            <span className="font-medium">Claves</span>
            <span className="text-ink3">
              ({n}/{claves.length})
            </span>
            <ChevronDown
              className={`w-3.5 h-3.5 text-ink4 ml-auto transition-transform ${open ? "rotate-180" : ""}`}
            />
          </button>
          {open && (
            <div className="mt-1.5">
              <InlineCredentials keys={claves} onSaved={onReloadCreds} />
            </div>
          )}
          {/* El recordatorio de lo que falta solo cuando falta de verdad: si el
              servicio ya funciona (por ejemplo Instagram conectado entrando con
              tu cuenta, sin pegar claves), decir "Falta" es mentira y asusta. */}
          {!open && n < claves.length && estado !== "ok" && (
            <div className="text-[10px] text-ink3">
              Falta: {claves.filter((k) => !puestas.has(k)).map((k) => CREDENTIAL_LABELS[k] || k).join(", ")}
            </div>
          )}
        </div>
      )}
      {children}
    </div>
  );
}
