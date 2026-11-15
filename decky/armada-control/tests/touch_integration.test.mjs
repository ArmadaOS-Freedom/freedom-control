// Controller <-> Touch page integration checks. Everything runs real code: the
// touch api module is loaded against a recording @decky/api stub, its shared
// mutation queue is exercised directly and again behind the shipped page, the
// touch dictionaries and decision helpers are transpiled from source, the
// plugin lifecycle is executed against stubbed router/Router/window globals,
// and the exported Content and TouchContent components are rendered against a
// minimal React hook shim, so tab order, lazy mounting, the global heartbeat
// and the page controls are read from the returned element tree, not from
// source text. The fullscreen LayoutEditor is mounted on the same runtime, so
// a save that outlives the editor it started in is checked against its real
// lifecycle effect and the real api queue.

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Buffer } from "node:buffer";
import ts from "typescript";

const compilerOptions = {
  module: ts.ModuleKind.ESNext,
  target: ts.ScriptTarget.ES2020,
  jsx: ts.JsxEmit.ReactJSX,
};

const transpile = (source, fileName) =>
  ts.transpileModule(source, { compilerOptions, fileName }).outputText;

const asDataUrl = (js) => `data:text/javascript;base64,${Buffer.from(js, "utf8").toString("base64")}`;

const toModuleUrl = (source, fileName = "module.ts") => asDataUrl(transpile(source, fileName));

const readSource = (relativePath) => readFile(new URL(relativePath, import.meta.url), "utf8");

