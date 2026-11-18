export const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

export async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = response.status === 204 ? null : await response.json();
  if (!response.ok) throw new Error(payload?.detail || payload?.error || "Request failed");
  return payload;
}

export function calendarPath(view, date, timezone, limit = 500) {
  const params = new URLSearchParams({ view, date, timezone, limit: String(limit) });
  return `/calendar?${params.toString()}`;
}

export async function fetchCalendar(view, date, timezone) {
  if (view !== "3-days") return request(calendarPath(view, date, timezone));
  const dates = [date, addDays(date, 1), addDays(date, 2)];
  const payloads = await Promise.all(dates.map((day) => request(calendarPath("day", day, timezone))));
  const events = payloads.flatMap((payload) => payload.events || []);
  return {
    view,
    date,
    timezone,
    events,
    days: payloads.flatMap((payload) => payload.days || []),
    total: events.length,
    statistics: payloads.reduce((accumulator, payload) => mergeStatistics(accumulator, payload.statistics), emptyStatistics()),
  };
}

export function addDays(date, amount) {
  const value = new Date(`${date}T12:00:00`);
  value.setDate(value.getDate() + amount);
  return localDate(value);
}

export function shiftPeriod(date, view, direction) {
  if (view === "month") {
    const value = new Date(`${date}-01T12:00:00`);
    value.setMonth(value.getMonth() + direction);
    return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}`;
  }
  return addDays(date, direction * (view === "week" ? 7 : view === "3-days" ? 3 : 1));
}

export function localDate(value = new Date()) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

export function emptyStatistics() {
  return {
    memory_count: 0,
    captured_files: 0,
    created_notes: 0,
    saved_webpages: 0,
    study_sessions: 0,
    file_types: {},
    event_types: {},
    memories_per_day: {},
  };
}

function mergeStatistics(left, right = {}) {
  const merged = { ...left };
  ["memory_count", "captured_files", "created_notes", "saved_webpages", "study_sessions"].forEach((key) => {
    merged[key] = (merged[key] || 0) + (right[key] || 0);
  });
  ["file_types", "event_types", "memories_per_day"].forEach((key) => {
    merged[key] = { ...(merged[key] || {}) };
    Object.entries(right[key] || {}).forEach(([name, count]) => {
      merged[key][name] = (merged[key][name] || 0) + count;
    });
  });
  return merged;
}
