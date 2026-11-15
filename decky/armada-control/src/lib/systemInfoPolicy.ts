// Native Steam reports this SoC as sCPUName "Unknown" even though the device
// profile already identifies it. Only that one class and only a missing or
// "Unknown" native name are filled; everything else passes through untouched.
export const KNOWN_GEN5_SOC_CLASS = "SM8850";
export const KNOWN_GEN5_CPU_NAME = "Qualcomm Snapdragon 8 Elite Gen 5";

const isUnknownReportedName = (value: unknown): boolean => {
  if (value === undefined || value === null) return true;
  if (typeof value !== "string") return false;
  const trimmed = value.trim();
  return trimmed === "" || trimmed.toLowerCase() === "unknown";
};

// Returns the original object when nothing needs filling; the clone keeps every
// other native field exactly as it arrived.
export function applySystemInfoCpuNameFallback<T>(cpuDeviceClass: unknown, info: T): T {
  if (!info || typeof info !== "object") return info;
  if (cpuDeviceClass !== KNOWN_GEN5_SOC_CLASS) return info;
  const fields = info as Record<string, unknown>;
  if (!isUnknownReportedName(fields.sCPUName)) return info;
  return { ...fields, sCPUName: KNOWN_GEN5_CPU_NAME } as unknown as T;
}
