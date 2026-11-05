// Normalize a stored scheduler selection against the schedulers actually
// exposed by the backend for the current kernel.
//
// - null/undefined (or empty string) -> '' meaning "inherit / default"
// - 'eevdf' / 'infinity' -> the actual base scheduler found in `available`
//   (prefers an exact match already present, never promotes a stale value
//   to a nonexistent option)
// - anything else -> unchanged
//
// The backend `perf.schedulers` array never contains both base schedulers:
// it is ["eevdf", ...cosmos/lavd] on a normal kernel, or
// ["infinity", ...cosmos/lavd] on the Infinity kernel.
export function normalizeSchedulerSelection(
  value: unknown,
  available: readonly string[],
): string {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  const raw = String(value);

  const isBase = raw === "eevdf" || raw === "infinity";
  if (!isBase) {
    return raw;
  }

  const list = available || [];

  // Exact match already present -> keep it.
  if (list.includes(raw)) {
    return raw;
  }

  // Otherwise, fall back to whichever base scheduler actually exists.
  // Prefer the conceptually opposite base (kernel migration), then any
  // other base present. Never return a value absent from `available`.
  if (raw === "eevdf") {
    if (list.includes("infinity")) return "infinity";
  } else {
    if (list.includes("eevdf")) return "eevdf";
  }

  return "";
}
