import { localDate } from "../api";

export default function MiniCalendar({ selectedDate, month, eventDates, onSelect, onMonthChange }) {
  const first = new Date(`${month}-01T12:00:00`);
  const firstDay = new Date(first);
  firstDay.setDate(1 - first.getDay());
  const days = Array.from({ length: 42 }, (_, index) => {
    const value = new Date(firstDay);
    value.setDate(firstDay.getDate() + index);
    return value;
  });
  return <div className="mini-calendar">
    <div className="mini-header"><strong>{first.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</strong><div><button onClick={() => onMonthChange(-1)} aria-label="Previous month">‹</button><button onClick={() => onMonthChange(1)} aria-label="Next month">›</button></div></div>
    <div className="weekday-row">{["S", "M", "T", "W", "T", "F", "S"].map((day, index) => <span key={`${day}-${index}`}>{day}</span>)}</div>
    <div className="calendar-grid">{days.map((day) => {
      const iso = localDate(day);
      const inMonth = day.getMonth() === first.getMonth();
      return <button key={iso} className={`calendar-day ${inMonth ? "" : "outside"} ${iso === selectedDate ? "selected" : ""} ${eventDates.has(iso) ? "has-events" : ""}`} onClick={() => onSelect(iso)}>{day.getDate()}</button>;
    })}</div>
  </div>;
}

export function eventDatesForMonth(days) {
  return new Set((days || []).map((day) => day.date));
}
