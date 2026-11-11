import assert from "node:assert/strict";
import test from "node:test";
import {
  isDownloadActive,
  registerLowPowerDownloadEntry,
  setLowPowerDownloadsEnabled,
  setLowPowerDownloadsOnBattery,
  shouldOfferLowPowerDownload,
  type LowPowerDownloadSettings,
  type PowerState,
} from "../src/lib/lowPowerDownloads.ts";

const settings = (enabled: boolean, onBattery: boolean): LowPowerDownloadSettings => ({ enabled, onBattery });
const power = (overrides: Partial<PowerState> = {}): PowerState => ({
  hasBattery: true,
  acConnected: true,
  batteryLevel: 1,
  ...overrides,
});

function installSleepPatch(windowOverrides: Record<string, unknown> = {}) {
  const calls: unknown[][] = [];
  const original = function (this: unknown, ...args: unknown[]) {
    calls.push(args);
    return "slept";
  };
  const store: any = { InitiateSleep: original, ShowConfirmSleepModal: false };
  store.SetShowConfirmSleepModal = (open: boolean) => {
    store.ShowConfirmSleepModal = open;
  };
  const fakeWindow: any = {
    settingsStore: { GetClientSetting: () => [true, () => {}] },
    downloadsStore: { m_DownloadOverview: new Map([[1, { update_state: "Downloading", paused: false }]]) },
    SuspendResumeStore: store,
    ...windowOverrides,
  };
  (globalThis as any).window = fakeWindow;
  const unregister = registerLowPowerDownloadEntry();
  return { calls, store, fakeWindow, unregister };
}

test("passes sleep through when SystemPowerStore is missing", () => {
  const { calls, store, unregister } = installSleepPatch();
  store.InitiateSleep.call(store, "sleep");
  assert.equal(store.ShowConfirmSleepModal, false);
  assert.deepEqual(calls, [["sleep"]]);
  unregister();
});

test("passes sleep through when batteryState is missing", () => {
  const { calls, store, unregister } = installSleepPatch({ SystemPowerStore: {} });
  store.InitiateSleep.call(store, "sleep");
  assert.equal(store.ShowConfirmSleepModal, false);
  assert.deepEqual(calls, [["sleep"]]);
  unregister();
});

test("passes sleep through while the battery identity is uninitialized", () => {
  const { calls, store, fakeWindow, unregister } = installSleepPatch({
    SystemPowerStore: { batteryState: { eACState: 0, flLevel: 1 } },
  });
  store.InitiateSleep.call(store, "first");
  fakeWindow.SystemPowerStore.batteryState = { bHasBattery: undefined, eACState: 0, flLevel: 1 };
  store.InitiateSleep.call(store, "second");
  assert.equal(store.ShowConfirmSleepModal, false);
  assert.deepEqual(calls, [["first"], ["second"]]);
  unregister();
});

test("passes sleep through when a present battery has no usable AC state", () => {
  const batteryStates = [
    { bHasBattery: true, flLevel: 1 },
    { bHasBattery: true, eACState: null, flLevel: 1 },
    { bHasBattery: true, eACState: undefined, flLevel: 1 },
  ];
  for (const batteryState of batteryStates) {
    const { calls, store, unregister } = installSleepPatch({
      SystemPowerStore: { batteryState },
    });
    store.InitiateSleep.call(store, "sleep");
    assert.equal(store.ShowConfirmSleepModal, false);
    assert.deepEqual(calls, [["sleep"]]);
    unregister();
  }
});

test("passes sleep through when a present battery reports the unknown AC state", () => {
  const { calls, store, unregister } = installSleepPatch({
    SystemPowerStore: { batteryState: { bHasBattery: true, eACState: 0, flLevel: 0.1 } },
  });
  store.InitiateSleep.call(store, "sleep");
  assert.equal(store.ShowConfirmSleepModal, false);
  assert.deepEqual(calls, [["sleep"]]);
  unregister();
});

test("passes sleep through on AC state values outside the SteamTracking enum", () => {
  const batteryStates = [
    { bHasBattery: true, eACState: 4, flLevel: 0.1 },
    { bHasBattery: true, eACState: -1, flLevel: 0.1 },
    { bHasBattery: true, eACState: "2", flLevel: 0.1 },
    { bHasBattery: true, eACState: Number.NaN, flLevel: 0.1 },
  ];
  for (const batteryState of batteryStates) {
    const { calls, store, unregister } = installSleepPatch({
      SystemPowerStore: { batteryState },
    });
    store.InitiateSleep.call(store, "sleep");
    assert.equal(store.ShowConfirmSleepModal, false);
    assert.deepEqual(calls, [["sleep"]]);
    unregister();
  }
});

