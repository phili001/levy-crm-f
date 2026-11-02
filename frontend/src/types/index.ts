// revisor = cuenta de solo mirar para quien revisa la app en Meta: la bandeja
// de Instagram y la ficha de la cuenta conectada, nada más.
export type UserRole = "admin" | "cliente" | "revisor";

export interface User {
  id: string;
  email: string;
  role: UserRole;
  nombre: string | null;
  activo: boolean;
  last_login?: string | null;
  // El administrador inicial entra con la contraseña que venía en las
  // variables del despliegue, que ha visto cualquiera con acceso al panel del
  // servidor. Mientras esto sea true, la sesión solo sirve para cambiarla: el
  // backend responde 403 a todo lo demás.
  debe_cambiar_password?: boolean;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export type ConversationStatus = "bot" | "humano" | "cerrada";
export type ConversationCanal =
  | "whatsapp"
  | "web"
  | "instagram_dm"
  | "email"
  | "retell_voice"
  | "messenger";

export interface Contact {
  id: string;
  telefono: string;
  email: string | null;
  nombre: string | null;
  // Instagram: el @usuario, para mostrarlo bajo el nombre real en la bandeja.
  social_handle?: string | null;
  estado: string;
  origen: string;
  servicio_interes: string | null;
  // Ficha CRM (todos opcionales).
  empresa?: string | null;
  cargo?: string | null;
  web?: string | null;
  nif?: string | null;
  direccion?: string | null;
  ultimo_mensaje_at: string | null;
  notas_internas: string | null;
  in_crm?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  // Fase 10.1, solo lectura: el teléfono que dio en un canal que no lo trae
  // (no es la llave) y la cuenta de canal que se unió a esta ficha.
  telefono_declarado?: string | null;
  identidad_canal?: string | null;
}

/** Una de las dos fichas de una propuesta «¿Es el mismo paciente?». */
export interface FichaDePropuesta {
  id: string;
  nombre: string | null;
  telefono: string;
  social_handle: string | null;
  // De qué canal es la ficha, ya en texto: "WhatsApp", "Instagram"…
  canal: string;
}

/** Propuesta de unir dos fichas del mismo paciente (Fase 10.1). */
export interface PropuestaFusion {
  id: string;
  motivo: "telefono" | "email";
  // El teléfono o el correo que coincidió.
  dato: string;
  // `absorbe` desaparece al unirlas; `queda` se queda con todo.
  absorbe: FichaDePropuesta;
  queda: FichaDePropuesta;
  telefono_final: string;
  citas_activas: number;
  // Por qué no se puede unir ahora (el botón sale desactivado).
  bloqueo: string | null;
  created_at: string;
}

export interface Tag {
  id: string;
  nombre: string;
  color: string;
}

export interface ContactNoteAuthor {
  id: string;
  nombre: string | null;
  role: UserRole;
}

export interface ContactNote {
  id: string;
  texto: string;
  created_at: string;
  author: ContactNoteAuthor | null;
}

// Actor de un evento de actividad (mismo shape que el autor de una nota).
// null = sistema/agente (p. ej. conversación iniciada por el cliente).
export interface ContactActivityActor {
  id: string;
  nombre: string | null;
  role: UserRole;
}

// Un evento del timeline de actividad de la ficha. `meta` es flexible: payload
// pequeño SIN PII que varía según `tipo` (estados, nombre de etiqueta, canal,
// ids). El frontend lo interpreta para componer una etiqueta legible.
export interface ContactActivity {
  id: string;
  tipo: string;
  meta: Record<string, unknown> | null;
  created_at: string;
  actor: ContactActivityActor | null;
}

export interface Conversation {
  id: string;
  contact_id: string;
  contact?: Contact;
  canal: ConversationCanal;
  status: ConversationStatus;
  demo_active?: boolean;
  archived?: boolean;
  quarantined?: boolean;
  quarantine_reason?: string | null;
  started_at: string;
  ended_at: string | null;
  last_message_at: string | null;
  resumen: string | null;
  // Asunto del correo (canal Email, F5a). NULL en el resto de canales.
  subject?: string | null;
  // Canal Voz (Retell) — metadatos de la llamada (sección "Llamadas"). NULL en
  // el resto de canales. El fin de la llamada llega en `ended_at` y el resumen
  // en `resumen`.
  call_duration_seconds?: number | null;
  // URL de la grabación en Retell. Se reproduce/enlaza directamente (no se
  // descarga). Puede caducar (es un enlace de Retell).
  call_recording_url?: string | null;
  call_ended_reason?: string | null;
  last_message_preview?: string | null;
  // 5d — True si el hilo tiene un borrador del agente sin enviar. La lista
  // pinta una etiqueta "Borrador".
  has_pending_draft?: boolean;
  // Sugerencia del modo Entrenamiento sin resolver (pestaña "Sugerencias").
  has_training_suggestion?: boolean;
  unread_count?: number;
  // Señal "pendiente": necesita acción de una persona (sugerencia sin resolver,
  // cliente sin contestar, o derivada a humano sin respuesta de un operador).
  needs_action?: boolean;
  // Fase 10.1: la ficha tiene una propuesta «¿Es el mismo paciente?» sin
  // resolver. Está en «Para hacer», pero no es un cliente esperando.
  fusion_pendiente?: boolean;
  tags?: Tag[];
}

export type MessageRole = "user" | "assistant" | "operator" | "system";

export type MediaKind = "image" | "audio" | "video" | "document" | "sticker";

export interface Message {
  id: string;
  conversation_id: string;
  rol: MessageRole;
  contenido: string | null;
  audio_url: string | null;
  audio_transcript: string | null;
  created_at: string;
  metadata?: Record<string, unknown>;
  media_type?: MediaKind | null;
  // Ruta relativa al endpoint protegido `/api/v1/uploads/{filename}`.
  // El frontend la usa con auth (token JWT en header) para mostrar el adjunto.
  media_url?: string | null;
  media_mime?: string | null;
  media_size?: number | null;
  media_filename?: string | null;
  media_duration_seconds?: number | null;
  // F5c — Borrador del agente para email. Si is_draft && !draft_sent, el panel
  // lo muestra como tarjeta editable con botón "Enviar" (no es un mensaje ya
  // enviado, sino una propuesta pendiente de revisión humana).
  is_draft?: boolean;
  draft_sent?: boolean;
  gmail_draft_id?: string | null;
  // Modo Entrenamiento (sombra) — true si la sugerencia la generó el agente en
  // un canal NO email mientras el canal estaba en Entrenamiento. Usa la misma
  // tarjeta de borrador, pero la copy habla de "sugerencia" y al enviar se
  // enruta por el canal (no por Gmail).
  training?: boolean;
  // 5d — HTML del correo (canal Email) para renderizado seguro con DOMPurify.
  // NULL/ausente en mensajes sin HTML; el frontend cae a `contenido`.
  html_body?: string | null;
  // Retención email — True si el contenido se purgó de la BD (a los 6 meses).
  // El panel muestra un stub "Contenido archivado" con botón para recuperar el
  // cuerpo desde Gmail bajo demanda (no se vuelve a persistir).
  purged?: boolean;
  // Instagram — true si quien lo envió lo retiró después ("deshacer envío").
  // El contenido ya no está guardado (se borra al recibir el aviso de Meta):
  // la burbuja se pinta como "Mensaje eliminado". `deleted_at` dice cuándo.
  is_deleted?: boolean;
  deleted_at?: string | null;
  // Entrega en WhatsApp. Que el proveedor acepte un mensaje no significa que el
  // cliente lo reciba: Meta responde 200 y minutos después avisa por webhook de
  // que no ha podido entregarlo (número que no existe, cliente que bloqueó a la
  // empresa, ventana de 24 h cerrada, plantilla pausada). Ausente en los canales
  // sin acuse (web, correo, Instagram) y en los mensajes anteriores a esto.
  delivery_status?: "enviado" | "entregado" | "leido" | "fallido" | null;
  // Por qué no llegó. Solo viene con delivery_status === "fallido".
  delivery_detail?: string | null;
}

export interface Document {
  id: string;
  nombre: string;
  formato: string;
  tamano_bytes: number;
  status: "procesando" | "indexado" | "error";
  error_msg: string | null;
  num_chunks: number;
  uploaded_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

// Agenda propia (Fase 9). Las horas `*_local` llegan ya en la hora del negocio
// ("AAAA-MM-DDTHH:MM"): la recepción puede estar en otro país, y pintar con la
// hora del navegador movería las citas.
export type AppointmentEstado =
  | "agendada"
  | "reagendada"
  | "cancelada"
  | "completada"
  | "no_asistio";

export interface Appointment {
  id: string;
  contact_id: string;
  contact_nombre: string | null;
  contact_telefono: string;
  conversation_id: string | null;
  canal: string | null;
  servicio: string;
  starts_at: string;
  ends_at: string;
  inicio_local: string;
  fin_local: string;
  duracion_min: number;
  estado: AppointmentEstado;
  activa: boolean;
  notas: string | null;
  creada_por: "agente" | "operador";
  en_google: boolean;
  google_sync_pendiente: boolean;
  created_at: string;
  // Recordatorios y seguimientos (SPEC-18).
  confirmada_at?: string | null;
  recordatorio_enviado_at?: string | null;
  inasistencia_enviada_at?: string | null;
  avisos?: Partial<Record<"recordatorio" | "inasistencia", AvisoDeCita>>;
}

// Cómo acabó un aviso automático de la cita (SPEC-18).
export interface AvisoDeCita {
  resultado: "enviado" | "omitido";
  canal?: string;
  motivo?: string;
}

export interface ScheduleBlock {
  id: string;
  starts_at: string;
  ends_at: string;
  inicio_local: string;
  fin_local: string;
  motivo: string | null;
}

export interface AgendaServicio {
  nombre: string;
  duracion_min: number;
}

export interface AgendaVista {
  zona: string;
  hoy: string;
  ahora_local: string;
  /** isoweekday: 1 = lunes … 7 = domingo. */
  work_days: number[];
  work_hours: string[];
  holidays: string[];
  slot_step_min: number;
  servicios: AgendaServicio[];
  citas: Appointment[];
  bloqueos: ScheduleBlock[];
}
