import test from "node:test";
import assert from "node:assert/strict";
import {
  getAppResolution,
  getGlobalResolution,
  initializeGlobalResolution,
  resolutionOptions,
  setGlobalResolution,
} from "../src/lib/steamSettings.ts";

const KEY = "gamescope_game_resolution_global";

type Setter = (value: string) => unknown;

function install(opts: {
  store?: Map<string, string>;
  persist?: Map<string, string>;
  updateStoreOnSet?: boolean;
  rejectOnce?: string;
  waitServices?: () => Promise<void>;
} = {}) {
  const store = opts.store ?? new Map<string, string>();
  const persist = opts.persist ?? new Map<string, string>();
  const calls: Array<{ key: string; value: string }> = [];
  let pendingReject = opts.rejectOnce;
  const fake: any = {
    settingsStore: {
      GetClientSetting(key: string) {
        if (key !== KEY) return undefined;
        const setter: Setter = async (value: string) => {
          if (pendingReject !== undefined) {
            const reason = pendingReject;
            pendingReject = undefined;
            throw new Error(reason);
          }
          calls.push({ key, value });
          if (opts.updateStoreOnSet !== false) store.set(key, value);
        };
        return [store.get(key), setter];
      },
    },
    localStorage: {
      getItem: (key: string) => persist.get(key) ?? null,
      setItem: (key: string, value: string) => {
        persist.set(key, value);
      },
    },
    App: { WaitForServicesInitialized: opts.waitServices ?? (async () => {}) },
  };
  (globalThis as any).window = fake;
  return { fake, store, persist, calls };
}

let prev: unknown;
test.beforeEach(() => {
  prev = (globalThis as any).window;
});
test.afterEach(() => {
  if (prev === undefined) delete (globalThis as any).window;
  else (globalThis as any).window = prev;
});

test("legacy global values reset to 1280x720 and write store", async () => {
  for (const legacy of [undefined, "", "Native", "Default"] as const) {
    const store = new Map<string, string>();
    if (legacy !== undefined) store.set(KEY, legacy);
    const { calls } = install({ store });
    assert.equal(await initializeGlobalResolution(), "1280x720");
    assert.deepEqual(calls, [{ key: KEY, value: "1280x720" }]);
    assert.equal(store.get(KEY), "1280x720");
  }
});

test("setter write accepted even when store has not refreshed", async () => {
  const { calls, store } = install({ updateStoreOnSet: false });
  assert.equal(await initializeGlobalResolution(), "1280x720");
  assert.deepEqual(calls, [{ key: KEY, value: "1280x720" }]);
  assert.equal(store.has(KEY), false);
});

test("explicit presets preserved", async () => {
  for (const preset of ["960x540", "1920x1080", "2560x1440"]) {
    const store = new Map<string, string>([[KEY, preset]]);
    install({ store });
    assert.equal(await initializeGlobalResolution(), preset);
    assert.equal(store.get(KEY), preset);
  }
});

test("explicit Native/Default selected post-startup survives restart marker", async () => {
  const persist = new Map<string, string>();
  const { store } = install({ persist });
  assert.equal(await initializeGlobalResolution(), "1280x720");
  assert.equal(persist.get("armada-resolution-preset-applied"), "1");
  for (const explicit of ["Native", "Default"]) {
    store.set(KEY, explicit);
    const { calls } = install({ store, persist });
    assert.equal(await initializeGlobalResolution(), explicit);
    assert.equal(store.get(KEY), explicit);
    assert.deepEqual(calls, []);
  }
});

test("failed setter surfaces, retry with same storage succeeds", async () => {
  const { store, persist, calls } = install({ rejectOnce: "setter rejected" });
  await assert.rejects(() => initializeGlobalResolution(), /setter rejected/);
  assert.equal(persist.size, 0);
  assert.equal(calls.length, 0);
  assert.equal(await initializeGlobalResolution(), "1280x720");
  assert.deepEqual(calls, [{ key: KEY, value: "1280x720" }]);
  assert.equal(store.get(KEY), "1280x720");
});

test("resolutionOptions exposes shared data set", () => {
  const datas = new Set(resolutionOptions.map((o) => o.data));
  for (const value of ["Default", "Native", "1280x720", "960x540", "1920x1080", "2560x1440"]) {
    assert.ok(datas.has(value), `missing ${value}`);
  }
});

test("getAppResolution prefers direct SteamClient getter", async () => {
  const { fake } = install();
  let detailCalled = false;
  let seen: number | undefined;
  fake.SteamClient = {
    Apps: {
      GetResolutionOverrideForApp: async (appid: number) => {
        seen = appid;
        return "1920x1080";
      },
    },
  };
  fake.appDetailsStore = {
    RequestAppDetails: async () => {
      detailCalled = true;
      return { strResolutionOverride: "2560x1440" };
    },
  };
  assert.equal(await getAppResolution(42), "1920x1080");
  assert.equal(seen, 42);
  assert.equal(detailCalled, false);
});

test("getAppResolution uses appDetailsStore when direct missing", async () => {
  const { fake } = install();
  fake.SteamClient = { Apps: {} };
  let seen: number | undefined;
  fake.appDetailsStore = {
    RequestAppDetails: async (appid: number) => {
      seen = appid;
      return { strResolutionOverride: appid === 7 ? "2560x1440" : "" };
    },
  };
  assert.equal(await getAppResolution(7), "2560x1440");
  assert.equal(seen, 7);
});

test("getAppResolution falls back to Default", async () => {
  const { fake } = install();
  fake.SteamClient = { Apps: {} };
  fake.appDetailsStore = { RequestAppDetails: async () => ({ strResolutionOverride: "" }) };
  assert.equal(await getAppResolution(99), "Default");
  delete fake.appDetailsStore;
  assert.equal(await getAppResolution(1), "Default");
  delete fake.SteamClient;
  assert.equal(await getAppResolution(1), "Default");
});

test("getGlobalResolution and setGlobalResolution round-trip", async () => {
  const { store } = install();
  store.set(KEY, "960x540");
  assert.equal(getGlobalResolution(), "960x540");
  assert.equal(await setGlobalResolution("1920x1080"), "1920x1080");
});

test("initialize coalesces concurrent callers after readiness", async () => {
  let resolveReady: () => void = () => {};
  const ready = new Promise<void>((r) => {
    resolveReady = r;
  });
  const { calls } = install({ waitServices: () => ready });
  const a = initializeGlobalResolution();
  const b = initializeGlobalResolution();
  assert.equal(a, b);
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(calls, []);
  resolveReady();
  assert.equal(await a, "1280x720");
  assert.deepEqual(calls, [{ key: KEY, value: "1280x720" }]);
  const c = initializeGlobalResolution();
  assert.notEqual(a, c);
  await c;
});