test("gating resumes once valid power data arrives", () => {
  const { calls, store, fakeWindow, unregister } = installSleepPatch();
  store.InitiateSleep.call(store, "before");
  assert.equal(store.ShowConfirmSleepModal, false);
  assert.deepEqual(calls, [["before"]]);
  fakeWindow.SystemPowerStore = { batteryState: { bHasBattery: true, eACState: 2, flLevel: 0.1 } };
  store.InitiateSleep.call(store, "after");
  assert.equal(store.ShowConfirmSleepModal, true);
  assert.deepEqual(calls, [["before"]]);
  store.InitiateSleep.call(store, "confirm");
  assert.deepEqual(calls, [["before"], ["confirm"]]);
  unregister();
});

test("keeps sleep unchanged when valid low battery data arrives with battery mode disabled", () => {
  const settingsStore = {
    GetClientSetting: (key: string) => [
      key !== "system_allow_battery_low_power_downloads",
      () => {},
    ],
  };
  const { calls, store, fakeWindow, unregister } = installSleepPatch({
    settingsStore,
    SystemPowerStore: { batteryState: { bHasBattery: true, flLevel: 0.1 } },
  });
  store.InitiateSleep.call(store, "unknown");
  assert.equal(store.ShowConfirmSleepModal, false);
  assert.deepEqual(calls, [["unknown"]]);
  fakeWindow.SystemPowerStore = { batteryState: { bHasBattery: true, eACState: 1, flLevel: 0.1 } };
  store.InitiateSleep.call(store, "low");
  assert.equal(store.ShowConfirmSleepModal, false);
  assert.deepEqual(calls, [["unknown"], ["low"]]);
  unregister();
});

test("still offers on a confirmed device without a battery", () => {
  const { calls, store, unregister } = installSleepPatch({
    SystemPowerStore: { batteryState: { bHasBattery: false, eACState: 1, flLevel: 0 } },
  });
  store.InitiateSleep.call(store, "sleep");
  assert.equal(store.ShowConfirmSleepModal, true);
  assert.deepEqual(calls, []);
  unregister();
});

test("still offers on a confirmed device without a battery and no AC data", () => {
  const { calls, store, unregister } = installSleepPatch({
    SystemPowerStore: { batteryState: { bHasBattery: false } },
  });
  store.InitiateSleep.call(store, "sleep");
  assert.equal(store.ShowConfirmSleepModal, true);
  assert.deepEqual(calls, []);
  unregister();
});

test("still offers when the charger reports connected slow", () => {
  const { calls, store, unregister } = installSleepPatch({
    SystemPowerStore: { batteryState: { bHasBattery: true, eACState: 3, flLevel: 0.1 } },
  });
  store.InitiateSleep.call(store, "sleep");
  assert.equal(store.ShowConfirmSleepModal, true);
  assert.deepEqual(calls, []);
  unregister();
});

test("still offers on a disconnected battery above 20% with battery mode allowed", () => {
  const { calls, store, unregister } = installSleepPatch({
    SystemPowerStore: { batteryState: { bHasBattery: true, eACState: 1, flLevel: 0.21 } },
  });
  store.InitiateSleep.call(store, "sleep");
  assert.equal(store.ShowConfirmSleepModal, true);
  assert.deepEqual(calls, []);
  unregister();
});

test("offers to keep downloading with the screen off on AC power", () => {
  assert.equal(shouldOfferLowPowerDownload(settings(true, false), power(), true), true);
});

test("offers when the device reports no battery", () => {
  assert.equal(
    shouldOfferLowPowerDownload(settings(true, false), power({ hasBattery: false, acConnected: false }), true),
    true,
  );
});

test("does not offer on battery when the battery setting is off", () => {
  assert.equal(
    shouldOfferLowPowerDownload(settings(true, false), power({ acConnected: false, batteryLevel: 0.9 }), true),
    false,
  );
});

test("does not offer on battery at exactly 20%", () => {
  assert.equal(
    shouldOfferLowPowerDownload(settings(true, true), power({ acConnected: false, batteryLevel: 0.2 }), true),
    false,
  );
});

test("offers on battery above 20%", () => {
  assert.equal(
    shouldOfferLowPowerDownload(settings(true, true), power({ acConnected: false, batteryLevel: 0.21 }), true),
    true,
  );
});

test("does not offer on battery below 20%", () => {
  assert.equal(
    shouldOfferLowPowerDownload(settings(true, true), power({ acConnected: false, batteryLevel: 0.199 }), true),
    false,
  );
});

