import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("../src/tabs/Settings.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    jsx: ts.JsxEmit.ReactJSX,
  },
}).outputText;

type Toast = { title: string; body?: string };
type El = { type: any; props: any } | string | null;

function isEl(v: any): v is { type: any; props: any } {
  return v !== null && typeof v === "object" && "type" in v && "props" in v;
}

function walk(node: any, out: El[] = []): El[] {
  if (Array.isArray(node)) {
    for (const c of node) walk(c, out);
  } else if (isEl(node)) {
    out.push(node);
    walk(node.props?.children, out);
  }
  return out;
}

function findToggleByLabel(rendered: any, labelMatcher: (s: string) => boolean): El | undefined {
  return walk(rendered).find(
    (n) => isEl(n) && n.type && (n.type as any).__isToggleRow && labelMatcher(String(n.props?.label ?? "")),
  ) as El | undefined;
}

function createHarness(opts: {
  initialConfig: any;
  getConfigImpl: (call: number) => any;
}) {
  const toasts: Toast[] = [];
  const messages: Record<string, string> = {};
  const backendCalls: { fn: string; arg?: any }[] = [];
  let getConfigCalls = 0;

  let state: any = { ...opts.initialConfig };
  const setConfig = (updater: any) => {
    state = typeof updater === "function" ? updater(state) : updater;
  };

  const hooks: any[] = [];
  let hookIndex = 0;

  const useState = (init: any) => {
    const i = hookIndex++;
    if (!(i in hooks)) hooks[i] = { value: typeof init === "function" ? init() : init };
    const slot = hooks[i];
    return [slot.value, (next: any) => {
      slot.value = typeof next === "function" ? next(slot.value) : next;
    }];
  };
  const useRef = (init: any) => {
    const i = hookIndex++;
    if (!(i in hooks)) hooks[i] = { current: init };
    return hooks[i];
  };
  const useEffect = (_fn: any, _deps: any) => {
    hookIndex += 0;
  };
  const useDebouncedApply = (..._args: any[]) => () => {};

  const t = (key: string) => messages[key] ?? key;

  messages["settings.usbSettingsRefreshError"] = "Could not refresh USB status";
  messages["settings.retryUsbSettings"] = "Retry USB status refresh";
  messages["settings.usbSettingsError"] = "Could not apply USB settings";
  messages["settings.usbNetwork"] = "Enable USB SSH";
  messages["settings.usbFileTransfer"] = "USB File Transfer";

  const noop = async (arg?: any) => arg;
  const backend: any = {
    getBottomScreenActive: async () => false,
    getConfig: async () => {
      getConfigCalls++;
      return opts.getConfigImpl(getConfigCalls);
    },
    getSleepLogsEnabled: async () => false,
    setBottomScreenBrightness: noop,
    setBottomScreenEnabled: noop,
    setChargingFanPwm: noop,
    setDesktopMode: noop,
    setSleepMode: noop,
    setSleepLogsEnabled: noop,
    setSshEnabled: noop,
    setSwipeGesturesEnabled: noop,
    setSwipeGesturesInGameEnabled: noop,
    setUsbNetworkEnabled: async (arg: any) => {
      backendCalls.push({ fn: "setUsbNetworkEnabled", arg });
      return arg;
    },
    setMtpEnabled: async (arg: any) => {
      backendCalls.push({ fn: "setMtpEnabled", arg });
      return arg;
    },
  };

  const components: any = {
    GameModeOrientationSetting: () => null,
    HomePowerSetting: () => null,
    ToggleRow: Object.assign((props: any) => ({ __toggle: true, props }), { __isToggleRow: true }),
    SelectEdit: () => null,
    SliderEdit: () => null,
  };

  const fakeRequire = (id: string) => {
    if (id === "@decky/api") return { toaster: { toast: (t: Toast) => { toasts.push(t); } } };
    if (id === "@decky/ui") return { Field: () => null, PanelSection: (props: any) => ({ __section: true, props }) };
    if (id === "react") {
      return {
        useEffect,
        useRef,
        useState,
        createElement: (type: any, props: any, ...children: any[]) => ({ type, props: { ...props, children } }),
      };
    }
    if (id === "react/jsx-runtime" || id === "react/jsx-dev-runtime") {
      return {
        jsx: (type: any, props: any, key?: any) => ({ type, props: { ...props, ...(key !== undefined ? { key } : {}) } }),
        jsxs: (type: any, props: any, key?: any) => ({ type, props: { ...props, ...(key !== undefined ? { key } : {}) } }),
        Fragment: "Fragment",
      };
    }
    if (id === "../backend") return backend;
    if (id === "../components/GameModeOrientationSetting") return { GameModeOrientationSetting: components.GameModeOrientationSetting };
    if (id === "../components/HomePowerSetting") return { HomePowerSetting: components.HomePowerSetting };
    if (id === "../components/widgets") return {
      ButtonRow: "ButtonRow",
      SelectEdit: components.SelectEdit,
      SliderEdit: components.SliderEdit,
      ToggleRow: components.ToggleRow,
    };
    if (id === "../hooks/useDebouncedApply") return { useDebouncedApply };
    if (id === "../i18n") return { t, translateLabel: (s: any) => s };
    if (id === "../lib/fanCurve") return { percentToPwm: (v: number) => v, pwmToPercent: (v: number) => v };
    if (id === "../lib/lowPowerDownloads") return {
      getLowPowerDownloadSettings: () => ({ enabled: false, onBattery: false }),
      setLowPowerDownloadsEnabled: async () => {},
      setLowPowerDownloadsOnBattery: async () => {},
    };
    if (id === "../types") return {};
    throw new Error("unexpected require: " + id);
  };

  const exports: any = {};
  runInNewContext(compiled, {
    exports,
    module: { exports },
    require: fakeRequire,
    console,
    setTimeout,
    clearTimeout,
    setInterval: () => 0,
    clearInterval: () => {},
    process,
  });

  const render = () => {
    hookIndex = 0;
    return exports.Settings({ config: state, setConfig });
  };

  return {
    toasts,
    backendCalls,
    getState: () => state,
    getConfigCalls: () => getConfigCalls,
    render,
  };
}

