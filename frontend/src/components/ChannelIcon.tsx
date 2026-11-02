/**
 * Marcas de los canales, en SVG propio.
 *
 * Por qué no una librería: lucide quitó los logos de marca hace tiempo, y
 * meter `simple-icons` entero por cuatro glifos son cientos de KB para lo
 * mismo. Son cuatro paths, viven aquí y no dependen de nadie.
 *
 * Los logos se usan para DECIR DE DÓNDE VIENE una conversación — uso
 * nominativo, igual que el punto verde de WhatsApp en la lista de canales.
 * Correo, teléfono y web no son marcas de nadie y van con lucide.
 */
import { Globe, Mail, Phone } from "lucide-react";

export type CanalId =
  | "whatsapp"
  | "instagram_dm"
  | "messenger"
  | "email"
  | "retell_voice"
  | "webchat";

type Props = { className?: string };

function WhatsAppMark({ className }: Props) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M17.47 14.38c-.3-.15-1.76-.87-2.03-.97-.27-.1-.47-.15-.67.15-.2.3-.77.96-.94 1.16-.17.2-.35.22-.65.07-.3-.15-1.26-.46-2.4-1.48-.89-.79-1.49-1.76-1.66-2.06-.17-.3-.02-.46.13-.61.14-.14.3-.35.45-.53.15-.18.2-.3.3-.5.1-.2.05-.38-.02-.53-.08-.15-.67-1.62-.92-2.22-.24-.58-.49-.5-.67-.51h-.57c-.2 0-.52.07-.8.38-.27.3-1.04 1.02-1.04 2.48s1.07 2.88 1.22 3.08c.15.2 2.1 3.2 5.08 4.49.71.3 1.26.49 1.7.63.71.23 1.36.2 1.87.12.57-.09 1.76-.72 2-1.41.25-.7.25-1.29.18-1.42-.07-.13-.27-.2-.57-.35z" />
      <path d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91c0 1.75.46 3.45 1.32 4.95L2 22l5.25-1.38a9.87 9.87 0 0 0 4.79 1.22h.01c5.46 0 9.91-4.45 9.91-9.91 0-2.65-1.03-5.14-2.9-7.01A9.82 9.82 0 0 0 12.04 2zm0 1.67c2.2 0 4.27.86 5.83 2.42a8.2 8.2 0 0 1 2.41 5.82c0 4.54-3.7 8.24-8.25 8.24a8.2 8.2 0 0 1-4.19-1.15l-.3-.18-3.11.82.83-3.04-.2-.31a8.18 8.18 0 0 1-1.26-4.38c0-4.54 3.7-8.24 8.24-8.24z" />
    </svg>
  );
}

function InstagramMark({ className }: Props) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M12 2.16c3.2 0 3.58.01 4.85.07 1.17.05 1.8.25 2.23.41.56.22.96.48 1.38.9.42.42.68.82.9 1.38.16.42.36 1.06.41 2.23.06 1.27.07 1.65.07 4.85s-.01 3.58-.07 4.85c-.05 1.17-.25 1.8-.41 2.23-.22.56-.48.96-.9 1.38-.42.42-.82.68-1.38.9-.42.16-1.06.36-2.23.41-1.27.06-1.65.07-4.85.07s-3.58-.01-4.85-.07c-1.17-.05-1.8-.25-2.23-.41a3.8 3.8 0 0 1-1.38-.9 3.8 3.8 0 0 1-.9-1.38c-.16-.42-.36-1.06-.41-2.23C2.17 15.58 2.16 15.2 2.16 12s.01-3.58.07-4.85c.05-1.17.25-1.8.41-2.23.22-.56.48-.96.9-1.38.42-.42.82-.68 1.38-.9.42-.16 1.06-.36 2.23-.41C8.42 2.17 8.8 2.16 12 2.16zm0 2.12c-3.14 0-3.5.01-4.74.07-1.14.05-1.76.24-2.17.4-.55.22-.94.47-1.35.88-.41.41-.66.8-.88 1.35-.16.41-.35 1.03-.4 2.17-.06 1.24-.07 1.6-.07 4.74s.01 3.5.07 4.74c.05 1.14.24 1.76.4 2.17.22.55.47.94.88 1.35.41.41.8.66 1.35.88.41.16 1.03.35 2.17.4 1.24.06 1.6.07 4.74.07s3.5-.01 4.74-.07c1.14-.05 1.76-.24 2.17-.4.55-.22.94-.47 1.35-.88.41-.41.66-.8.88-1.35.16-.41.35-1.03.4-2.17.06-1.24.07-1.6.07-4.74s-.01-3.5-.07-4.74c-.05-1.14-.24-1.76-.4-2.17a3.64 3.64 0 0 0-.88-1.35 3.64 3.64 0 0 0-1.35-.88c-.41-.16-1.03-.35-2.17-.4-1.24-.06-1.6-.07-4.74-.07z" />
      <path d="M12 7.94a4.06 4.06 0 1 0 0 8.12 4.06 4.06 0 0 0 0-8.12zm0 6.7a2.64 2.64 0 1 1 0-5.28 2.64 2.64 0 0 1 0 5.28z" />
      <circle cx="16.22" cy="7.78" r=".95" />
    </svg>
  );
}

function MessengerMark({ className }: Props) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M12 2C6.3 2 2 6.2 2 11.85c0 2.96 1.2 5.52 3.16 7.28.16.15.26.35.27.57l.05 1.79c.02.57.6.94 1.12.71l2-.88c.17-.08.36-.09.54-.04 1.2.33 2.47.47 3.72.4C18.6 21.3 22 17.1 22 11.85 22 6.2 17.7 2 12 2zm6 7.6-2.94 4.66c-.47.74-1.47.93-2.18.4L10.53 13a.6.6 0 0 0-.72 0l-3.16 2.4c-.42.32-.97-.18-.69-.63l2.94-4.66c.47-.74 1.47-.93 2.18-.4l2.35 1.66c.21.16.5.16.72 0l3.16-2.4c.42-.32.97.18.69.63z" />
    </svg>
  );
}

const MARCAS: Record<CanalId, (p: Props) => JSX.Element> = {
  whatsapp: WhatsAppMark,
  instagram_dm: InstagramMark,
  messenger: MessengerMark,
  email: ({ className }) => <Mail className={className} />,
  retell_voice: ({ className }) => <Phone className={className} />,
  webchat: ({ className }) => <Globe className={className} />,
};

export const CANAL_META: Record<CanalId, { label: string; color: string }> = {
  whatsapp: { label: "WhatsApp", color: "#25D366" },
  instagram_dm: { label: "Instagram", color: "#E1306C" },
  messenger: { label: "Messenger", color: "#0084FF" },
  email: { label: "Correo", color: "#0E9F9F" },
  retell_voice: { label: "Teléfono", color: "#C2427A" },
  webchat: { label: "Chat web", color: "#6B7686" },
};

export function ChannelIcon({ canal, className }: { canal: string; className?: string }) {
  const Marca = MARCAS[canal as CanalId];
  if (!Marca) return <Globe className={className} />;
  return <Marca className={className} />;
}
