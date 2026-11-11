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
    SystemPowerStore: { batteryState: { bHasBattery: true, eACState: 0, flLevel: 1 } },
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
