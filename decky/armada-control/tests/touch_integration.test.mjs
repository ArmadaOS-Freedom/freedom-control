// Controller <-> Touch page integration checks. Everything runs real code: the
// touch api module is loaded against a recording @decky/api stub, the touch
// dictionaries and decision helpers are transpiled from source, the plugin
// lifecycle is executed against stubbed router/Router/window globals, and the
// exported Content and TouchContent components are rendered against a minimal
// React hook shim, so tab order, lazy mounting, the global heartbeat and the
// page controls are read from the returned element tree, not from source text.

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
`, "page-hooks.js");

const pageUiStub = `export const ButtonItem = "ButtonItem";
export const Navigation = {
  Navigate: (path) => globalThis.__touchPage.navigations.push(path),
  CloseSideMenus: () => { globalThis.__touchPage.closedMenus += 1; },
};
export const PanelSection = "PanelSection";
export const PanelSectionRow = "PanelSectionRow";
export const SliderField = "SliderField";
export const ToggleField = "ToggleField";`;

const pageApiStub = `export const fetchState = () => globalThis.__touchPage.fetchState();
export const previewHaptics = () => globalThis.__touchPage.previewHaptics();
export const selectPreset = (id) => globalThis.__touchPage.selectPreset(id);
export const sendHeartbeat = (appid) => {
  globalThis.__touchPage.heartbeats.push(appid);
  return Promise.resolve({});
};
export const setControlsVisible = (visible) => globalThis.__touchPage.setControlsVisible(visible);
export const updateOptions = (patch) => globalThis.__touchPage.updateOptions(patch);`;

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

const pageCalls = { fetches: 0, updates: [], presets: [], previews: 0, visible: [], heartbeats: [], navigations: [] };
let resolveUpdate;
globalThis.__touchPage = {
  fetchState: () => {
    pageCalls.fetches += 1;
    return Promise.resolve(pageState);
  },
  updateOptions: (patch) => {
    pageCalls.updates.push(patch);
    return new Promise((resolve) => {
      resolveUpdate = resolve;
    });
  },
  selectPreset: (id) => {
    pageCalls.presets.push(id);
    return Promise.resolve({
      ...pageState,
      settings: { ...pageState.settings, active_preset_id: id },
    });
  },
  previewHaptics: () => {
    pageCalls.previews += 1;
    return Promise.resolve({ queued: true });
  },
  setControlsVisible: (visible) => {
    pageCalls.visible.push(visible);
    return Promise.resolve({ ...pageState, status: { ...pageState.status, controls_visible: visible } });
  },
  heartbeats: pageCalls.heartbeats,
  navigations: pageCalls.navigations,
  closedMenus: 0,
};

const mountPage = (locale = "en") => {
  const runtime = {
    hooks: [],
    cursor: 0,
    effects: [],
    scheduled: false,
    renders: 0,
    unmounted: false,
    tree: null,
    component: TouchContent,
    props: { locale },
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
const pagePanelTitles = (tree) =>
  findAll(tree, (node) => node.type === "PanelSection").map((node) => node.props.title);

const page = mountPage("en");
assert.equal(pageCalls.fetches, 1, "mounting the page reads the state once");
await settle();

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
resolveUpdate({ ...pageState, settings: { ...pageState.settings, enabled: false } });
await settle();
assert.equal(pageToggle(page.tree, touch("en", "common.enable")).props.checked, false);

// Choosing a preset writes only the preset id and keeps every section mounted.
pageButton(page.tree, "Mine").props.onClick();
await settle();
assert.deepEqual(pageCalls.presets, ["custom-1"]);
assert.equal(propStrings(page.tree).includes(touch("en", "status.state", { value: "capturing" })), true);
assert.equal(findAll(page.tree, (node) => node.type === "PanelSection").length, 4);
assert.equal(pageCalls.updates.length, 1, "preset choice does not re-send option patches");

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

console.log("Touch integration tests passed: prefixed RPCs, global lifecycle, tab mount and the single touch page");
