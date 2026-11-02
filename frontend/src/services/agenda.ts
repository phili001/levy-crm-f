import { api } from "./api";
import type { AgendaServicio, AgendaVista, Appointment, ScheduleBlock } from "@/types";

// Agenda propia (Fase 9). Ver backend/app/api/agenda.py. Las horas que se
// mandan sin desfase ("AAAA-MM-DDTHH:MM") el servidor las entiende en la zona
// del negocio, igual que las que devuelve en `*_local`.

export async function getAgenda(desde: string, hasta: string): Promise<AgendaVista> {
  const { data } = await api.get<AgendaVista>("/agenda", { params: { desde, hasta } });
  return data;
}

export interface CitasDeHoy {
  hoy: string;
  zona: string;
  citas: Appointment[];
}

export async function getCitasDeHoy(): Promise<CitasDeHoy> {
  const { data } = await api.get<CitasDeHoy>("/appointments/today");
  return data;
}

/** Todas las citas de una persona (la más nueva primero) o solo las próximas. */
export async function getCitasDeContacto(contactId: string, proximas = false): Promise<Appointment[]> {
  const { data } = await api.get<Appointment[]>("/appointments", {
    params: { contact_id: contactId, proximas },
  });
  return data;
}

export async function getServiciosAgenda(): Promise<AgendaServicio[]> {
  const { data } = await api.get<AgendaServicio[]>("/appointments/services");
  return data;
}

export interface HorariosLibres {
  fecha: string;
  zona: string;
  servicio: string;
  duracion_min: number;
  /** Por qué no hay nada ese día (festivo, fin de semana), si es el caso. */
  cerrado: string | null;
  horarios: string[];
}

export async function getHorariosLibres(params: {
  fecha: string;
  servicio?: string;
  duracion_min?: number;
  excluir_cita?: string;
}): Promise<HorariosLibres> {
  const { data } = await api.get<HorariosLibres>("/appointments/slots", { params });
  return data;
}

export async function crearCita(body: {
  contact_id: string;
  servicio: string;
  starts_at: string;
  duracion_min?: number;
  notas?: string | null;
}): Promise<Appointment> {
  const { data } = await api.post<Appointment>("/appointments", body);
  return data;
}

/** Mover (`starts_at`) devuelve la cita NUEVA: la vieja queda cancelada. */
export async function cambiarCita(
  id: string,
  body: {
    starts_at?: string;
    servicio?: string;
    duracion_min?: number;
    notas?: string | null;
    estado?: "completada" | "no_asistio";
  },
): Promise<Appointment> {
  const { data } = await api.patch<Appointment>(`/appointments/${id}`, body);
  return data;
}

export async function cancelarCita(id: string): Promise<Appointment> {
  const { data } = await api.delete<Appointment>(`/appointments/${id}`);
  return data;
}

export async function crearBloqueo(body: {
  starts_at: string;
  ends_at: string;
  motivo?: string | null;
}): Promise<ScheduleBlock & { citas_afectadas: number }> {
  const { data } = await api.post<ScheduleBlock & { citas_afectadas: number }>("/schedule-blocks", body);
  return data;
}

export async function borrarBloqueo(id: string): Promise<void> {
  await api.delete(`/schedule-blocks/${id}`);
}