test("does not offer while the main setting is off", () => {
  assert.equal(shouldOfferLowPowerDownload(settings(false, true), power(), true), false);
});

test("does not offer without an active download", () => {
  assert.equal(shouldOfferLowPowerDownload(settings(true, true), power(), false), false);
  assert.equal(isDownloadActive({ update_state: "Downloading", paused: true }), false);
  assert.equal(isDownloadActive({ update_state: "Stopped", paused: false }), false);
  assert.equal(isDownloadActive(undefined), false);
});

test("rejects when Steam provides no settings setter", async () => {
  (globalThis as any).window = { settingsStore: { GetClientSetting: () => [true, undefined] } };
  await assert.rejects(setLowPowerDownloadsEnabled(true), /Steam settings are unavailable/);
});

test("propagates a rejected settings setter", async () => {
  (globalThis as any).window = {
    settingsStore: {
      GetClientSetting: () => [
        true,
        async () => {
          throw new Error("settings rejected");
        },
      ],
    },
  };
  await assert.rejects(setLowPowerDownloadsOnBattery(true), /settings rejected/);
});

test("unregistering before installation cancels the retry timer", () => {
  let tick: (() => void) | undefined;
  let clearedHandle: number | undefined;
  const fakeWindow: any = {
    setInterval: (callback: () => void) => {
      tick = callback;
      return 21;
    },
    clearInterval: (handle: number) => {
      clearedHandle = handle;
    },
  };
  (globalThis as any).window = fakeWindow;
  const unregister = registerLowPowerDownloadEntry();
  unregister();
  assert.equal(clearedHandle, 21);
  const original = function (this: unknown, ...args: unknown[]) {
    return args.length;
  };
  const store: any = { InitiateSleep: original, ShowConfirmSleepModal: false };
  store.SetShowConfirmSleepModal = (open: boolean) => {
    store.ShowConfirmSleepModal = open;
  };
  fakeWindow.SuspendResumeStore = store;
  tick?.();
  assert.equal(store.InitiateSleep, original);
});

test("registration retries until SuspendResumeStore exists, then patches and restores", () => {
  const calls: unknown[][] = [];
  const original = function (this: unknown, ...args: unknown[]) {
    calls.push(args);
    return "slept";
  };
  let tick: (() => void) | undefined;
  let clearedHandle: number | undefined;
  const store: any = { InitiateSleep: original, ShowConfirmSleepModal: false };
  store.SetShowConfirmSleepModal = (open: boolean) => {
    store.ShowConfirmSleepModal = open;
  };
  const fakeWindow: any = {
    setInterval: (callback: () => void) => {
      tick = callback;
      return 11;
    },
    clearInterval: (handle: number) => {
      clearedHandle = handle;
    },
  };
  (globalThis as any).window = fakeWindow;
  const unregister = registerLowPowerDownloadEntry();
  assert.equal(typeof tick, "function");
  fakeWindow.SuspendResumeStore = store;
  tick?.();
  assert.equal(clearedHandle, 11);
  assert.notEqual(store.InitiateSleep, original);
  store.InitiateSleep.call(store, "sleep");
  assert.deepEqual(calls, [["sleep"]]);
  unregister();
  assert.equal(store.InitiateSleep, original);
});

test("a sleep request passes through while the confirm modal is already open", () => {
  const calls: unknown[][] = [];
  const original = function (this: unknown, ...args: unknown[]) {
    calls.push(args);
    return "slept";
  };
  let tick: (() => void) | undefined;
  const store: any = { InitiateSleep: original, ShowConfirmSleepModal: false };
  store.SetShowConfirmSleepModal = (open: boolean) => {
    store.ShowConfirmSleepModal = open;
  };
  const fakeWindow: any = {
    settingsStore: { GetClientSetting: () => [true, () => {}] },
    downloadsStore: { m_DownloadOverview: new Map([[1, { update_state: "Downloading", paused: false }]]) },
    SystemPowerStore: { batteryState: { bHasBattery: true, eACState: 2, flLevel: 1 } },
    setInterval: (callback: () => void) => {
      tick = callback;
      return 12;
    },
    clearInterval: () => {},
  };
  (globalThis as any).window = fakeWindow;
  const unregister = registerLowPowerDownloadEntry();
  fakeWindow.SuspendResumeStore = store;
  tick?.();
  store.InitiateSleep.call(store, "first");
  assert.equal(store.ShowConfirmSleepModal, true);
  assert.deepEqual(calls, []);
  store.InitiateSleep.call(store, "second");
  assert.deepEqual(calls, [["second"]]);
  unregister();
  assert.equal(store.InitiateSleep, original);
});
