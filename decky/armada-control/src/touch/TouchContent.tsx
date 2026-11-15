import {
  ButtonItem,
  Navigation,
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
} from "@decky/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import {
  fetchState,
  previewHaptics,
  selectPreset,
  sendHeartbeat,
  setControlsVisible,
  updateOptions,
} from "./api";
import { beginLayoutEditing } from "./editor-state";
import type { Language, TranslationKey } from "./i18n";
import { TOUCH_LAYOUT_ROUTE } from "./route";
import { touchText } from "./touch_i18n";
import type { OptionsPatch, PluginState } from "./types";

// One page, one scrolling column. The page keeps its natural height so the
// Controller's own tab panel stays the only scroll owner; nothing here clips.
const pageStyles = `
  .touch-controller-page {
    box-sizing: border-box;
    width: 100%;
    min-width: 0;
  }
`;

const hintStyle: CSSProperties = {
  fontSize: 12,
  lineHeight: 1.55,
  opacity: 0.86,
  wordBreak: "break-word",
};

const errorStyle: CSSProperties = {
  fontSize: 12,
  lineHeight: 1.5,
  color: "#ff8a80",
  wordBreak: "break-word",
};

const presetRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 10,
  width: "100%",
  minHeight: 44,
};

const presetNameStyle: CSSProperties = {
  fontSize: 16,
  lineHeight: 1.3,
  flex: "1 1 auto",
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const presetMetaStyle: CSSProperties = {
  flex: "0 0 auto",
  fontSize: 12,
  opacity: 0.9,
};

type TextFn = (
  key: TranslationKey,
  variables?: Record<string, string | number>,
) => string;

const describeError = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

export function TouchContent({ locale }: { locale: Language }) {
  const text = useCallback<TextFn>(
    (key, variables) => touchText(locale, key, variables),
    [locale],
  );

  const [state, setState] = useState<PluginState | null>(null);
  const [busy, setBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const stateRef = useRef<PluginState | null>(null);
  const confirmedRef = useRef<PluginState | null>(null);
  const busyRef = useRef(false);
  const previewingRef = useRef(false);
  const pendingOptionsRef = useRef<OptionsPatch | null>(null);

  const adopt = (next: PluginState) => {
    stateRef.current = next;
    confirmedRef.current = next;
    setState(next);
  };

  useEffect(() => {
    let disposed = false;
    let inFlight = false;

    const poll = async () => {
      if (inFlight) {
        return;
      }
      inFlight = true;
      try {
        const next = await fetchState();
        if (disposed) {
          return;
        }
        // A write owns the authoritative snapshot until it settles.  Skipping
        // adoption here also keeps a stale poll from overwriting an optimistic
        // slider value or a just-selected preset.
        if (!busyRef.current) {
          adopt(next);
        }
        setStatusError(null);
      } catch (error) {
        if (!disposed) {
          setStatusError(text("error.status", { error: describeError(error) }));
        }
      } finally {
        inFlight = false;
      }
    };

    void poll();
    const timer = window.setInterval(() => {
      void poll();
    }, 1000);

    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, []);

  const runOptions = async (patch: OptionsPatch) => {
    if (busyRef.current) {
      pendingOptionsRef.current = {
        ...(pendingOptionsRef.current ?? {}),
        ...patch,
      };
      return;
    }

    busyRef.current = true;
    setBusy(true);
    setActionError(null);

    let currentPatch: OptionsPatch | null = patch;
    try {
      while (currentPatch) {
        const next = await updateOptions(currentPatch);
        const queued = pendingOptionsRef.current;
        pendingOptionsRef.current = null;
        if (queued) {
          // Do not publish the older response over the queued optimistic
          // value; send the newest patch first, then adopt its state.
          currentPatch = queued;
          continue;
        }
        adopt(next);
        currentPatch = null;
      }
    } catch (error) {
      pendingOptionsRef.current = null;
      const confirmed = confirmedRef.current;
      if (confirmed) {
        stateRef.current = confirmed;
        setState(confirmed);
      }
      setActionError(text("error.save", { error: describeError(error) }));
    } finally {
      busyRef.current = false;
      setBusy(false);
      const queued = pendingOptionsRef.current;
      if (queued) {
        pendingOptionsRef.current = null;
        void runOptions(queued);
      }
    }
  };

  const changeOptions = (patch: OptionsPatch) => {
    const current = stateRef.current;
    if (!current) {
      return;
    }
    // Slider drags must stay responsive while one update_options request is
    // in flight; the queue keeps at most one newest patch.
    const next: PluginState = {
      ...current,
      settings: { ...current.settings, ...patch },
    };
    stateRef.current = next;
    setState(next);
    setActionError(null);
    void runOptions(patch);
  };

  const choosePreset = async (presetId: string) => {
    if (busyRef.current) {
      return;
    }
    if (stateRef.current?.settings.active_preset_id === presetId) {
      return;
    }

    busyRef.current = true;
    setBusy(true);
    setActionError(null);
    try {
      adopt(await selectPreset(presetId));
    } catch (error) {
      setActionError(text("error.preset", { error: describeError(error) }));
    } finally {
      busyRef.current = false;
      setBusy(false);
      const queued = pendingOptionsRef.current;
      if (queued) {
        pendingOptionsRef.current = null;
        void runOptions(queued);
      }
    }
  };

  const toggleControlsVisible = async (visible: boolean) => {
    if (!stateRef.current || busyRef.current) {
      return;
    }

    busyRef.current = true;
    setBusy(true);
    setActionError(null);
    try {
      adopt(await setControlsVisible(visible));
    } catch (error) {
      setActionError(text("error.visibility", { error: describeError(error) }));
    } finally {
      busyRef.current = false;
      setBusy(false);
      const queued = pendingOptionsRef.current;
      if (queued) {
        pendingOptionsRef.current = null;
        void runOptions(queued);
      }
    }
  };

  const tryHaptics = async () => {
    const current = stateRef.current;
    if (
      !current ||
      busyRef.current ||
      previewingRef.current ||
      !current.settings.enabled ||
      !current.settings.haptics_enabled ||
      (current.settings.haptics_strength ?? 0) <= 0 ||
      current.status.haptics_available !== true
    ) {
      return;
    }

    previewingRef.current = true;
    setPreviewing(true);
    setActionError(null);
    try {
      const result = await previewHaptics();
      if (!result.queued) {
        setActionError(text("error.hapticsBusy"));
      }
    } catch (error) {
      setActionError(text("error.haptics", { error: describeError(error) }));
    } finally {
      previewingRef.current = false;
      setPreviewing(false);
    }
  };

  const openEditor = () => {
    if (busyRef.current) {
      return;
    }
    // Stop game capture before the route transition, then let the global
    // heartbeat loop keep sending null for the whole editor mount.
    beginLayoutEditing();
    void sendHeartbeat(null).catch((error) => {
      console.warn("[armada-control] touch editor heartbeat failed", error);
    });
    Navigation.Navigate(TOUCH_LAYOUT_ROUTE);
    Navigation.CloseSideMenus();
  };

  const presets = state?.presets ?? [];
  const activePresetId = state?.settings.active_preset_id ?? null;
  const activePreset =
    presets.find((preset) => preset.id === activePresetId) ?? null;
  const activePresetName = activePreset
    ? `${activePreset.name}${
        activePreset.builtin
          ? text("common.builtinSuffix")
          : text("common.customSuffix")
      }`
    : null;
  const hapticsStrength = state?.settings.haptics_strength ?? 25;
  const previewDisabled =
    !state ||
    !state.settings.enabled ||
    !state.settings.haptics_enabled ||
    hapticsStrength <= 0 ||
    state.status.haptics_available !== true ||
    busy ||
    previewing;

  return (
    <div className="touch-controller-page">
      <style>{pageStyles}</style>
      {actionError ? (
        <div role="alert" style={errorStyle}>
          {actionError}
        </div>
      ) : null}

      <PanelSection title={text("common.title")}>
        {state ? (
          <>
            <PanelSectionRow>
              <ToggleField
                label={text("common.enable")}
                checked={state.settings.enabled}
                onChange={(value) => changeOptions({ enabled: value })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField
                label={text("common.show")}
                description={text("common.showDescription")}
                checked={state.status.controls_visible !== false}
                disabled={
                  !state.settings.enabled ||
                  state.status.controls_visible === undefined ||
                  busy
                }
                onChange={(value) => void toggleControlsVisible(value)}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <div style={hintStyle}>{text("common.captureHint")}</div>
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField
                label={text("common.haptics")}
                description={text("common.hapticsDescription")}
                checked={state.settings.haptics_enabled}
                onChange={(value) => changeOptions({ haptics_enabled: value })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField
                label={text("common.strength")}
                description={`${hapticsStrength}%`}
                value={hapticsStrength}
                min={0}
                max={100}
                step={5}
                onChange={(value) =>
                  changeOptions({ haptics_strength: value })
                }
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                disabled={previewDisabled}
                onClick={() => void tryHaptics()}
              >
                <span
                  style={{
                    fontSize: 16,
                    fontWeight: 700,
                    opacity: previewDisabled ? 0.55 : 1,
                  }}
                >
                  {previewing
                    ? text("common.previewing")
                    : text("common.preview")}
                </span>
              </ButtonItem>
            </PanelSectionRow>
            {state.settings.enabled &&
            state.status.haptics_available === false ? (
              <PanelSectionRow>
                <div style={hintStyle}>{text("common.hapticsUnavailable")}</div>
              </PanelSectionRow>
            ) : null}
          </>
        ) : (
          <PanelSectionRow>
            <div style={hintStyle}>{text("common.reading")}</div>
          </PanelSectionRow>
        )}
      </PanelSection>

      {state ? (
        <PanelSection title={text("common.display")}>
          <PanelSectionRow>
            <SliderField
              label={text("common.opacity")}
              value={state.settings.opacity}
              min={0.15}
              max={1}
              step={0.05}
              onChange={(value) => changeOptions({ opacity: value })}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField
              label={text("common.scale")}
              value={state.settings.scale}
              min={0.6}
              max={1.4}
              step={0.05}
              onChange={(value) => changeOptions({ scale: value })}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField
              label={text("common.deadzone")}
              value={state.settings.deadzone}
              min={0}
              max={0.5}
              step={0.05}
              onChange={(value) => changeOptions({ deadzone: value })}
            />
          </PanelSectionRow>
        </PanelSection>
      ) : null}

      <PanelSection title={text("layout.title")}>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={openEditor}>
            <span style={{ fontSize: 16, fontWeight: 700 }}>
              {text("layout.openEditor")}
            </span>
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={hintStyle}>{text("layout.openEditorHint")}</div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={hintStyle}>
            {text("layout.currentPreset", {
              name:
                activePresetName ??
                (state ? text("layout.unmatched") : text("layout.reading")),
            })}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={hintStyle}>{text("layout.presetsHint")}</div>
        </PanelSectionRow>
        {presets.length > 0 ? (
          presets.map((preset) => {
            const selected = preset.id === activePresetId;
            return (
              <PanelSectionRow key={preset.id}>
                <ButtonItem
                  layout="below"
                  onClick={() => void choosePreset(preset.id)}
                >
                  <span style={presetRowStyle}>
                    <span
                      style={{
                        ...presetNameStyle,
                        fontWeight: selected ? 700 : 500,
                      }}
                    >
                      {preset.name}
                    </span>
                    <span
                      style={{
                        ...presetMetaStyle,
                        color: selected
                          ? "#8ce99a"
                          : "rgba(255,255,255,0.72)",
                      }}
                    >
                      {selected
                        ? text("common.selected")
                        : preset.builtin
                          ? text("common.builtin")
                          : text("common.custom")}
                    </span>
                  </span>
                </ButtonItem>
              </PanelSectionRow>
            );
          })
        ) : (
          <PanelSectionRow>
            <div style={hintStyle}>
              {state
                ? text("layout.noPresets")
                : text("layout.readingPresets")}
            </div>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title={text("status.title")}>
        <PanelSectionRow>
          {state ? (
            <div style={hintStyle}>
              <div>
                {text("status.state", {
                  value: state.status.detail || state.status.state,
                })}
              </div>
              <div>
                {text("status.capture", {
                  value: state.status.capturing
                    ? text("status.yes")
                    : text("status.no"),
                })}
              </div>
              {state.status.device ? (
                <div>
                  {text("status.device", { device: state.status.device })}
                  {typeof state.status.rotation === "number"
                    ? text("status.rotation", {
                        rotation: state.status.rotation,
                      })
                    : ""}
                </div>
              ) : null}
              <div>
                {text("status.haptics", {
                  value:
                    state.status.haptics_available === true
                      ? text("status.available")
                      : state.status.haptics_available === false
                        ? text("status.unavailable")
                        : text("status.unknown"),
                })}
              </div>
              {state.status.haptics_detail ? (
                <div>{state.status.haptics_detail}</div>
              ) : null}
              {busy ? <div>{text("status.syncing")}</div> : null}
            </div>
          ) : (
            <div style={hintStyle}>{text("common.reading")}</div>
          )}
        </PanelSectionRow>
        {statusError ? (
          <PanelSectionRow>
            <div style={errorStyle}>{statusError}</div>
          </PanelSectionRow>
        ) : null}
      </PanelSection>
    </div>
  );
}
