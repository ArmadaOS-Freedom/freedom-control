export interface PowerProfile {
  label: string;
  cpu_governor: string;
  cpu_max: string;
  cpu_underclock: string;
  gpu_max: string;
  gpu_min: string;
  fan_curve: string;
}

export interface FanCurve {
  label: string;
  curve: string;
}

export interface PowerConfig {
  general: { default_profile: string };
  profiles: Record<string, PowerProfile>;
  fan_curves: Record<string, FanCurve>;
  fan: Record<string, string>;
  underclocks: Record<string, Record<string, Record<string, string>>>;
}

export interface GameTweak {
  enabled?: boolean;
  name?: string;
  fexProfile?: string;
  fexConfig?: Record<string, string>;
  turnipDriver?: string;
  thunks?: Record<string, boolean>;
  [key: string]: any;
}

export interface Tweaks {
  global: Record<string, any>;
  games: Record<string, GameTweak>;
}

export interface CompatAppliedState {
  appids: string[];
  protonDefault: string;
}

export interface InstalledGame {
  appid: string;
  name: string;
  nonSteam?: boolean;
}

export interface FexProfile {
  label: string;
  config?: Record<string, string>;
}

export interface TurnipDriver {
  id: string;
  label: string;
  version: string;
}

export interface UpdateItem {
  id: string;
  kind: "turnip" | "compatibility";
  label: string;
  version: string;
  description: string;
  url: string;
  sha256: string;
  verified?: boolean;
  installed: boolean;
  installedId?: string;
  selectionId?: string;
}

export interface UpdatesState {
  ok: boolean;
  source: string;
  generatedAt?: string;
  error?: string;
  updates: UpdateItem[];
}

export interface LocalUpdate {
  id: string;
  kind: "turnip" | "compatibility";
  label: string;
  version: string;
  removable: boolean;
  selectionId?: string;
  selectionCleared?: boolean;
  installedId?: string;
}

export interface UpdateProgress {
  id: string;
  phase: "idle" | "checking" | "downloading" | "extracting" | "installing" | "complete" | "error";
  percent: number;
  downloaded: number;
  total: number;
}

// A text in env-presets.json is either one string or one string per locale, so a
// label that needs no translation does not cost four lines.
export type LocalizedText = string | Record<string, string>;

export interface EnvPresetOption {
  data: string;
  label: LocalizedText;
}

export interface EnvPreset {
  name: string;
  description: LocalizedText;
  // Closed list of values; absent means the value is free text.
  options?: EnvPresetOption[];
  // A hint only. The docs show these inside examples and never state a default.
  example?: string;
}

export interface AbsControl {
  value: number;
  min: number;
  max: number;
  flat: number;
  fuzz: number;
  resolution: number;
}

export interface HomePowerState {
  enabled: boolean;
  supported: boolean;
  active: boolean;
  reason: string;
}

// Panel axes, not a landscape/portrait flag: "normal" is whatever the panel
// reports natively, so every orientation is relative to that.
export type DisplayOrientation = "normal" | "left" | "right" | "upsidedown";

export interface DisplayOrientationState {
  supported: boolean;
  // Empty means the Game Mode override is off.
  orientation: DisplayOrientation | "";
  defaultOrientation: DisplayOrientation;
}

export interface CalibrationState {
  supported: boolean;
  reason: string;
  controls: Record<string, AbsControl>;
  event: any;
  canApply?: boolean;
  backend?: string;
  saved?: boolean;
  params?: Record<string, number>;
}

export interface RgbConfig {
  version: number;
  enabled: boolean;
  linkBrightness: boolean;
  maxBrightness: number;
  brightness: number;
  color: string;
  saturation: number;
}

export interface GameRef {
  appid: string;
  name: string;
  nonSteam?: boolean;
}

export interface PerfInfo {
  governors: string[];
  schedulers: string[];
  corePresets: DropdownChoice[];
  cpuCount: number;
}

export interface Config {
  power: PowerConfig;
  powerDefaults: PowerConfig;
  tweaks: Tweaks;
  installedGames: InstalledGame[];
  fexProfiles: Record<string, FexProfile>;
  turnipDrivers?: TurnipDriver[];
  envPresets: EnvPreset[];
  perf?: PerfInfo;
  cpuDeviceClass: string;
  deviceId?: string;
  rgbSupported: boolean;
  protonDefaults: string[];
  osVersion: string;
  bottomScreenSupported: boolean;
  bottomScreenEnabled: boolean;
  bottomScreenBrightnessSupported: boolean;
  bottomScreenActive: boolean;
  bottomScreenBrightness: number;
  chargingFanPwm: number;
  fanSupported: boolean;
  sshEnabled: boolean;
  swipeGesturesEnabled: boolean;
  swipeGesturesInGameEnabled: boolean;
  mtpEnabled: boolean;
  usbNetworkEnabled: boolean;
  usbNetworkSupported: boolean;
  usbNetworkPaused?: boolean;
  desktopMode: string;
  desktopModes: DropdownChoice[];
  sleepMode: string;
  sleepModes: DropdownChoice[];
  game?: GameRef | null;
  selectedGame?: GameRef | null;
}

export interface DropdownChoice {
  data: string;
  label: string;
}

export interface ProfileSummary {
  label: string;
  fan_curve: string;
}

export interface FanSettings {
  ramp_up: number;
  ramp_down: number;
  smoothing: number;
  min_pwm: number;
}

export interface CurvesState {
  fanCurves: Record<string, FanCurve>;
  factoryFanCurves: Record<string, FanCurve>;
  fanSettings: FanSettings;
  factoryFanSettings: FanSettings;
  profiles: Record<string, ProfileSummary>;
  // Falls back to the configured default, then any profile, if the daemon state can't be read.
  activeProfile: string;
  // Live marker instead polls get_current_temp (see hooks/useCurrentTemp).
  currentTemp: number | null;
}