const settle = async () => {
  for (let turn = 0; turn < 4; turn += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
};

// The real touch dictionary, used only as data.
const touchI18nUrl = toModuleUrl(await readSource("../src/touch/i18n.ts"), "touch-i18n.ts");
const touchI18n = await import(touchI18nUrl);
const touch = (language, key, variables) => touchI18n.translate(language, key, variables);

// The Controller's own dictionary, so the outer tab label is checked against
// the same tables the panel ships with.
const localeUrls = Object.fromEntries(await Promise.all(
  ["en", "zh-CN", "pt-BR", "pt-PT"].map(async (locale) => [
    `./locales/${locale}`,
    toModuleUrl(await readSource(`../src/locales/${locale}.ts`), `${locale}.ts`),
  ]),
));
let controllerI18nJs = transpile(await readSource("../src/i18n.ts"), "i18n.ts");
for (const [specifier, url] of Object.entries(localeUrls)) {
  controllerI18nJs = controllerI18nJs.replace(`from "${specifier}"`, `from "${url}"`);
}
const controllerI18nUrl = asDataUrl(controllerI18nJs);
const controllerI18n = await import(controllerI18nUrl);

// ---------------------------------------------------------------------------
// Touch api: every callable is addressed through its prefixed RPC name
// ---------------------------------------------------------------------------

globalThis.__touchCalls = [];
globalThis.__touchOutcome = () => ({ ok: true });
const apiStubUrl = toModuleUrl(`
  export const callable = (name) => (...args) => {
    globalThis.__touchCalls.push([name, ...args]);
    return Promise.resolve(globalThis.__touchOutcome(name, args));
  };
`);

const touchApi = await import(toModuleUrl(
  (await readSource("../src/touch/api.ts")).replace(
    /from ["']@decky\/api["']/,
    `from "${apiStubUrl}"`,
  ),
  "touch-api.ts",
));

const state = { settings: { enabled: true }, status: {}, presets: [] };
const layout = [{ id: "a", kind: "button", output: "a", x: 0, y: 0, w: 0.1, h: 0.1 }];
globalThis.__touchOutcome = () => state;

assert.deepEqual(await touchApi.fetchState(), state);
assert.deepEqual(await touchApi.setSettings({ version: 2 }), state);
assert.deepEqual(await touchApi.updateOptions({ opacity: 0.5 }), state);
assert.deepEqual(await touchApi.selectPreset("preset-2"), state);
assert.deepEqual(await touchApi.savePreset("Mine", layout, 1, null), state);
assert.deepEqual(await touchApi.resetLayout(), state);
assert.deepEqual(await touchApi.sendHeartbeat("42"), state);
assert.deepEqual(await touchApi.sendHeartbeat(null), state);
assert.deepEqual(await touchApi.previewHaptics(), state);
assert.deepEqual(await touchApi.setControlsVisible(false), state);

// Argument order is forwarded verbatim and no legacy unprefixed name survives.
assert.deepEqual(globalThis.__touchCalls, [
  ["get_touch_state"],
  ["set_touch_settings", { version: 2 }],
  ["update_touch_options", { opacity: 0.5 }],
  ["select_touch_preset", "preset-2"],
  ["save_touch_preset", "Mine", layout, 1, null],
  ["reset_touch_layout"],
  ["touch_heartbeat", "42"],
  ["touch_heartbeat", null],
  ["preview_touch_haptics"],
  ["set_touch_controls_visible", false],
]);

// A rejected transport call reaches the caller as the same rejection.
const transportClosed = new Error("RPC transport closed");
globalThis.__touchOutcome = () => Promise.reject(transportClosed);
await assert.rejects(touchApi.fetchState(), (error) => error === transportClosed);
await assert.rejects(touchApi.sendHeartbeat(null), (error) => error === transportClosed);

// ---------------------------------------------------------------------------
// Shared mutation queue: accepted work survives its page, in call order
// ---------------------------------------------------------------------------

const queueCalls = [];
const queuePending = [];
globalThis.__queueCalls = queueCalls;
globalThis.__queueDispatch = (name, args) =>
  new Promise((resolve, reject) => {
    queuePending.push({ name, args, resolve, reject });
  });
const queueApiStubUrl = toModuleUrl(`
  export const callable = (name) => (...args) => {
    globalThis.__queueCalls.push([name, ...args]);
    return globalThis.__queueDispatch(name, args);
  };
`);
const queueApi = await import(toModuleUrl(
  (await readSource("../src/touch/api.ts")).replace(
    /from ["']@decky\/api["']/,
    `from "${queueApiStubUrl}"`,
  ),
  "touch-queue-api.ts",
));

const queueResponse = (tag) => ({ tag, settings: {}, status: {}, presets: [] });
const takePending = (name) => {
  const index = queuePending.findIndex((entry) => entry.name === name);
  assert.ok(index >= 0, `the ${name} call is in flight`);
  return queuePending.splice(index, 1)[0];
};

// Patches accepted before the batch reaches the wire share one write, and all
// of their callers receive that write's response.
const mergedA = queueApi.updateOptions({ opacity: 0.2 });
const mergedB = queueApi.updateOptions({ opacity: 0.3 });
const mergedC = queueApi.updateOptions({ opacity: 0.3, scale: 1.1 });
await settle();
assert.deepEqual(
  queueCalls,
  [["update_touch_options", { opacity: 0.3, scale: 1.1 }]],
  "adjacent patches are merged into the slot the first action took",
);
const mergedAnswer = queueResponse("merged");
queuePending.shift().resolve(mergedAnswer);
await settle();
for (const caller of [mergedA, mergedB, mergedC]) {
  assert.equal(await caller, mergedAnswer, "every merged caller sees the same response");
}

// A snapshot read never overtakes a mutation accepted before it.
const callsBeforeRead = queueCalls.length;
const waitingWrite = queueApi.updateOptions({ deadzone: 0.2 });
await settle();
const waitingRead = queueApi.fetchState();
await settle();
assert.deepEqual(queueCalls.slice(-1), [["update_touch_options", { deadzone: 0.2 }]]);
assert.equal(queueCalls.length, callsBeforeRead + 1, "the read waits for the accepted write");
queuePending.shift().resolve(queueResponse("write"));
await settle();
assert.deepEqual(queueCalls.at(-1), ["get_touch_state"], "the read runs once the write finished");
const readAnswer = queueResponse("read");
queuePending.shift().resolve(readAnswer);
assert.equal(await waitingRead, readAnswer);
await waitingWrite;

// A failed mutation rejects only itself; the queue keeps running.
const rejectedWrite = queueApi.updateOptions({ opacity: 0.1 });
const rejectedCheck = assert.rejects(rejectedWrite, (error) => error === transportClosed);
await settle();
queuePending.shift().reject(transportClosed);
await rejectedCheck;
const writeAfterFailure = queueApi.setSettings({ version: 2 });
await settle();
assert.deepEqual(queueCalls.at(-1), ["set_touch_settings", { version: 2 }], "the next write still runs");
const settingsAnswer = queueResponse("settings");
queuePending.shift().resolve(settingsAnswer);
assert.equal(await writeAfterFailure, settingsAnswer);

// A layout mutation is a merge barrier: a patch made after it must not join
// the earlier slot and overtake it.
const callsBeforeBarrier = queueCalls.length;
const beforeBarrier = queueApi.updateOptions({ opacity: 0.4 });
await settle();
const barrierReset = queueApi.resetLayout();
const afterBarrier = queueApi.updateOptions({ opacity: 0.5 });
await settle();
assert.equal(queueCalls.length, callsBeforeBarrier + 1, "only the first write reached the wire");
queuePending.shift().resolve(queueResponse("first"));
await settle();
assert.deepEqual(queueCalls.at(-1), ["reset_touch_layout"], "the layout mutation keeps its place");
queuePending.shift().resolve(queueResponse("reset"));
await settle();
assert.deepEqual(
  queueCalls.at(-1),
  ["update_touch_options", { opacity: 0.5 }],
  "the later patch takes its own slot instead of merging backwards",
);
queuePending.shift().resolve(queueResponse("second"));
await settle();
await Promise.all([beforeBarrier, barrierReset, afterBarrier]);
assert.equal(queuePending.length, 0, "the barrier case left nothing in flight");

// The heartbeat and the haptics preview never wait behind a mutation.
const slowWrite = queueApi.updateOptions({ opacity: 0.9 });
await settle();
const heartbeat = queueApi.sendHeartbeat("42");
await settle();
assert.deepEqual(queueCalls.at(-1), ["touch_heartbeat", "42"]);
const heartbeatAnswer = queueResponse("heartbeat");
takePending("touch_heartbeat").resolve(heartbeatAnswer);
assert.equal(await heartbeat, heartbeatAnswer);
takePending("update_touch_options").resolve(queueResponse("slow"));
await slowWrite;
assert.equal(queuePending.length, 0, "the api queue is idle again");

// ---------------------------------------------------------------------------
// Global lifecycle: route registration and the 1 Hz heartbeat
// ---------------------------------------------------------------------------

const routePath = "/freedom-control/touch-layout";
const editorStateUrl = toModuleUrl(await readSource("../src/touch/editor-state.ts"), "editor-state.ts");
const routeUrl = toModuleUrl(await readSource("../src/touch/route.ts"), "route.ts");
const editorState = await import(editorStateUrl);

const router = { added: [], removed: [] };
globalThis.__touchRouter = {
  addRoute: (path, component, options) => router.added.push([path, component, options]),
  removeRoute: (path) => router.removed.push(path),
};
globalThis.__touchMainApp = { appid: 4242 };
globalThis.__touchRouterState = {
  get MainRunningApp() {
    return globalThis.__touchMainApp;
  },
};

const timers = { next: 1, intervals: new Map() };
globalThis.window = {
  setInterval: (callback, delay) => {
    const id = timers.next;
    timers.next += 1;
    timers.intervals.set(id, { callback, delay });
    return id;
  },
  clearInterval: (id) => {
    timers.intervals.delete(id);
  },
};

globalThis.__touchHeartbeats = [];
globalThis.__touchHeartbeatResult = () => Promise.resolve({});
const integrationStubs = {
  "@decky/api": `export const routerHook = globalThis.__touchRouter;`,
  "@decky/ui": `export const Router = globalThis.__touchRouterState;`,
  "./api": `export const sendHeartbeat = (appid) => {
    globalThis.__touchHeartbeats.push(appid);
    return globalThis.__touchHeartbeatResult(appid);
  };`,
  "./editor-state": editorStateUrl,
  "./LayoutEditor": `const LayoutEditor = "LayoutEditor";
export default LayoutEditor;`,
  "./route": routeUrl,
};
const integrationUrls = Object.fromEntries(
  Object.entries(integrationStubs).map(([specifier, value]) => [
    specifier,
    value.startsWith("data:") ? value : asDataUrl(value),
  ]),
);

const integrationJs = transpile(
  await readSource("../src/touch/integration.tsx"),
  "integration.tsx",
).replace(/from ["']([^"']+)["']/g, (_match, specifier) => {
  const url = integrationUrls[specifier];
  if (!url) throw new Error(`integration.tsx imports an unstubbed module: ${specifier}`);
  return `from "${url}"`;
});
const integration = await import(asDataUrl(integrationJs));

const dispose = integration.registerTouchController();

// The layout route is added once, with the editor component and an exact match.
assert.deepEqual(router.added, [[routePath, "LayoutEditor", { exact: true }]]);

// One 1 Hz timer is started and the first heartbeat names the running game.
assert.equal(timers.intervals.size, 1, "the plugin owns exactly one heartbeat timer");
const heartbeatTimer = [...timers.intervals.values()][0];
assert.equal(heartbeatTimer.delay, 1000);
await settle();
assert.deepEqual(globalThis.__touchHeartbeats, ["4242"]);

const tick = async () => {
  heartbeatTimer.callback();
  await settle();
};

await tick();
assert.deepEqual(globalThis.__touchHeartbeats, ["4242", "4242"]);

// The editor owns capture release: the heartbeat sends null while it is open.
editorState.beginLayoutEditing();
await tick();
assert.equal(globalThis.__touchHeartbeats.at(-1), null);
editorState.endLayoutEditing();
await tick();
assert.equal(globalThis.__touchHeartbeats.at(-1), "4242");

// No running game is also null, and a numeric appid is stringified.
globalThis.__touchMainApp = undefined;
await tick();
assert.equal(globalThis.__touchHeartbeats.at(-1), null);
globalThis.__touchMainApp = { appid: "777" };
await tick();
assert.equal(globalThis.__touchHeartbeats.at(-1), "777");

// ---------------------------------------------------------------------------
// Content tab: executed render
// ---------------------------------------------------------------------------

const reactStubUrl = toModuleUrl(`
  const store = () => globalThis.__touchHooks;
  export function useState(initial) {
    const state = store();
    const index = state.stateCursor++;
    if (index >= state.states.length) state.states.push(initial);
    const set = (next) => {
      const current = state.states[index];
      state.states[index] = typeof next === "function" ? next(current) : next;
    };
    return [state.states[index], set];
  }
  export function useRef(initial) {
    const state = store();
    const index = state.refCursor++;
    if (index >= state.refs.length) state.refs.push({ current: initial });
    return state.refs[index];
  }
  export function useCallback(fn) { return fn; }
  export function useMemo(fn) { return fn(); }
  export function useEffect() {}
`);

const contentStubs = {
  "@decky/ui": `export const Field = "Field";
export const PanelSection = "PanelSection";
export const Tabs = "Tabs";`,
  "react/jsx-runtime": `export const Fragment = "Fragment";
export const jsx = (type, props) => ({ type, props: props || {} });
export const jsxs = jsx;
export const jsxDEV = jsx;`,
  "./backend": `export const getConfig = async () => ({});
export const getInstalledGames = async () => [];
export const savePowerConfig = async () => {};
export const saveTweaks = async () => {};`,
  "./components/RgbLighting": `export const RgbLighting = "RgbLighting";`,
  "./hooks/useDebouncedSave": `export const useDebouncedSave = () => {};`,
  "./hooks/useLocale": `export const useLocale = () => globalThis.__touchControllerLocale;`,
  "./i18n": controllerI18nUrl,
  "./icons": `export const tabIcons = { Compatibility: {}, Power: {}, Touch: {}, Fans: {}, RGB: {}, TF: {}, Advanced: {} };`,
  "./lib/games": `export const currentGame = () => null;`,
  "./styles": `export const styles = "";`,
  "./tabs/Compatibility": `export const Compatibility = "Compatibility";`,
  "./tabs/Fans": `export const Fans = "Fans";`,
  "./tabs/Power": `export const Power = "Power";`,
  "./tabs/Settings": `export const Settings = "Settings";`,
  "./tf/TfLibraryContent": `export const TfLibraryContent = "TfLibraryContent";`,
  "./tf/tf_i18n": `export const tfText = () => "tf";`,
  "./touch/TouchContent": `export const TouchContent = "TouchContent";`,
};
const contentUrls = {
  react: reactStubUrl,
  ...Object.fromEntries(
    Object.entries(contentStubs).map(([specifier, value]) => [
      specifier,
      value.startsWith("data:") ? value : asDataUrl(value),
    ]),
  ),
};

const contentJs = transpile(await readSource("../src/Content.tsx"), "Content.tsx").replace(
  /from ["']([^"']+)["']/g,
  (_match, specifier) => {
    const url = contentUrls[specifier];
    if (!url) throw new Error(`Content.tsx imports an unstubbed module: ${specifier}`);
    return `from "${url}"`;
  },
);
const { Content } = await import(asDataUrl(contentJs));

// Element objects are { type, props }; a Tabs element carries its pages in the
// props.tabs list rather than in children, so both are followed.
const walk = (node, visit) => {
  if (Array.isArray(node)) {
    node.forEach((child) => walk(child, visit));
    return;
  }
  if (!node || typeof node !== "object") return;
  visit(node);
  const props = node.props || {};
  walk(props.children, visit);
  if (Array.isArray(props.tabs)) props.tabs.forEach((tab) => walk(tab.content, visit));
};

const findAll = (tree, predicate) => {
  const found = [];
  walk(tree, (node) => {
    if (predicate(node)) found.push(node);
  });
  return found;
};

const tabsOf = (tree) => findAll(tree, (node) => Array.isArray(node.props && node.props.tabs))[0];
const tabIds = (tabsElement) => tabsElement.props.tabs.map((tab) => tab.id);
const tabById = (tabsElement, id) => tabsElement.props.tabs.find((tab) => tab.id === id);
const touchPages = (tree) => findAll(tree, (node) => node.type === "TouchContent");

const baseConfig = { fanSupported: true, rgbSupported: true, installedGames: [], power: {}, tweaks: {} };

const renderController = () => {
  const hooks = globalThis.__touchHooks;
  hooks.stateCursor = 0;
  hooks.refCursor = 0;
  return Content();
};

const mountContent = (locale, config = baseConfig) => {
  globalThis.__touchControllerLocale = locale;
  controllerI18n.setCurrentLocale(locale);
  globalThis.__touchHooks = {
    states: ["Compatibility", config, "Loading"],
    stateCursor: 0,
    refCursor: 0,
    refs: [],
  };
  return renderController();
};

// Touch sits after Power, ahead of the optional pages, TF and Advanced; every
// previous page keeps its place and Compatibility stays the default.
const full = tabsOf(mountContent("en"));
assert.deepEqual(tabIds(full), ["Compatibility", "Power", "Touch", "Fans", "RGB", "TF", "Advanced"]);
const bare = tabsOf(mountContent("en", { ...baseConfig, fanSupported: false, rgbSupported: false }));
assert.deepEqual(tabIds(bare), ["Compatibility", "Power", "Touch", "TF", "Advanced"]);
const rgbOnly = tabsOf(mountContent("en", { ...baseConfig, fanSupported: false, rgbSupported: true }));
assert.deepEqual(tabIds(rgbOnly), ["Compatibility", "Power", "Touch", "RGB", "TF", "Advanced"]);

// Every page keeps the same plain, auto-height body wrapper; the Touch page no
// longer opts into a height chain. This is element structure, not a layout
// measurement; the real geometry is checked on the device DOM.
const bodyClass = (tabsEl, id) => tabById(tabsEl, id).content.props.className;
for (const id of ["Compatibility", "Power", "Touch", "Fans", "RGB", "TF", "Advanced"]) {
  assert.equal(bodyClass(full, id), "armada-control-tab-content", `${id} keeps the plain body`);
}

// A fresh mount keeps Compatibility active and renders no touch page, so no
// state poll can run before the tab is opened.
let tabsElement = tabsOf(mountContent("en"));
assert.equal(tabsElement.props.activeTab, "Compatibility");
assert.equal(tabById(tabsElement, "Touch").content.props.children, null);
assert.equal(touchPages(tabsElement).length, 0);
assert.equal(
  tabById(tabsElement, "Touch").title.props["aria-label"],
  controllerI18n.translate("en", "touch.title"),
);

// Opening the tab mounts exactly one page with the mapped locale, and leaving
// it unmounts the page again.
tabsElement.props.onShowTab("Touch");
tabsElement = tabsOf(renderController());
assert.equal(tabsElement.props.activeTab, "Touch");
let pages = touchPages(tabsElement);
assert.equal(pages.length, 1);
assert.equal(tabById(tabsElement, "Touch").content.props.children, pages[0]);
assert.equal(pages[0].props.locale, "en");

tabsElement.props.onShowTab("Power");
tabsElement = tabsOf(renderController());
assert.equal(tabsElement.props.activeTab, "Power");
assert.equal(tabById(tabsElement, "Touch").content.props.children, null);
assert.equal(touchPages(tabsElement).length, 0);

// Controller locale -> touch language: only zh-CN is translated, every other
// locale reads the page in English; the tab label follows the panel language.
for (const [controllerLocale, expected] of [
  ["zh-CN", "zh"],
  ["en", "en"],
  ["pt-BR", "en"],
  ["pt-PT", "en"],
]) {
  let current = tabsOf(mountContent(controllerLocale));
  current.props.onShowTab("Touch");
  current = tabsOf(renderController());
  pages = touchPages(current);
  assert.equal(pages.length, 1, `${controllerLocale} mounts one touch page`);
  assert.equal(pages[0].props.locale, expected, `${controllerLocale} renders the touch page as ${expected}`);
  assert.equal(
    tabById(current, "Touch").title.props["aria-label"],
    controllerI18n.translate(controllerLocale, "touch.title"),
    `${controllerLocale} labels the touch tab in its own language`,
  );
}
assert.equal(controllerI18n.translate("zh-CN", "touch.title"), "触屏手柄");

// Mounting and switching pages never adds or removes the global heartbeat.
assert.equal(timers.intervals.size, 1, "panel pages do not own the heartbeat");
const beatsBeforePages = globalThis.__touchHeartbeats.length;
await tick();
assert.equal(globalThis.__touchHeartbeats.length, beatsBeforePages + 1);
assert.equal(globalThis.__touchHeartbeats.at(-1), "777");

// ---------------------------------------------------------------------------
// Lifecycle cleanup: timers, late ticks and the final heartbeat
// ---------------------------------------------------------------------------

// A heartbeat already in flight when the plugin dismounts must not send again,
// and the cleared interval callback must be inert.
let releaseHeartbeat;
globalThis.__touchHeartbeatResult = () => new Promise((resolve) => {
  releaseHeartbeat = resolve;
});
heartbeatTimer.callback();
await settle();
const beatsBeforeDispose = globalThis.__touchHeartbeats.length;

dispose();

assert.equal(timers.intervals.size, 0, "cleanup clears the heartbeat timer");
assert.deepEqual(router.removed, [routePath], "cleanup removes only the touch layout route");
assert.equal(editorState.isLayoutEditing(), false, "cleanup ends layout editing");
assert.equal(globalThis.__touchHeartbeats.length, beatsBeforeDispose + 1, "cleanup sends one final heartbeat");
assert.equal(globalThis.__touchHeartbeats.at(-1), null, "the final heartbeat releases capture");

releaseHeartbeat({});
globalThis.__touchHeartbeatResult = () => Promise.resolve({});
await settle();
heartbeatTimer.callback();
await settle();
assert.equal(
  globalThis.__touchHeartbeats.length,
  beatsBeforeDispose + 1,
  "a late interval callback and an in-flight heartbeat send nothing after cleanup",
);

// ---------------------------------------------------------------------------
// Touch page: key controls and flows
// ---------------------------------------------------------------------------

const pageHookRuntimeUrl = toModuleUrl(`
  const runtime = () => globalThis.__touchPageRuntime;
  const same = (left, right) =>
    !!left && !!right && left.length === right.length &&
    left.every((value, index) => Object.is(value, right[index]));
  export function useState(initial) {
    const rt = runtime();
    const index = rt.cursor++;
    if (index >= rt.hooks.length) {
      rt.hooks.push({ kind: "state", value: typeof initial === "function" ? initial() : initial });
    }
    const slot = rt.hooks[index];
    const set = (next) => {
      const value = typeof next === "function" ? next(slot.value) : next;
      if (Object.is(value, slot.value)) return;
      slot.value = value;
      rt.schedule();
    };
    return [slot.value, set];
  }
  export function useRef(initial) {
    const rt = runtime();
    const index = rt.cursor++;
    if (index >= rt.hooks.length) rt.hooks.push({ kind: "ref", ref: { current: initial } });
    return rt.hooks[index].ref;
  }
  export function useCallback(fn, deps) {
    const rt = runtime();
    const index = rt.cursor++;
    if (index >= rt.hooks.length) rt.hooks.push({ kind: "callback", value: fn, deps });
    const slot = rt.hooks[index];
    if (!deps || !slot.deps || !same(deps, slot.deps)) {
      slot.value = fn;
      slot.deps = deps;
    }
    return slot.value;
  }
  export function useMemo(fn, deps) {
    const rt = runtime();
    const index = rt.cursor++;
    if (index >= rt.hooks.length) rt.hooks.push({ kind: "memo", value: fn(), deps });
    const slot = rt.hooks[index];
    if (!deps || !slot.deps || !same(deps, slot.deps)) {
      slot.value = fn();
      slot.deps = deps;
    }
    return slot.value;
  }
  export function useEffect(effect, deps) {
    const rt = runtime();
    const index = rt.cursor++;
    if (index >= rt.hooks.length) {
      rt.hooks.push({ kind: "effect", effect, deps: undefined, cleanup: undefined });
    }
    const slot = rt.hooks[index];
    slot.effect = effect;
    if (!deps || !slot.deps || !same(deps, slot.deps)) {
      slot.deps = deps;
      rt.effects.push(slot);
    }
  }
  export const useLayoutEffect = useEffect;
`, "page-hooks.js");

const pageUiStub = `export const ButtonItem = "ButtonItem";
export const Navigation = {
  Navigate: (path) => globalThis.__touchPage.navigations.push(path),
  NavigateBack: () => { globalThis.__touchPage.backNavigations += 1; },
  CloseSideMenus: () => { globalThis.__touchPage.closedMenus += 1; },
};
export const PanelSection = "PanelSection";
export const PanelSectionRow = "PanelSectionRow";
export const SliderField = "SliderField";
export const ToggleField = "ToggleField";`;

// The page talks to the shipped api module, only its transport is recorded, so
// the ordering and merging under test are the real queue's, not a stub's.
const pageCallableStubUrl = toModuleUrl(`
  export const callable = (name) => (...args) => globalThis.__touchPage.rpc(name, args);
`);
const pageTouchApiUrl = toModuleUrl(
  (await readSource("../src/touch/api.ts")).replace(
    /from ["']@decky\/api["']/,
    `from "${pageCallableStubUrl}"`,
  ),
  "page-touch-api.ts",
);
const pageApiStub = `export {
  fetchState,
  previewHaptics,
  savePreset,
  selectPreset,
  sendHeartbeat,
  setControlsVisible,
  updateOptions,
} from "${pageTouchApiUrl}";`;

const pageTouchI18nUrl = toModuleUrl(
  (await readSource("../src/touch/touch_i18n.ts")).replace(
    /from ["']\.\/i18n(\.ts)?["']/,
    `from "${touchI18nUrl}"`,
  ),
  "touch_i18n.ts",
);

const pageStubUrls = {
  "@decky/ui": asDataUrl(pageUiStub),
  react: pageHookRuntimeUrl,
  "react/jsx-runtime": asDataUrl(`export const Fragment = "Fragment";
export const jsx = (type, props) => ({ type, props: props || {} });
export const jsxs = jsx;
export const jsxDEV = jsx;`),
  "./api": asDataUrl(pageApiStub),
  "./editor-state": editorStateUrl,
  "./i18n": touchI18nUrl,
  "./touch_i18n": pageTouchI18nUrl,
  "./route": routeUrl,
  "./types": asDataUrl("export {};"),
};

const pageJs = transpile(await readSource("../src/touch/TouchContent.tsx"), "TouchContent.tsx").replace(
  /from ["']([^"']+)["']/g,
  (_match, specifier) => {
    const url = pageStubUrls[specifier];
    if (!url) throw new Error(`TouchContent.tsx imports an unstubbed module: ${specifier}`);
    return `from "${url}"`;
  },
);
const { TouchContent } = await import(asDataUrl(pageJs));

const pageState = {
  settings: {
    version: 2,
    enabled: true,
    opacity: 0.9,
    scale: 1,
    deadzone: 0.1,
    edge_swipes: true,
    haptics_enabled: true,
    haptics_strength: 25,
    active_preset_id: "builtin-a",
    custom_presets: [],
    layout: [],
  },
  status: {
    state: "running",
    detail: "capturing",
    capturing: true,
    device: "/dev/input/event3",
    rotation: 0,
    haptics_available: true,
    controls_visible: true,
  },
  presets: [
    { id: "builtin-a", name: "Balanced", layout: [], scale: 1, builtin: true },
    { id: "custom-1", name: "Mine", layout: [], scale: 1, builtin: false },
  ],
};

const clonePageState = (source) => ({
  settings: { ...source.settings },
  status: { ...source.status },
  presets: source.presets.map((preset) => ({ ...preset })),
});

let livePageState = clonePageState(pageState);
const pageCalls = {
  fetches: 0,
  updates: [],
  presets: [],
  saves: [],
  previews: 0,
  visible: [],
  heartbeats: [],
  navigations: [],
};
const pendingUpdates = [];
const pendingPresets = [];
const pendingSaves = [];
const issuedPatches = [];
let deferNextFetch = false;
let deferNextPreset = false;
const deferredFetches = [];

// The backend model: a write only changes the live snapshot when the test
// acknowledges it, so a deferred answer really is older than a later one.
const pageHandlers = {
  get_touch_state: () => {
    pageCalls.fetches += 1;
    if (deferNextFetch) {
      deferNextFetch = false;
      return new Promise((resolve) => deferredFetches.push(resolve));
    }
    return Promise.resolve(clonePageState(livePageState));
  },
  update_touch_options: (patch) => {
    pageCalls.updates.push(patch);
    issuedPatches.push(patch);
    return new Promise((resolve, reject) => pendingUpdates.push({ patch, resolve, reject }));
  },
  select_touch_preset: (id) => {
    pageCalls.presets.push(id);
    if (deferNextPreset) {
      deferNextPreset = false;
      return new Promise((resolve) => pendingPresets.push({ id, resolve }));
    }
    livePageState = {
      ...livePageState,
      settings: { ...livePageState.settings, active_preset_id: id },
    };
    return Promise.resolve(clonePageState(livePageState));
  },
  save_touch_preset: (name, savedLayout, scale, overwriteId) => {
    pageCalls.saves.push({ name, layout: savedLayout, scale, overwriteId });
    return new Promise((resolve, reject) =>
      pendingSaves.push({ name, layout: savedLayout, scale, overwriteId, resolve, reject }));
  },
  set_touch_controls_visible: (visible) => {
    pageCalls.visible.push(visible);
    livePageState = {
      ...livePageState,
      status: { ...livePageState.status, controls_visible: visible },
    };
    return Promise.resolve(clonePageState(livePageState));
  },
  touch_heartbeat: (appid) => {
    pageCalls.heartbeats.push(appid);
    return Promise.resolve({});
  },
  preview_touch_haptics: () => {
    pageCalls.previews += 1;
    return Promise.resolve({ queued: true });
  },
};

globalThis.__touchPage = {
  rpc: (name, args) => {
    const handler = pageHandlers[name];
    if (!handler) {
      throw new Error(`unexpected touch RPC from the page: ${name}`);
    }
    return handler(...args);
  },
  heartbeats: pageCalls.heartbeats,
  navigations: pageCalls.navigations,
  backNavigations: 0,
  closedMenus: 0,
};

const withSettings = (patch) => {
  livePageState = {
    ...livePageState,
    settings: { ...livePageState.settings, ...patch },
  };
  return clonePageState(livePageState);
};

const completeUpdate = async (entry, { fail = false, response } = {}) => {
  const index = pendingUpdates.indexOf(entry);
  assert.ok(index >= 0, "the expected write is in flight");
  pendingUpdates.splice(index, 1);
  if (fail) {
    entry.reject(new Error("backend rejected the write"));
  } else {
    entry.resolve(response ?? withSettings(entry.patch));
  }
  await settle();
};

// A deferred preset write applies its id when the backend acknowledges it, so
// the snapshot it carries is not older than the one a later write returns.
const completePreset = async (entry) => {
  assert.ok(entry, "the expected preset write is in flight");
  livePageState = {
    ...livePageState,
    settings: { ...livePageState.settings, active_preset_id: entry.id },
  };
  entry.resolve(clonePageState(livePageState));
  await settle();
};

// A preset save only changes the backend when it is acknowledged, so a save
// completed after its editor left still left a real trace.
const completePresetSave = async (entry, { fail = false } = {}) => {
  const index = pendingSaves.indexOf(entry);
  assert.ok(index >= 0, "the expected preset save is on the wire");
  pendingSaves.splice(index, 1);
  if (fail) {
    entry.reject(new Error("backend rejected the preset save"));
    await settle();
    return;
  }
  const id = entry.overwriteId ?? "custom-saved";
  livePageState = {
    ...livePageState,
    settings: {
      ...livePageState.settings,
      active_preset_id: id,
      layout: entry.layout.map((control) => ({ ...control })),
    },
    presets: [
      ...livePageState.presets.filter((preset) => preset.id !== id),
      {
        id,
        name: entry.name,
        layout: entry.layout.map((control) => ({ ...control })),
        scale: entry.scale,
        builtin: false,
      },
    ],
  };
  entry.resolve(clonePageState(livePageState));
  await settle();
};

const resetPageFixtures = () => {
  assert.equal(pendingUpdates.length, 0, "the previous case left no write in flight");
  assert.equal(pendingPresets.length, 0, "the previous case left no preset write in flight");
  assert.equal(pendingSaves.length, 0, "the previous case left no preset save in flight");
  livePageState = clonePageState(pageState);
  pageCalls.updates.length = 0;
  pageCalls.presets.length = 0;
  pageCalls.saves.length = 0;
  pageCalls.visible.length = 0;
  pageCalls.fetches = 0;
  pageCalls.previews = 0;
  pageCalls.heartbeats.length = 0;
  pageCalls.navigations.length = 0;
  globalThis.__touchPage.backNavigations = 0;
  issuedPatches.length = 0;
  deferredFetches.length = 0;
  deferNextFetch = false;
  deferNextPreset = false;
};

const mountPage = (locale = "en", component = TouchContent) => {
  const runtime = {
    hooks: [],
    cursor: 0,
    effects: [],
    scheduled: false,
    renders: 0,
    unmounted: false,
    tree: null,
    component,
    props: component === TouchContent ? { locale } : {},
  };
  runtime.schedule = () => {
    if (runtime.unmounted) return;
    if (runtime.scheduled) return;
    runtime.scheduled = true;
    Promise.resolve().then(() => {
      runtime.scheduled = false;
      if (!runtime.unmounted) runtime.render();
    });
  };
  runtime.render = () => {
    runtime.cursor = 0;
    runtime.renders += 1;
    runtime.effects = [];
    runtime.tree = runtime.component(runtime.props);
    const queued = runtime.effects;
    runtime.effects = [];
    for (const slot of queued) {
      if (typeof slot.cleanup === "function") slot.cleanup();
      slot.cleanup = undefined;
      const cleanup = slot.effect();
      slot.cleanup = typeof cleanup === "function" ? cleanup : undefined;
    }
  };
  runtime.unmount = () => {
    for (const slot of runtime.hooks) {
      if (slot.kind === "effect" && typeof slot.cleanup === "function") {
        slot.cleanup();
        slot.cleanup = undefined;
      }
    }
    runtime.unmounted = true;
  };
  globalThis.__touchPageRuntime = runtime;
  runtime.render();
  return runtime;
};

const stringsIn = (node, found = []) => {
  if (typeof node === "string" || typeof node === "number") {
    found.push(String(node));
    return found;
  }
  if (Array.isArray(node)) {
    node.forEach((child) => stringsIn(child, found));
    return found;
  }
  if (!node || typeof node !== "object") return found;
  stringsIn((node.props || {}).children, found);
  return found;
};

const propStrings = (tree) => {
  const found = [];
  walk(tree, (node) => {
    const props = node.props || {};
    for (const key of ["label", "description", "title", "aria-label", "children"]) {
      stringsIn(props[key], found);
    }
  });
  return found;
};

const pageLabels = (tree) =>
  findAll(tree, (node) => node.type === "ToggleField" || node.type === "SliderField")
    .map((node) => node.props.label);
const pageButton = (tree, label) =>
  findAll(tree, (node) => node.type === "ButtonItem" && stringsIn(node.props.children).includes(label))[0];
const pageToggle = (tree, label) =>
  findAll(tree, (node) => node.type === "ToggleField" && node.props.label === label)[0];
const pageSlider = (tree, label) =>
  findAll(tree, (node) => node.type === "SliderField" && node.props.label === label)[0];
const pagePanelTitles = (tree) =>
  findAll(tree, (node) => node.type === "PanelSection").map((node) => node.props.title);

const mountPollingPage = async (locale = "en") => {
  const before = new Set(timers.intervals.keys());
  const runtime = mountPage(locale);
  const added = [...timers.intervals.keys()].filter((id) => !before.has(id));
  assert.equal(added.length, 1, "a mounted page owns exactly one poll timer");
  await settle();
  return { runtime, poll: timers.intervals.get(added[0]).callback };
};

const page = mountPage("en");
await settle();
assert.equal(pageCalls.fetches, 1, "mounting the page reads the state once");

// One page, one column: the touch tree holds no nested tab strip of its own,
// so the Controller's outer tabs stay the only navigation on screen.
assert.equal(
  findAll(page.tree, (node) => node.type === "Tabs").length,
  0,
  "the touch page renders no nested tabs",
);

// Common, display/input, layout and status render consecutively in the same
// scrolled column, in that order.
assert.deepEqual(
  pagePanelTitles(page.tree),
  [
    touch("en", "common.title"),
    touch("en", "common.display"),
    touch("en", "layout.title"),
    touch("en", "status.title"),
  ],
  "the four sections share one column, in reading order",
);

// Every everyday control is present, and there is no second edge-swipe toggle:
// the Controller's own System and in-game swipe switches stay canonical.
const labels = pageLabels(page.tree);
for (const key of [
  "common.enable",
  "common.show",
  "common.haptics",
  "common.strength",
  "common.opacity",
  "common.scale",
  "common.deadzone",
]) {
  assert.ok(labels.includes(touch("en", key)), `the page renders ${key}`);
}
const pageText = propStrings(page.tree);
assert.ok(pageText.includes(touch("en", "common.captureHint")));
assert.ok(pageButton(page.tree, touch("en", "common.preview")), "the haptics preview button is present");
assert.ok(
  !pageText.some((value) => /edge swipe|swipe gestures|手势|侧滑/i.test(value)),
  "the page offers no duplicate edge-swipe preference",
);

// The layout and status controls are on that same page: the editor entry, the
// preset list and the live runtime report all render without a tab change.
assert.ok(pageButton(page.tree, touch("en", "layout.openEditor")), "the editor entry sits on the same page");
assert.ok(pageButton(page.tree, "Mine"), "the custom preset is listed on the same page");
assert.ok(
  pageText.includes(touch("en", "layout.currentPreset", { name: "Balanced (built-in)" })),
  "the current preset reports the built-in suffix",
);
assert.ok(pageText.includes(touch("en", "status.state", { value: "capturing" })));
assert.ok(pageText.includes(touch("en", "status.capture", { value: touch("en", "status.yes") })));
assert.ok(pageText.includes(touch("en", "status.haptics", { value: touch("en", "status.available") })));

// Haptics preview reaches the backend.
pageButton(page.tree, touch("en", "common.preview")).props.onClick();
await settle();
assert.equal(pageCalls.previews, 1);

// A toggle writes one patch and stays optimistic while the write is in flight.
pageToggle(page.tree, touch("en", "common.enable")).props.onChange(false);
await settle();
assert.deepEqual(pageCalls.updates, [{ enabled: false }]);
assert.equal(pageToggle(page.tree, touch("en", "common.enable")).props.checked, false);
await completeUpdate(pendingUpdates[0]);
assert.equal(pageToggle(page.tree, touch("en", "common.enable")).props.checked, false);

// Turning it back on is another accepted write, not a local patch.
pageToggle(page.tree, touch("en", "common.enable")).props.onChange(true);
await settle();
await completeUpdate(pendingUpdates[0]);
assert.equal(livePageState.settings.enabled, true);

// Choosing a preset writes only the preset id and keeps every section mounted.
const updatesBeforePreset = pageCalls.updates.length;
pageButton(page.tree, "Mine").props.onClick();
await settle();
assert.deepEqual(pageCalls.presets, ["custom-1"]);
assert.equal(propStrings(page.tree).includes(touch("en", "status.state", { value: "capturing" })), true);
assert.equal(findAll(page.tree, (node) => node.type === "PanelSection").length, 4);
assert.equal(pageCalls.updates.length, updatesBeforePreset, "preset choice does not re-send option patches");

// Opening the editor releases capture before the route change.
pageButton(page.tree, touch("en", "layout.openEditor")).props.onClick();
await settle();
assert.deepEqual(pageCalls.navigations, [routePath]);
assert.equal(globalThis.__touchPage.closedMenus, 1);
assert.equal(editorState.isLayoutEditing(), true);
assert.deepEqual(pageCalls.heartbeats, [null]);
// The page itself stays mounted under the editor route; nothing was unmounted.
assert.deepEqual(
  pagePanelTitles(page.tree),
  [
    touch("en", "common.title"),
    touch("en", "common.display"),
    touch("en", "layout.title"),
    touch("en", "status.title"),
  ],
);

// zh-CN renders the same single page in Chinese.
const zhPage = mountPage("zh");
await settle();
assert.equal(findAll(zhPage.tree, (node) => node.type === "Tabs").length, 0);
assert.ok(pageLabels(zhPage.tree).includes(touch("zh", "common.enable")));
assert.ok(pageLabels(zhPage.tree).includes(touch("zh", "common.deadzone")));
assert.deepEqual(
  pagePanelTitles(zhPage.tree),
  [
    touch("zh", "common.title"),
    touch("zh", "common.display"),
    touch("zh", "layout.title"),
    touch("zh", "status.title"),
  ],
  "the zh page keeps the migrated copy in one column",
);
assert.ok(pageButton(zhPage.tree, touch("zh", "layout.openEditor")));
assert.notEqual(touch("zh", "common.enable"), touch("en", "common.enable"));

// Unmounting the page clears its own poll; the global heartbeat is untouched.
const intervalsBeforeUnmount = timers.intervals.size;
page.unmount();
zhPage.unmount();
assert.ok(timers.intervals.size < intervalsBeforeUnmount, "unmounting clears the page poll");

editorState.endLayoutEditing();

// ---------------------------------------------------------------------------
// Touch page: accepted work outlives the page and keeps its order
// ---------------------------------------------------------------------------

// --- Regression: a queued disable must not die with the page ----------------

// The opacity save is still on the wire when the enable toggle is turned off
// and the page unmounts. That disable was already accepted, so it must still
// reach the backend instead of being dropped with the local pending state.
resetPageFixtures();
const droppedPage = await mountPollingPage();
const droppedSlider = () => pageSlider(droppedPage.runtime.tree, touch("en", "common.opacity"));
const droppedEnable = () => pageToggle(droppedPage.runtime.tree, touch("en", "common.enable"));
droppedSlider().props.onChange(0.4);
await settle();
assert.deepEqual(issuedPatches, [{ opacity: 0.4 }], "the slider save owns the backend");
droppedEnable().props.onChange(false);
await settle();
assert.equal(droppedEnable().props.checked, false, "the disable is shown immediately");
assert.deepEqual(issuedPatches, [{ opacity: 0.4 }], "the disable waits behind the accepted save");
droppedPage.runtime.unmount();
const droppedRenders = droppedPage.runtime.renders;
await completeUpdate(pendingUpdates[0]);
assert.deepEqual(
  issuedPatches,
  [{ opacity: 0.4 }, { enabled: false }],
  "unmounting the page does not cancel the accepted disable",
);
await completeUpdate(pendingUpdates[0]);
assert.equal(livePageState.settings.enabled, false, "the backend ends up disabled");
assert.equal(livePageState.settings.opacity, 0.4);
assert.equal(droppedPage.runtime.renders, droppedRenders, "the detached page never re-renders");

// The next mount reads the finished writes, so it opens on the disable.
const afterDropped = await mountPollingPage();
assert.equal(
  pageToggle(afterDropped.runtime.tree, touch("en", "common.enable")).props.checked,
  false,
  "the remount keeps the disable the old page accepted",
);
assert.equal(
  pageSlider(afterDropped.runtime.tree, touch("en", "common.opacity")).props.value,
  0.4,
);
afterDropped.runtime.unmount();

// A poll that goes out before a write and lands after it must not roll the
// display or the confirmed snapshot back to the older value it carried.
resetPageFixtures();
const polledPage = await mountPollingPage();
const polledOpacity = () =>
  pageSlider(polledPage.runtime.tree, touch("en", "common.opacity"));
assert.equal(pageCalls.fetches, 1);
assert.equal(polledOpacity().props.value, 0.9);

deferNextFetch = true;
polledPage.poll();
await settle();
assert.equal(pageCalls.fetches, 2, "the interval starts a second read");
assert.equal(deferredFetches.length, 1, "the poll is still outstanding");

polledOpacity().props.onChange(0.4);
await settle();
assert.deepEqual(pageCalls.updates, [{ opacity: 0.4 }], "the slider move is written once");
assert.equal(polledOpacity().props.value, 0.4, "the slider stays optimistic while that write is in flight");
await completeUpdate(pendingUpdates[0]);
assert.equal(polledOpacity().props.value, 0.4, "the completed write stays on screen");

const staleSnapshot = clonePageState(livePageState);
staleSnapshot.settings.opacity = 0.9;
staleSnapshot.status = { ...staleSnapshot.status, state: "idle", detail: "stale" };
deferredFetches.shift()(staleSnapshot);
await settle();
assert.equal(polledOpacity().props.value, 0.4, "the stale poll never rolls the display back");
assert.equal(
  propStrings(polledPage.runtime.tree).includes(touch("en", "status.state", { value: "stale" })),
  false,
  "the stale poll never publishes the older status it carried",
);

// The confirmed snapshot survived the stale poll: a rejected write falls back
// to the value the completed write adopted, not to the stale one.
polledOpacity().props.onChange(0.7);
await settle();
assert.equal(polledOpacity().props.value, 0.7);
await completeUpdate(pendingUpdates[0], { fail: true });
assert.equal(
  polledOpacity().props.value,
  0.4,
  "a failed write restores the confirmed value the stale poll left alone",
);
assert.ok(
  propStrings(polledPage.runtime.tree).some((value) => value.includes("backend rejected the write")),
  "the failed write is reported on the page",
);
polledPage.runtime.unmount();

// A tab switch keeps the accepted writes as well: the fresh page waits for
// them, reads the value they produced, and the detached tree stays frozen.
resetPageFixtures();
const oldPage = await mountPollingPage();
const oldOpacity = () => pageSlider(oldPage.runtime.tree, touch("en", "common.opacity"));
oldOpacity().props.onChange(0.3);
await settle();
oldOpacity().props.onChange(0.35);
await settle();
assert.deepEqual(issuedPatches, [{ opacity: 0.3 }], "one accepted write is on the wire");
assert.equal(oldOpacity().props.value, 0.35, "the queued value is shown optimistically");
assert.equal(pendingUpdates.length, 1);

const oldRenders = oldPage.runtime.renders;
oldPage.runtime.unmount();

const newPage = await mountPollingPage();
const newOpacity = () => pageSlider(newPage.runtime.tree, touch("en", "common.opacity"));
assert.equal(newOpacity(), undefined, "the fresh page shows nothing until the accepted writes finish");
await completeUpdate(pendingUpdates[0]);
assert.equal(pageCalls.fetches, 1, "the read still waits for the queued write");
await completeUpdate(pendingUpdates[0]);
assert.equal(pageCalls.fetches, 2, "the read runs once every accepted write finished");
assert.equal(newOpacity().props.value, 0.35, "the fresh page reads what the old page saved");

newOpacity().props.onChange(0.6);
await settle();
assert.deepEqual(
  issuedPatches,
  [{ opacity: 0.3 }, { opacity: 0.35 }, { opacity: 0.6 }],
  "accepted operations keep their order across the remount",
);
await completeUpdate(pendingUpdates[0]);
assert.equal(newOpacity().props.value, 0.6);
assert.equal(livePageState.settings.opacity, 0.6, "the newest accepted write is the last word");
assert.equal(oldOpacity().props.value, 0.35, "the detached page keeps the tree it last rendered");
assert.equal(oldPage.runtime.renders, oldRenders, "a late resolution never renders the detached page");
assert.equal(pendingUpdates.length, 0);
newPage.runtime.unmount();

// The active page merges slider readings, recovers from a rejected write, and
// keeps a preset from being overtaken by a patch made after it.
resetPageFixtures();
const workingPage = await mountPollingPage();
const workingSlider = (key) => pageSlider(workingPage.runtime.tree, touch("en", key));
assert.equal(workingSlider("common.scale").props.value, 1);

workingSlider("common.opacity").props.onChange(0.5);
await settle();
workingSlider("common.opacity").props.onChange(0.55);
await settle();
workingSlider("common.opacity").props.onChange(0.6);
await settle();
assert.deepEqual(issuedPatches, [{ opacity: 0.5 }], "one write owns the backend while the slider moves");
assert.equal(workingSlider("common.opacity").props.value, 0.6, "the newest reading stays on screen");

await completeUpdate(pendingUpdates[0]);
assert.deepEqual(
  issuedPatches,
  [{ opacity: 0.5 }, { opacity: 0.6 }],
  "the pending readings are merged into one follow-up write",
);
assert.equal(
  workingSlider("common.opacity").props.value,
  0.6,
  "the older response never overrides the newer reading",
);
await completeUpdate(pendingUpdates[0]);
assert.equal(workingSlider("common.opacity").props.value, 0.6);
assert.equal(livePageState.settings.opacity, 0.6);

workingSlider("common.scale").props.onChange(1.2);
await settle();
assert.equal(workingSlider("common.scale").props.value, 1.2, "the scale moves optimistically");
await completeUpdate(pendingUpdates[0], { fail: true });
assert.equal(
  workingSlider("common.scale").props.value,
  1,
  "a rejected write restores the confirmed value",
);
assert.ok(
  propStrings(workingPage.runtime.tree).some((value) => value.includes("backend rejected the write")),
  "the rejected write is reported",
);

workingSlider("common.deadzone").props.onChange(0.2);
await settle();
assert.deepEqual(issuedPatches.at(-1), { deadzone: 0.2 }, "a rejected write does not block the next one");
await completeUpdate(pendingUpdates[0]);
assert.equal(workingSlider("common.deadzone").props.value, 0.2);

// A preset takes its own queue slot between the patches accepted before and
// after it, so the later patch can never be merged backwards over the preset.
const updatesBeforeBarrier = pageCalls.updates.length;
workingSlider("common.opacity").props.onChange(0.65);
await settle();
workingSlider("common.opacity").props.onChange(0.7);
await settle();
workingSlider("common.opacity").props.onChange(0.72);
await settle();
pageButton(workingPage.runtime.tree, "Mine").props.onClick();
await settle();
workingSlider("common.opacity").props.onChange(0.75);
await settle();
assert.deepEqual(issuedPatches.at(-1), { opacity: 0.65 });
assert.deepEqual(pageCalls.presets, [], "the preset waits behind the accepted patches");

await completeUpdate(pendingUpdates[0]);
assert.deepEqual(issuedPatches.at(-1), { opacity: 0.72 }, "the patches accepted before it are merged");
assert.deepEqual(pageCalls.presets, [], "the preset still waits for that merged write");
await completeUpdate(pendingUpdates[0]);
assert.deepEqual(pageCalls.presets, ["custom-1"], "the preset runs in its own slot");
assert.deepEqual(issuedPatches.at(-1), { opacity: 0.75 }, "the later patch takes a new slot after it");
await completeUpdate(pendingUpdates[0]);
assert.equal(pageCalls.updates.length, updatesBeforeBarrier + 3, "four readings became three writes");
assert.equal(workingSlider("common.opacity").props.value, 0.75);
assert.ok(
  propStrings(workingPage.runtime.tree).includes(
    touch("en", "layout.currentPreset", {
      name: `Mine${touch("en", "common.customSuffix")}`,
    }),
  ),
  "the chosen preset is reported on the page",
);
assert.equal(findAll(workingPage.runtime.tree, (node) => node.type === "PanelSection").length, 4);

const showToggle = () => pageToggle(workingPage.runtime.tree, touch("en", "common.show"));
assert.equal(showToggle().props.checked, true);
showToggle().props.onChange(false);
await settle();
assert.deepEqual(pageCalls.visible, [false]);
assert.equal(showToggle().props.checked, false, "the visibility toggle reflects the adopted status");

workingPage.runtime.unmount();

// A preset clicked while an earlier one is still on the wire is the user's
// newest choice, even though the page still shows the id that choice will
// replace: it must be queued, not skipped as a repeat of the visible id.
resetPageFixtures();
const queuedPage = await mountPollingPage();
const presetText = (name) => stringsIn(pageButton(queuedPage.runtime.tree, name).props.children);
const presetSelected = (name) => presetText(name).includes(touch("en", "common.selected"));
assert.equal(presetSelected("Balanced"), true, "the fixture opens on the built-in preset");

deferNextPreset = true;
pageButton(queuedPage.runtime.tree, "Mine").props.onClick();
await settle();
assert.deepEqual(pageCalls.presets, ["custom-1"], "the custom preset owns the backend");
assert.equal(pendingPresets.length, 1, "the custom preset is still on the wire");
assert.ok(
  propStrings(queuedPage.runtime.tree).includes(touch("en", "status.syncing")),
  "the page reports the queued write",
);

pageButton(queuedPage.runtime.tree, "Balanced").props.onClick();
await settle();
assert.deepEqual(pageCalls.presets, ["custom-1"], "the newest choice waits behind the queued one");
assert.equal(pendingPresets.length, 1, "only the queued write is on the wire");

await completePreset(pendingPresets.shift());
assert.deepEqual(pageCalls.presets, ["custom-1", "builtin-a"], "the newest choice runs once it is its turn");
assert.equal(livePageState.settings.active_preset_id, "builtin-a", "the backend ends on the newest choice");
assert.equal(presetSelected("Balanced"), true, "the page marks the newest choice selected");
assert.equal(presetSelected("Mine"), false, "the replaced preset is no longer selected");
assert.equal(
  propStrings(queuedPage.runtime.tree).includes(touch("en", "status.syncing")),
  false,
  "the page is idle once both writes finished",
);

// Idle, clicking the id already in effect stays a no-op.
pageButton(queuedPage.runtime.tree, "Balanced").props.onClick();
await settle();
assert.deepEqual(pageCalls.presets, ["custom-1", "builtin-a"], "an idle repeat sends no redundant write");
queuedPage.runtime.unmount();
assert.equal(timers.intervals.size, 0, "no page keeps polling once every page is unmounted");

// ---------------------------------------------------------------------------
// Layout editor: a save that outlives its editor never steers the new page
// ---------------------------------------------------------------------------

// The editor is the shipped component, mounted on the same hook runtime as the
// page: its own effects, refs and re-renders run, the real api queue carries
// its save, and the real editor-state module holds the global editing flag.
const editorUiStub = `export const Navigation = {
  Navigate: (path) => globalThis.__touchPage.navigations.push(path),
  NavigateBack: () => { globalThis.__touchPage.backNavigations += 1; },
};
export const TextField = "TextField";`;

const editorStubUrls = {
  "@decky/ui": asDataUrl(editorUiStub),
  react: pageHookRuntimeUrl,
  "react/jsx-runtime": pageStubUrls["react/jsx-runtime"],
  "../hooks/useLocale": asDataUrl(
    `export const useLocale = () => globalThis.__touchControllerLocale;`,
  ),
  "./api": asDataUrl(pageApiStub),
  "./editor-state": editorStateUrl,
  "./touch_i18n": pageTouchI18nUrl,
};

const layoutEditorJs = transpile(
  await readSource("../src/touch/LayoutEditor.tsx"),
  "LayoutEditor.tsx",
).replace(/from ["']([^"']+)["']/g, (_match, specifier) => {
  const url = editorStubUrls[specifier];
  if (!url) throw new Error(`LayoutEditor.tsx imports an unstubbed module: ${specifier}`);
  return `from "${url}"`;
});
const { default: LayoutEditor } = await import(asDataUrl(layoutEditorJs));

const editorButton = (runtime, label) =>
  findAll(
    runtime.tree,
    (node) => node.type === "button" && stringsIn(node.props.children).includes(label),
  )[0];

const mountEditor = async () => {
  globalThis.__touchControllerLocale = "en";
  const runtime = mountPage("en", LayoutEditor);
  await settle();
  assert.equal(runtime.tree.type, "div", "the editor renders its canvas");
  assert.ok(
    editorButton(runtime, touch("en", "editor.save")),
    "the editor offers a save button",
  );
  return runtime;
};

// --- Regression: a save finished after the editor left must not steer ------

resetPageFixtures();
editorState.endLayoutEditing();

const leavingEditor = await mountEditor();
assert.equal(editorState.isLayoutEditing(), true, "a mounted editor owns the editing flag");
editorButton(leavingEditor, touch("en", "editor.save")).props.onClick();
await settle();
assert.equal(pageCalls.saves.length, 1, "the save reached the backend queue");
assert.equal(pendingSaves.length, 1, "the save is still on the wire");
assert.equal(pendingSaves[0].overwriteId, null, "the built-in preset is saved as a new custom one");

// The user backs out through Steam while the save is still in flight, then
// opens the editor again; the new instance owns the flag from its own effect.
const leavingRenders = leavingEditor.renders;
leavingEditor.unmount();
assert.equal(editorState.isLayoutEditing(), false, "leaving the editor releases the flag");

const reopenedEditor = await mountEditor();
assert.equal(editorState.isLayoutEditing(), true, "the new editor owns the flag again");

await completePresetSave(pendingSaves[0]);
assert.equal(
  livePageState.settings.active_preset_id,
  "custom-saved",
  "the save the old editor started still finished on the backend",
);
assert.equal(
  globalThis.__touchPage.backNavigations,
  0,
  "the finished old save does not navigate back",
);
assert.equal(
  editorState.isLayoutEditing(),
  true,
  "the finished old save leaves the new editor's flag alone",
);
assert.equal(leavingEditor.renders, leavingRenders, "the detached editor never renders again");

// The editor still on screen keeps the shipped behaviour: one back navigation
// and the flag released, for its own save only.
editorButton(reopenedEditor, touch("en", "editor.save")).props.onClick();
await settle();
assert.equal(pendingSaves.length, 1, "the live editor's save is on the wire");
await completePresetSave(pendingSaves[0]);
assert.equal(
  globalThis.__touchPage.backNavigations,
  1,
  "the live editor returns to the page exactly once",
);
assert.equal(editorState.isLayoutEditing(), false, "the live editor releases the flag");
assert.equal(
  propStrings(reopenedEditor.tree).includes(touch("en", "editor.saving")),
  false,
  "the live editor leaves its saving state",
);
reopenedEditor.unmount();

// --- Regression: a save that fails after the editor left stays silent ------

resetPageFixtures();
const failingEditor = await mountEditor();
assert.equal(editorState.isLayoutEditing(), true);
editorButton(failingEditor, touch("en", "editor.save")).props.onClick();
await settle();
assert.equal(pendingSaves.length, 1, "the doomed save is on the wire");
const failingRenders = failingEditor.renders;
failingEditor.unmount();
assert.equal(editorState.isLayoutEditing(), false, "the failed editor released its flag on the way out");

const afterFailureEditor = await mountEditor();
assert.equal(editorState.isLayoutEditing(), true);
await completePresetSave(pendingSaves[0], { fail: true });
assert.equal(globalThis.__touchPage.backNavigations, 0, "a late failure never navigates back");
assert.equal(
  editorState.isLayoutEditing(),
  true,
  "a late failure never clears the new editor's flag",
);
assert.equal(failingEditor.renders, failingRenders, "a late failure never renders the detached editor");
assert.equal(
  propStrings(afterFailureEditor.tree).some((value) =>
    value.includes("backend rejected the preset save")),
  false,
  "a late failure is not reported on the editor that replaced it",
);
assert.equal(pendingSaves.length, 0, "the editor left no save in flight");
afterFailureEditor.unmount();
assert.equal(editorState.isLayoutEditing(), false, "the last editor out releases the flag");
assert.equal(timers.intervals.size, 0, "the editor owns no timer of its own");

console.log("Touch integration tests passed: prefixed RPCs, the shared mutation queue, global lifecycle, tab mount, the single touch page, accepted work across remounts, and an editor save that outlives its editor");
