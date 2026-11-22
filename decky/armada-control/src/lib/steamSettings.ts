const GLOBAL_RESOLUTION_KEY = "gamescope_game_resolution_global";
const STARTUP_MARKER_KEY = "armada-resolution-preset-applied";

export const resolutionOptions = [
  { data: "Default", label: "Default" },
  { data: "Native", label: "Native" },
  { data: "2560x1440", label: "2560x1440" },
  { data: "1920x1080", label: "1920x1080" },
  { data: "1280x720", label: "1280x720" },
  { data: "960x540", label: "960x540" },
];

let pendingFallback: { store: unknown; staleValue: string | undefined } | null = null;

function resolveCached(raw: unknown): string {
  if (
    pendingFallback &&
    window.settingsStore === pendingFallback.store &&
    raw === pendingFallback.staleValue
  ) {
    return "1280x720";
  }
  pendingFallback = null;
  return (typeof raw === "string" ? raw : "") || "Default";
}

export function getGlobalResolution(): string {
  const raw = window.settingsStore?.GetClientSetting?.(GLOBAL_RESOLUTION_KEY)?.[0];
  return resolveCached(raw);
}

export async function setGlobalResolution(value: string): Promise<string> {
  const store = window.settingsStore;
  const setting = store?.GetClientSetting?.(GLOBAL_RESOLUTION_KEY);
  const setter = setting?.[1];
  if (!setter) throw new Error("Steam settings are unavailable");
  await Promise.resolve(setter(value));
  pendingFallback = null;
  return value || "Default";
}

let inflightInit: Promise<string> | null = null;

export function initializeGlobalResolution(): Promise<string> {
  if (inflightInit) return inflightInit;
  inflightInit = (async (): Promise<string> => {
    const app = (window as any).App;
    if (app && typeof app.WaitForServicesInitialized === "function") {
      await app.WaitForServicesInitialized();
    }
    const setting = window.settingsStore?.GetClientSetting?.(GLOBAL_RESOLUTION_KEY);
    const rawCurrent = setting?.[0];
    const setter = setting?.[1];
    const storage = window.localStorage;
    if (!setter || !storage || storage.getItem(STARTUP_MARKER_KEY)) {
      return resolveCached(rawCurrent);
    }
    if (!rawCurrent || rawCurrent === "Native" || rawCurrent === "Default") {
      const store = window.settingsStore;
      await setGlobalResolution("1280x720");
      const after = window.settingsStore?.GetClientSetting?.(GLOBAL_RESOLUTION_KEY)?.[0];
      if (after === rawCurrent) {
        pendingFallback = { store, staleValue: after };
      }
      storage.setItem(STARTUP_MARKER_KEY, "1");
      return "1280x720";
    }
    storage.setItem(STARTUP_MARKER_KEY, "1");
    return rawCurrent;
  })().finally(() => {
    inflightInit = null;
  });
  return inflightInit;
}

export async function getAppResolution(appid: number): Promise<string> {
  const direct = (window as any).SteamClient?.Apps?.GetResolutionOverrideForApp;
  if (typeof direct === "function") {
    const current = await direct.call((window as any).SteamClient.Apps, appid);
    return current || "Default";
  }
  const details = (window as any).appDetailsStore;
  const requestDetails = details?.RequestAppDetails;
  if (typeof requestDetails === "function") {
    const info = await requestDetails.call(details, appid);
    return info?.strResolutionOverride || "Default";
  }
  return "Default";
}
