import { callable } from "@decky/api";
import type {
  Control,
  DpadMode,
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
    dpadMode: DpadMode | null,
    basePresetId: string | null,
  ],
  State
>("save_touch_preset");
const deletePresetCall = callable<[id: string], State>(
  "delete_touch_preset",
);
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

/**
 * One serialized tail for every mutation. A page may unmount while its request
 * is still on the wire, but the backend has already accepted the operation, so
 * it must still run — and in the order the user produced it. A failed
 * mutation only rejects its own caller; the tail always keeps going.
 */
let mutationTail: Promise<void> = Promise.resolve();

const trail = (result: Promise<unknown>): void => {
  mutationTail = result.then(
    () => undefined,
    () => undefined,
  );
};

interface OptionsWaiter {
  resolve: (state: State) => void;
  reject: (error: unknown) => void;
}

/**
 * One queued update_options call. It holds its queue slot from the moment the
 * user acts, but its patch is only frozen when the slot reaches the wire, so
 * adjacent slider readings share a single write instead of a burst of them.
 */
interface OptionsBatch {
  patch: OptionsPatch;
  waiters: OptionsWaiter[];
}

/** The batch that can still absorb patches; null once it is dispatched. */
let openBatch: OptionsBatch | null = null;

/**
 * Accept one mutation. Every other mutation also closes the open options
 * batch, so a patch made after a preset/settings/layout call can never be
 * merged into an earlier slot and overtake that call.
 */
const enqueue = <T>(run: () => Promise<T>): Promise<T> => {
  openBatch = null;
  const result = mutationTail.then(run);
  trail(result);
  return result;
};

const startBatch = (): OptionsBatch => {
  const job: OptionsBatch = { patch: {}, waiters: [] };
  openBatch = job;
  trail(
    mutationTail.then(async () => {
      // The patch is fixed from here on: later readings take a new slot.
      if (openBatch === job) {
        openBatch = null;
      }
      try {
        const next = await updateOptionsCall(job.patch);
        job.waiters.forEach((waiter) => waiter.resolve(next));
      } catch (error) {
        job.waiters.forEach((waiter) => waiter.reject(error));
      }
    }),
  );
  return job;
};

/**
 * Read a snapshot once every mutation accepted before this call has finished.
 * A tab that remounts therefore reads the values its predecessor saved instead
 * of a snapshot that predates them.
 */
export const fetchState = (): Promise<State> => {
  const accepted = mutationTail;
  return accepted.then(() => getState());
};

export const setSettings = (settings: Settings): Promise<State> =>
  enqueue(() => setSettingsCall(settings));

/**
 * Apply an options patch. Patches handed in while an earlier batch is still
 * waiting for the wire are merged into it (newest value per field) and every
 * caller receives that batch's outcome.
 */
export const updateOptions = (patch: OptionsPatch): Promise<State> => {
  const batch = openBatch ?? startBatch();
  batch.patch = { ...batch.patch, ...patch };
  return new Promise<State>((resolve, reject) => {
    batch.waiters.push({ resolve, reject });
  });
};

export const selectPreset = (id: string): Promise<State> =>
  enqueue(() => selectPresetCall(id));

/**
 * Save and activate a preset. `overwriteId` is null when creating a new
 * custom preset; the backend rejects duplicate names unless the same entry is
 * being overwritten.
 */
export const savePreset = (
  name: string,
  layout: Control[],
  scale: number,
  overwriteId: string | null = null,
  dpadMode: DpadMode | null = null,
  basePresetId: string | null = null,
): Promise<State> =>
  enqueue(() =>
    savePresetCall(name, layout, scale, overwriteId, dpadMode, basePresetId),
  );

/**
 * Delete a custom preset. Built-in and unknown ids are rejected by the backend;
 * deleting the active custom falls back to the tablet_standard template.
 */
export const deletePreset = (id: string): Promise<State> =>
  enqueue(() => deletePresetCall(id));

export const resetLayout = (): Promise<State> =>
  enqueue(() => resetLayoutCall());

export const sendHeartbeat = (appid: string | null): Promise<Status> =>
  heartbeatCall(appid);

export const previewHaptics = (): Promise<{ queued: boolean }> =>
  previewHapticsCall();

/**
 * Show or hide the overlay controls. The backend applies this when the daemon
 * acknowledges and returns the fresh state; it is not persisted in settings.
 */
export const setControlsVisible = (visible: boolean): Promise<State> =>
  enqueue(() => setControlsVisibleCall(visible));
