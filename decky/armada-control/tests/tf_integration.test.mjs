// Controller <-> TF page integration checks. Both halves run real code: the TF
// backend module is loaded against a recording @decky/api stub (so the RPC
// names, argument order, envelope pass-through and rejected-call propagation
// are exercised), and the exported Content component is transpiled and rendered
// against a minimal React hook/jsx shim (so the tab list, the lazy TF mount and
// the locale mapping are read from the returned element tree, not from the
// source text).

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

// The real TF dictionary, used only as data: the tab label and the page locale
// expectations come from the same tables the page ships with.
const i18nUrl = toModuleUrl(await readSource("../src/tf/i18n.ts"), "i18n.ts");
const tfI18n = await import(i18nUrl);

// ---------------------------------------------------------------------------
// TF backend: executed RPC calls
// ---------------------------------------------------------------------------

// A recording @decky/api stand-in. Every call() is captured verbatim and its
// result is decided by __tfOutcome, so one stub covers a success envelope, an
// expected failure envelope and a rejected transport.
globalThis.__tfCalls = [];
const apiStubUrl = toModuleUrl(`
  export const call = (...args) => {
    globalThis.__tfCalls.push(args);
    return Promise.resolve(globalThis.__tfOutcome(...args));
  };
`);

const backendUrl = toModuleUrl(
  (await readSource("../src/tf/tf_backend.ts")).replace(
    /from ["']@decky\/api["']/,
    `from "${apiStubUrl}"`,
  ),
  "tf_backend.ts",
);
const backend = await import(backendUrl);

const okInspection = { ok: true, data: { cards: [{ device: "/dev/mmcblk0", state: "pending" }] } };
const failedPlan = { ok: false, error: "card_busy", detail: "the card is busy" };
const okStatus = {
  ok: true,
  data: {
    state: "running",
    phase: "preparing",
    detail: "",
    log_tail: "",
    // The service reports the full TfStatus on a successful start, so the stub
    // mirrors that contract instead of a partial running record.
    library_requested: false,
    library_registered: false,
    library_pending: false,
  },
};

globalThis.__tfOutcome = (name) => (name === "prepare_tf_library" ? failedPlan : okInspection);
// A success envelope and an expected failure envelope both reach the caller
// unchanged; the wrapper neither unwraps data nor turns a failure into a throw.
assert.deepEqual(await backend.getTfInspection(), okInspection);
assert.deepEqual(await backend.prepareTfLibrary("/dev/mmcblk0"), failedPlan);

globalThis.__tfOutcome = () => okStatus;
assert.deepEqual(await backend.startTfLibrary("token-1", true), okStatus);
assert.deepEqual(await backend.startTfLibrary("token-2", false), okStatus);
assert.deepEqual(await backend.getTfStatus(), okStatus);

// RPC names and argument order: the card path comes from a prepared plan and
// the confirmation flag is forwarded verbatim, never coerced.
assert.deepEqual(globalThis.__tfCalls, [
  ["get_tf_inspection"],
  ["prepare_tf_library", "/dev/mmcblk0"],
  ["start_tf_library", "token-1", true],
  ["start_tf_library", "token-2", false],
  ["get_tf_status"],
]);

// A rejected transport call propagates to the page as the same rejection.
const transportClosed = new Error("RPC transport closed");
globalThis.__tfOutcome = () => Promise.reject(transportClosed);
await assert.rejects(backend.getTfStatus(), (error) => error === transportClosed);
await assert.rejects(backend.startTfLibrary("token-3", true), (error) => error === transportClosed);

// ---------------------------------------------------------------------------
// Content tab: executed render
// ---------------------------------------------------------------------------

// Small stateful hooks shim: values survive between renders, setters write back
// into the slot the caller indexes, refs are stable objects and no effect is
// ever scheduled. Only the slots Content reads through useState are seeded.
const reactStubUrl = toModuleUrl(`
  const store = () => globalThis.__tfHooks;
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
  "./hooks/useLocale": `export const useLocale = () => globalThis.__tfLocale;`,
  "./i18n": `export const t = (key) => key;`,
  "./icons": `export const tabIcons = { Compatibility: {}, Power: {}, Fans: {}, RGB: {}, TF: {}, Advanced: {} };`,
  "./lib/games": `export const currentGame = () => null;`,
  "./styles": `export const styles = "";`,
  "./tabs/Compatibility": `export const Compatibility = "Compatibility";`,
  "./tabs/Fans": `export const Fans = "Fans";`,
  "./tabs/Power": `export const Power = "Power";`,
  "./tabs/Settings": `export const Settings = "Settings";`,
  "./tf/TfLibraryContent": `export const TfLibraryContent = "TfLibraryContent";`,
  // The page calls the real localiser so the rendered label is the real string.
  "./tf/tf_i18n": transpile(`import { translate } from "${i18nUrl}";
export const tfText = (locale, key, variables) => translate(locale, key, variables);
export const tfErrorText = (locale, code) => translate(locale, "error.unknown");`),
};

const stubUrls = {
  react: reactStubUrl,
  ...Object.fromEntries(
    Object.entries(contentStubs).map(([specifier, source]) => [specifier, asDataUrl(source)]),
  ),
};

const contentJs = transpile(await readSource("../src/Content.tsx"), "Content.tsx").replace(
  /from ["']([^"']+)["']/g,
  (_match, specifier) => {
    const url = stubUrls[specifier];
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

const tfPages = (tree) => findAll(tree, (node) => node.type === "TfLibraryContent");

const tfTabOf = (tabsElement) => tabsElement.props.tabs.find((tab) => tab.id === "TF");

const tabIds = (tabsElement) => tabsElement.props.tabs.map((tab) => tab.id);

const baseConfig = { fanSupported: true, rgbSupported: true, installedGames: [], power: {}, tweaks: {} };

const mountContent = (locale, config = baseConfig) => {
  globalThis.__tfLocale = locale;
  globalThis.__tfHooks = {
    states: ["Compatibility", config, "Loading"],
    stateCursor: 0,
    refCursor: 0,
    refs: [],
  };
  return renderContent();
};

const renderContent = () => {
  const hooks = globalThis.__tfHooks;
  hooks.stateCursor = 0;
  hooks.refCursor = 0;
  return Content();
};

// The optional pages follow the device flags, and TF stays before Advanced.
const full = tabsOf(mountContent("en"));
assert.deepEqual(tabIds(full), ["Compatibility", "Power", "Fans", "RGB", "TF", "Advanced"]);
const bare = tabsOf(mountContent("en", { ...baseConfig, fanSupported: false, rgbSupported: false }));
assert.deepEqual(tabIds(bare), ["Compatibility", "Power", "TF", "Advanced"]);
const rgbOnly = tabsOf(mountContent("en", { ...baseConfig, fanSupported: false, rgbSupported: true }));
assert.deepEqual(tabIds(rgbOnly), ["Compatibility", "Power", "RGB", "TF", "Advanced"]);

// A fresh default mount: Compatibility is active and the TF page is absent, so
// no card probe or status poll can run before the tab is opened.
let tabsElement = tabsOf(mountContent("en"));
assert.ok(tabsElement, "Content renders a Tabs element once config is loaded");
assert.equal(tabsElement.props.activeTab, "Compatibility");
assert.equal(tfTabOf(tabsElement).content.props.children, null);
assert.equal(tfPages(tabsElement).length, 0);
assert.equal(tfTabOf(tabsElement).title.props["aria-label"], tfI18n.translate("en", "tf.title"));
assert.equal(tfTabOf(tabsElement).title.props.title, tfTabOf(tabsElement).title.props["aria-label"]);

// Activating the tab builds exactly one page with the mapped locale.
tabsElement.props.onShowTab("TF");
tabsElement = tabsOf(renderContent());
assert.equal(tabsElement.props.activeTab, "TF");
let pages = tfPages(tabsElement);
assert.equal(pages.length, 1);
assert.equal(tfTabOf(tabsElement).content.props.children, pages[0]);
assert.equal(pages[0].props.locale, "en");

// Switching away unmounts it again.
tabsElement.props.onShowTab("Power");
tabsElement = tabsOf(renderContent());
assert.equal(tabsElement.props.activeTab, "Power");
assert.equal(tfTabOf(tabsElement).content.props.children, null);
assert.equal(tfPages(tabsElement).length, 0);

// Controller locale -> TF locale: only zh-CN is translated, every other
// locale reads the page and its tab label in English.
for (const [controllerLocale, expected] of [
  ["zh-CN", "zh"],
  ["en", "en"],
  ["pt-BR", "en"],
  ["pt-PT", "en"],
]) {
  let current = tabsOf(mountContent(controllerLocale));
  current.props.onShowTab("TF");
  current = tabsOf(renderContent());
  pages = tfPages(current);
  assert.equal(pages.length, 1, `${controllerLocale} mounts one TF page`);
  assert.equal(pages[0].props.locale, expected, `${controllerLocale} renders the TF page as ${expected}`);
  assert.equal(
    tfTabOf(current).title.props["aria-label"],
    tfI18n.translate(expected, "tf.title"),
    `${controllerLocale} labels the TF tab in ${expected}`,
  );
}
assert.notEqual(tfI18n.translate("zh", "tf.title"), tfI18n.translate("en", "tf.title"));

// ---------------------------------------------------------------------------
// TF page: overlapping card scans (request generations)
// ---------------------------------------------------------------------------

// The real TfLibraryContent is transpiled and driven by a page-specific hook
// scheduler: state, refs, memoised callbacks and effects with cleanup in hook
// order, effects flushed right after each render, and every state write
// scheduling the next render as a microtask. Nothing else of React is emulated.
const pageHookRuntimeUrl = toModuleUrl(`
  const runtime = () => globalThis.__tfPageRuntime;
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

const pageJsxStub = `export const Fragment = "Fragment";
export const jsx = (type, props) => ({ type, props: props || {} });
export const jsxs = jsx;
export const jsxDEV = jsx;`;

const pageUiStub = `export const ButtonItem = "ButtonItem";
export const ConfirmModal = "ConfirmModal";
export const DropdownItem = "DropdownItem";
export const Field = "Field";
export const PanelSection = "PanelSection";
export const PanelSectionRow = "PanelSectionRow";
export const ProgressBarItem = "ProgressBarItem";
export const ToggleField = "ToggleField";
export const showModal = () => {};`;

// The page's backend calls become deferreds the test settles by hand, so two
// scans can overlap in exactly the order the assertion needs.
const pageBackendStub = `export const getTfInspection = () => globalThis.__tfPage.nextInspection();
export const getTfStatus = () => globalThis.__tfPage.nextStatus();
export const prepareTfLibrary = (device) => globalThis.__tfPage.prepare(device);
export const startTfLibrary = (token, confirmed) => globalThis.__tfPage.start(token, confirmed);`;

// The real localiser and the real decision helpers, as the page imports them.
const pageTfI18nUrl = asDataUrl(
  transpile(await readSource("../src/tf/tf_i18n.ts"), "tf_i18n.ts").replace(
    /from ["']\.\/i18n(\.ts)?["']/,
    `from "${i18nUrl}"`,
  ),
);
const pageTfModelUrl = toModuleUrl(await readSource("../src/tf/tf_model.ts"), "tf_model.ts");

const pageStubUrls = {
  "@decky/ui": asDataUrl(pageUiStub),
  react: pageHookRuntimeUrl,
  "react/jsx-runtime": asDataUrl(pageJsxStub),
  "./tf_backend": asDataUrl(pageBackendStub),
  "./tf_i18n": pageTfI18nUrl,
  "./tf_model": pageTfModelUrl,
  "./i18n": i18nUrl,
  "./tf_types": asDataUrl("export {};"),
};

const pageJs = transpile(await readSource("../src/tf/TfLibraryContent.tsx"), "TfLibraryContent.tsx").replace(
  /from ["']([^"']+)["']/g,
  (_match, specifier) => {
    const url = pageStubUrls[specifier];
    if (!url) throw new Error(`TfLibraryContent.tsx imports an unstubbed module: ${specifier}`);
    return `from "${url}"`;
  },
);
const { TfLibraryContent } = await import(asDataUrl(pageJs));

// Timers are stubbed so the component's status poll can never run a real
// interval during the test; the pending entries are counted and cleared.
const pageTimers = { next: 1, pending: new Map() };
globalThis.window = {
  setTimeout: (callback) => {
    const id = pageTimers.next;
    pageTimers.next += 1;
    pageTimers.pending.set(id, callback);
    return id;
  },
  clearTimeout: (id) => {
    pageTimers.pending.delete(id);
  },
};

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};

const settle = async () => {
  for (let turn = 0; turn < 3; turn += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
};

const pageCard = (device) => ({
  device,
  size_bytes: 512 * 1024 * 1024,
  card_cid: "1b2c3d4e5f60718293a4b5c6d7e8f901",
  state: "pending",
  reason: "",
  detail: "",
  reserve_device: `${device}p1`,
  android_bytes: 64 * 1024 * 1024,
  linux_bytes: 128 * 1024 * 1024,
  mountpoint: null,
  library_path: null,
});
const cardDeviceA = "/dev/mmcblk0";
const cardDeviceB = "/dev/mmcblk1";
const inspectionOf = (devices) => ({ ok: true, data: { cards: devices.map(pageCard) } });
// An idle status: the page may review, and the poll resolves without touching
// the card list.
const pageIdleStatus = {
  ok: true,
  data: {
    state: "idle",
    phase: "",
    detail: "",
    log_tail: "",
    library_requested: false,
    library_registered: false,
    library_pending: false,
  },
};

const pageCalls = { inspections: [] };
globalThis.__tfPage = {
  nextInspection: () => {
    const pending = deferred();
    pageCalls.inspections.push(pending);
    return pending.promise;
  },
  nextStatus: () => Promise.resolve(pageIdleStatus),
  // Nothing in this scenario may start a conversion; a failure envelope would
  // surface if it ever did.
  prepare: () => Promise.resolve({ ok: false, error: "internal_error", detail: "" }),
  start: () => Promise.resolve({ ok: false, error: "internal_error", detail: "" }),
};

const mountTfPage = () => {
  const runtime = {
    hooks: [],
    cursor: 0,
    effects: [],
    scheduled: false,
    renders: 0,
    unmounted: false,
    afterUnmountWrites: 0,
    tree: null,
    component: TfLibraryContent,
    props: { locale: "en" },
  };
  runtime.schedule = () => {
    if (runtime.unmounted) {
      // A state write after unmount is a defect: record it instead of rendering.
      runtime.afterUnmountWrites += 1;
      return;
    }
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
  globalThis.__tfPageRuntime = runtime;
  runtime.render();
  return runtime;
};

const tfLabel = (key) => tfI18n.translate("en", key);

const pageFields = (tree) => findAll(tree, (node) => node.type === "Field").map((node) => node.props);

const pageCardDevices = (tree) => {
  const dropdown = findAll(tree, (node) => node.type === "DropdownItem")[0];
  return dropdown ? dropdown.props.rgOptions.map((option) => option.data) : [];
};

const pageShowsLoading = (tree) => pageFields(tree).some((props) => props.label === tfLabel("common.loading"));

const pageShowsErrorRow = (tree) => pageFields(tree).some((props) => props.label === tfLabel("failure.errorLabel"));

const pageShowsText = (tree, key) => pageFields(tree).some((props) => props.label === tfLabel(key));

const pageRescan = (tree) => {
  const button = findAll(tree, (node) => node.type === "ButtonItem" && node.props.children === tfLabel("tf.rescan"))[0];
  assert.ok(button, "the rendered page offers a Rescan button");
  return button;
};

// Mount: one scan is issued and the loading row is rendered while it is open.
let page = mountTfPage();
assert.equal(pageCalls.inspections.length, 1, "mounting the tab scans the cards once");
assert.ok(pageShowsLoading(page.tree), "the initial scan owns the loading row");

pageCalls.inspections[0].resolve(inspectionOf([cardDeviceA]));
await settle();
assert.deepEqual(pageCardDevices(page.tree), [cardDeviceA]);
assert.ok(!pageShowsLoading(page.tree), "the settled initial scan ends its loading row");

// Two rescans are started through the rendered button before either settles.
pageRescan(page.tree).props.onClick();
await settle();
pageRescan(page.tree).props.onClick();
await settle();
assert.equal(pageCalls.inspections.length, 3, "both rescans reached the backend");
const [olderScan, newerScan] = [pageCalls.inspections[1], pageCalls.inspections[2]];

// The newer scan settles first and is rendered.
newerScan.resolve(inspectionOf([cardDeviceB]));
await settle();
assert.deepEqual(pageCardDevices(page.tree), [cardDeviceB], "the newest scan result is rendered");

// The older scan then fails: it must neither clear those cards nor show an error.
olderScan.resolve({ ok: false, error: "card_busy", detail: "stale failure" });
await settle();
assert.deepEqual(pageCardDevices(page.tree), [cardDeviceB], "an older failed scan cannot clear the newer cards");
assert.ok(!pageShowsErrorRow(page.tree), "an older failed scan cannot surface an error row");

// A newer scan may legitimately report no cards: that result is rendered.
pageRescan(page.tree).props.onClick(); // request 4, kept pending
await settle();
pageRescan(page.tree).props.onClick(); // request 5, settles empty
await settle();
assert.equal(pageCalls.inspections.length, 5);
const lateScan = pageCalls.inspections[3];
pageCalls.inspections[4].resolve(inspectionOf([]));
await settle();
assert.deepEqual(pageCardDevices(page.tree), [], "the newest empty scan is rendered");
assert.ok(pageShowsText(page.tree, "tf.none.title"), "the empty result shows the no-card state");

// A scan started from the empty state owns the loading row; an older scan that
// settles afterwards must not end it or bring its own cards back.
pageRescan(page.tree).props.onClick(); // request 6, kept pending
await settle();
assert.equal(pageCalls.inspections.length, 6);
assert.ok(pageShowsLoading(page.tree), "a rescan from the empty state owns the loading row");
const currentScan = pageCalls.inspections[5];

lateScan.resolve(inspectionOf([cardDeviceA]));
await settle();
assert.ok(pageShowsLoading(page.tree), "an older completion cannot end the newer scan loading state");
assert.deepEqual(pageCardDevices(page.tree), [], "an older completion cannot render its cards");

currentScan.resolve(inspectionOf([cardDeviceB]));
await settle();
assert.deepEqual(pageCardDevices(page.tree), [cardDeviceB]);
assert.ok(!pageShowsLoading(page.tree), "the newest scan ends its own loading row");

// A scan still open when the page unmounts writes nothing at all.
pageRescan(page.tree).props.onClick();
await settle();
assert.equal(pageCalls.inspections.length, 7);
assert.ok(pageTimers.pending.size > 0, "the status poll runs on the stubbed timer");
const rendersBeforeUnmount = page.renders;
const writesBeforeUnmount = page.afterUnmountWrites;
page.unmount();
assert.equal(pageTimers.pending.size, 0, "unmounting clears the stubbed poll timer");
pageCalls.inspections[6].resolve(inspectionOf([cardDeviceA]));
await settle();
assert.equal(page.renders, rendersBeforeUnmount, "no render follows the unmount");
assert.equal(page.afterUnmountWrites, writesBeforeUnmount, "a scan settling after unmount writes no state");

console.log("TF integration tests passed: RPC calls/envelopes and Content tab rendering");