async function flush(): Promise<void> {
  for (let i = 0; i < 10; i++) await Promise.resolve();
}

function baseConfig(mtpEnabled: boolean) {
  return {
    mtpEnabled,
    usbNetworkEnabled: false,
    usbNetworkSupported: true,
    usbNetworkPaused: mtpEnabled,
    sshEnabled: false,
    bottomScreenBrightnessSupported: false,
    bottomScreenSupported: false,
    fanSupported: false,
    swipeGesturesEnabled: false,
    swipeGesturesInGameEnabled: false,
    sleepMode: "s2idle",
    desktopMode: "desktop",
  };
}

function isUsbSshToggle(n: El | undefined): boolean {
  return !!n && /USB SSH/.test(String(n.props?.label ?? ""));
}

function isUsbFileTransferToggle(n: El | undefined): boolean {
  return !!n && /USB File Transfer/.test(String(n.props?.label ?? ""));
}

function findRetryButton(rendered: any): El | undefined {
  return walk(rendered).find(
    (n) => isEl(n) && n.type === "ButtonRow" && n.props?.children === "Retry USB status refresh",
  ) as El | undefined;
}

test("successful MTP stop with failing readback keeps MTP off, locks USB SSH pending authoritative state, notifies, and allows retry", async () => {
  const initial = baseConfig(true);
  const harness = createHarness({
    initialConfig: initial,
    getConfigImpl: (call) => {
      if (call === 1) throw new Error("config read failed");
      if (call === 2) throw new Error("config read failed again");
      return { ...baseConfig(false), usbNetworkEnabled: true };
    },
  });

  let rendered = harness.render();
  const mtpToggle = findToggleByLabel(rendered, (s) => /USB File Transfer/.test(s));
  assert.ok(isUsbFileTransferToggle(mtpToggle), "usb file transfer toggle must render");
  assert.equal(mtpToggle!.props.value, true);

  await mtpToggle!.props.onChange(false);
  await flush();

  assert.equal(harness.backendCalls.filter((c) => c.fn === "setMtpEnabled").length, 1, "exactly one MTP mutation");
  assert.equal(harness.backendCalls.filter((c) => c.fn === "setUsbNetworkEnabled").length, 0, "no USB SSH mutation");

  const messages = harness.toasts.map((t) => t.title);
  assert.ok(
    messages.includes("Could not refresh USB status"),
    `expected refresh error toast, got ${JSON.stringify(harness.toasts)}`,
  );

  rendered = harness.render();

  const mtpAfter = findToggleByLabel(rendered, (s) => /USB File Transfer/.test(s));
  assert.equal(mtpAfter!.props.value, false, "MTP result kept despite readback failure");

  const usbSshAfter = findToggleByLabel(rendered, (s) => /USB SSH/.test(s));
  assert.ok(isUsbSshToggle(usbSshAfter), "USB SSH toggle must render");
  assert.equal(usbSshAfter!.props.disabled, true, "USB SSH must be locked pending authoritative state");

  let retry = findRetryButton(rendered);
  assert.ok(retry, "retry button must appear after failed readback");

  await retry!.props.onClick();
  await flush();

  rendered = harness.render();
  retry = findRetryButton(rendered);
  assert.ok(retry, "retry button must persist after another failed retry");

  await retry!.props.onClick();
  await flush();

  rendered = harness.render();
  assert.equal(findRetryButton(rendered), undefined, "retry button must disappear after success");

  const usbSshFinal = findToggleByLabel(rendered, (s) => /USB SSH/.test(s));
  assert.equal(usbSshFinal!.props.value, true);
  assert.equal(usbSshFinal!.props.disabled, false, "USB SSH restored to authoritative unlocked state");

  assert.equal(harness.backendCalls.filter((c) => c.fn === "setMtpEnabled").length, 1, "still exactly one MTP mutation");
  assert.equal(harness.backendCalls.filter((c) => c.fn === "setUsbNetworkEnabled").length, 0, "no USB SSH mutation after retries");
});
