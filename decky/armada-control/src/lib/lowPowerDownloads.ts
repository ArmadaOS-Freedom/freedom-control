const ENABLED_KEY = "system_enable_low_power_downloads";
const BATTERY_KEY = "system_allow_battery_low_power_downloads";
// SteamTracking EACState enum: Unknown = 0, Disconnected = 1, Connected = 2,
// ConnectedSlow = 3. Only the three defined power-source values are trusted.
const AC_DISCONNECTED = 1;
const AC_CONNECTED = 2;
const AC_CONNECTED_SLOW = 3;
const MIN_BATTERY_LEVEL = 0.2;

export interface LowPowerDownloadSettings {
  enabled: boolean;
  onBattery: boolean;
}

export interface PowerState {
  hasBattery: boolean;
  acConnected: boolean;
  batteryLevel: number;
}

export interface DownloadEntry {
  update_state?: string;
  paused?: boolean;
}

function clientSetting(key: string): [any, ((value: any) => unknown) | undefined] {
  const setting = window.settingsStore?.GetClientSetting?.(key);
  return [setting?.[0], setting?.[1]];
}

export function getLowPowerDownloadSettings(): LowPowerDownloadSettings {
  return { enabled: !!clientSetting(ENABLED_KEY)[0], onBattery: !!clientSetting(BATTERY_KEY)[0] };
}

async function setClientSetting(key: string, value: boolean): Promise<void> {
  const setter = clientSetting(key)[1];
  if (!setter) throw new Error("Steam settings are unavailable");
  await Promise.resolve(setter(value));
}

export const setLowPowerDownloadsEnabled = (value: boolean): Promise<void> =>
  setClientSetting(ENABLED_KEY, value);

export const setLowPowerDownloadsOnBattery = (value: boolean): Promise<void> =>
  setClientSetting(BATTERY_KEY, value);

export function isDownloadActive(entry: DownloadEntry | undefined): boolean {
  return !!entry && entry.update_state === "Downloading" && !entry.paused;
}

// Same conditions Steam uses on Deck: any power source when there is no
// battery, wall power, or battery power with more than 20% left when allowed.
export function shouldOfferLowPowerDownload(
  settings: LowPowerDownloadSettings,
  power: PowerState,
  downloadActive: boolean,
): boolean {
  if (!settings.enabled || !downloadActive) return false;
  if (!power.hasBattery || power.acConnected) return true;
  return settings.onBattery && power.batteryLevel > MIN_BATTERY_LEVEL;
}

function currentDownloadEntry(): DownloadEntry | undefined {
  const overview = (window as any).downloadsStore?.m_DownloadOverview;
  if (!overview || typeof overview.values !== "function") return undefined;
  return Array.from(overview.values() as Iterable<DownloadEntry>)[0];
}

// Returns undefined while Steam has no usable power data: a missing store, a
// missing batteryState or an unset battery identity is unknown power data, not
// a confirmed device without a battery. A confirmed battery also needs a
// recognised SteamTracking EACState before its power source is trusted, so the
// Unknown value (0) and out-of-enum values (strings, negative, NaN, ...) are
// reported as unknown rather than being read as on AC or on battery. A
// confirmed device without a battery keeps working without any AC data.
function systemPowerState(): PowerState | undefined {
  const battery = (window as any).SystemPowerStore?.batteryState;
  if (!battery || typeof battery.bHasBattery !== "boolean") return undefined;
  const batteryLevel = Number(battery.flLevel ?? 0);
  if (!battery.bHasBattery) return { hasBattery: false, acConnected: false, batteryLevel };
  const acState = battery.eACState;
  if (acState !== AC_DISCONNECTED && acState !== AC_CONNECTED && acState !== AC_CONNECTED_SLOW) {
    return undefined;
  }
  return {
    hasBattery: true,
    acConnected: acState === AC_CONNECTED || acState === AC_CONNECTED_SLOW,
    batteryLevel,
  };
}

function shouldOffer(): boolean {
  const power = systemPowerState();
  if (!power) return false;
  return shouldOfferLowPowerDownload(
    getLowPowerDownloadSettings(),
    power,
    isDownloadActive(currentDownloadEntry()),
  );
}

// Steam only offers its low-power download mode on Valve and Lenovo hardware,
// so put its confirm-sleep modal in front of sleep ourselves. The modal's own
// Sleep button calls InitiateSleep while it is open, so that call passes through.
export function registerLowPowerDownloadEntry(): () => void {
  let restore = () => {};
  let stopped = false;
  let timer: number | undefined;
  const install = (): boolean => {
    const store = (window as any).SuspendResumeStore;
    if (stopped || !store?.InitiateSleep || !store.SetShowConfirmSleepModal) return false;
    const original = store.InitiateSleep;
    store.InitiateSleep = function (this: any, ...args: unknown[]) {
      if (!this.ShowConfirmSleepModal && shouldOffer()) {
        this.SetShowConfirmSleepModal(true);
        return;
      }
      return original.apply(this, args);
    };
    restore = () => {
      store.InitiateSleep = original;
    };
    return true;
  };
  if (!install()) {
    timer = window.setInterval(() => {
      if (!install()) return;
      if (timer !== undefined) window.clearInterval(timer);
      timer = undefined;
    }, 1000);
  }
  return () => {
    stopped = true;
    if (timer !== undefined) window.clearInterval(timer);
    timer = undefined;
    restore();
    restore = () => {};
  };
}
