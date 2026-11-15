import { callable } from "@decky/api";
import type {
  Control,
  OptionsPatch,
  Settings,
  State,
  Status,
} from "./types";

// The touch page runs next to the Controller backend, so every callable keeps
// the original touch schema and is addressed through its prefixed RPC name.
const getState = callable<[], State>("get_touch_state");
const setSettingsCall = callable<[settings: Settings], State>(
  "set_touch_settings",
);
const updateOptionsCall = callable<[patch: OptionsPatch], State>(
  "update_touch_options",
);
const selectPresetCall = callable<[id: string], State>("select_touch_preset");
const savePresetCall = callable<
  [
    name: string,
    layout: Control[],
    scale: number,
    overwriteId: string | null,
  ],
  State
>("save_touch_preset");
const resetLayoutCall = callable<[], State>("reset_touch_layout");
const heartbeatCall = callable<[appid: string | null], Status>(
  "touch_heartbeat",
);
const previewHapticsCall = callable<[], { queued: boolean }>(
  "preview_touch_haptics",
);
const setControlsVisibleCall = callable<[visible: boolean], State>(
  "set_touch_controls_visible",
);

export const fetchState = (): Promise<State> => getState();

export const setSettings = (settings: Settings): Promise<State> =>
  setSettingsCall(settings);

export const updateOptions = (patch: OptionsPatch): Promise<State> =>
  updateOptionsCall(patch);

export const selectPreset = (id: string): Promise<State> =>
  selectPresetCall(id);

/**
 * Save and activate a preset. `overwriteId` is null when creating a new
 * custom preset; the backend rejects duplicate names unless the same entry is
 * being overwritten.
 */
export const savePreset = (
  name: string,
  layout: Control[],
  scale: number,
  overwriteId: string | null,
): Promise<State> => savePresetCall(name, layout, scale, overwriteId);

export const resetLayout = (): Promise<State> => resetLayoutCall();

export const sendHeartbeat = (appid: string | null): Promise<Status> =>
  heartbeatCall(appid);

export const previewHaptics = (): Promise<{ queued: boolean }> =>
  previewHapticsCall();

/**
 * Show or hide the overlay controls. The backend applies this when the daemon
 * acknowledges and returns the fresh state; it is not persisted in settings.
 */
export const setControlsVisible = (visible: boolean): Promise<State> =>
  setControlsVisibleCall(visible);
