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

export function getGlobalResolution(): string {
  return window.settingsStore?.GetClientSetting?.(GLOBAL_RESOLUTION_KEY)?.[0] || "Default";
}

export async function setGlobalResolution(value: string): Promise<string> {
  const setting = window.settingsStore?.GetClientSetting?.(GLOBAL_RESOLUTION_KEY);
  const setter = setting?.[1];
  if (!setter) throw new Error("Steam settings are unavailable");
  await Promise.resolve(setter(value));
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
      return rawCurrent || "Default";
    }
    if (!rawCurrent || rawCurrent === "Native" || rawCurrent === "Default") {
      await setGlobalResolution("1280x720");
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
