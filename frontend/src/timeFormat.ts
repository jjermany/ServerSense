export function parseTimestamp(value: string | Date) {
  if (value instanceof Date) return value;
  const explicitZone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value);
  return new Date(explicitZone ? value : `${value}Z`);
}

export function formatDateTime(value: string | Date, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone,
    timeZoneName: "short",
  }).format(parseTimestamp(value));
}

export function formatDate(value: string | Date, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone,
  }).format(parseTimestamp(value));
}

export function formatTime(value: string | Date, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    timeZone,
    timeZoneName: "short",
  }).format(parseTimestamp(value));
}

export function localHour(value: Date, timeZone: string) {
  const hour = new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    hourCycle: "h23",
    timeZone,
  })
    .formatToParts(value)
    .find((part) => part.type === "hour")?.value;
  return Number(hour ?? 0);
}
