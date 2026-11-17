import {
  ButtonItem,
  ConfirmModal,
  DropdownItem,
  Navigation,
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
  showModal,
} from "@decky/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import {
  deletePreset,
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
import type {
  ButtonColor,
  OptionsPatch,
  PluginState,
  Preset,
} from "./types";

// One page, one scrolling column. The page keeps its natural height so the
// Controller's own tab panel stays the only scroll owner; nothing here clips.
const pageStyles = `
  .touch-controller-page {
    box-sizing: border-box;
    width: 100%;
    min-width: 0;
  }
  .touch-controller-page .touch-page-row {
    box-sizing: border-box;
    width: 100%;
    min-width: 0;
  }
  .touch-controller-page .touch-preset-grid {
    box-sizing: border-box;
    display: grid;
    width: 100%;
    min-width: 0;
    grid-template-columns: minmax(0, 1fr);
    gap: 4px;
    align-items: stretch;
  }
  .touch-controller-page .touch-preset-grid.touch-preset-grid--deletable {
    grid-template-columns: minmax(0, 1fr) 88px;
  }
  .touch-controller-page .touch-preset-select,
  .touch-controller-page .touch-preset-delete {
    box-sizing: border-box;
    min-width: 0;
    width: 100%;
    display: block;
  }
  .touch-controller-page .touch-preset-delete button {
    min-width: 0;
    width: 100%;
    box-sizing: border-box;
    padding-left: 8px;
    padding-right: 8px;
  }
  .touch-controller-page .touch-preset-select-inner {
    box-sizing: border-box;
    display: grid;
    width: 100%;
    min-width: 0;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    column-gap: 8px;
    min-height: 20px;
  }
  .touch-controller-page .touch-preset-name {
    display: block;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 14px;
    line-height: 1.3;
  }
  .touch-controller-page .touch-preset-meta {
    display: block;
    flex: 0 0 auto;
    font-size: 11px;
    line-height: 1.3;
    white-space: nowrap;
  }
  .touch-controller-page .touch-preset-delete-label {
    display: block;
    font-size: 12px;
    line-height: 1.3;
    white-space: nowrap;
  }
  .touch-controller-page .touch-tip {
    font-size: 12px;
    line-height: 1.45;
    opacity: 0.8;
    word-break: break-word;
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
  // Every accepted mutation counts here until it is acknowledged, including
  // the ones still waiting in the shared api queue.
  const outstandingRef = useRef(0);
  // The tab switch unmounts this page; work awaited by the old instance must
  // never publish state afterwards. Accepted mutations still run to the end.
  const aliveRef = useRef(true);
  // Every write that owns settings/status bumps this counter, so a poll can
  // tell whether a write started after its own request went out.
  const writeGenerationRef = useRef(0);

  const adopt = (next: PluginState) => {
    stateRef.current = next;
    confirmedRef.current = next;
    setState(next);
  };

  useEffect(() => {
    let disposed = false;
    let inFlight = false;

    aliveRef.current = true;

    const poll = async () => {
      if (inFlight) {
        return;
      }
      inFlight = true;
      const generationAtStart = writeGenerationRef.current;
      const busyAtStart = busyRef.current;
      try {
        const next = await fetchState();
        if (disposed) {
          return;
        }
        // A snapshot may only be adopted when it was requested and delivered
        // inside the same write generation: if a write started while the poll
        // was outstanding, the older snapshot would roll back a value the
        // write has already confirmed.
        if (
          !busyAtStart &&
          !busyRef.current &&
          writeGenerationRef.current === generationAtStart
        ) {
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
      aliveRef.current = false;
      window.clearInterval(timer);
    };
  }, []);

  const beginWrite = () => {
    outstandingRef.current += 1;
    if (busyRef.current) {
      return;
    }
    busyRef.current = true;
    if (aliveRef.current) {
      setBusy(true);
    }
  };

  const endWrite = () => {
    outstandingRef.current = Math.max(0, outstandingRef.current - 1);
    if (outstandingRef.current > 0 || !busyRef.current) {
      return;
    }
    busyRef.current = false;
    if (aliveRef.current) {
      setBusy(false);
    }
  };

  const runMutation = async (
    operation: () => Promise<PluginState>,
    describeFailure: (error: unknown) => string,
  ) => {
    const generation = writeGenerationRef.current + 1;
    writeGenerationRef.current = generation;
    beginWrite();
    if (aliveRef.current) {
      setActionError(null);
    }
    try {
      const next = await operation();
      // Every acknowledgement is the newest truth for rollback, but only the
      // newest action may publish it: an older response would rewind the
      // optimistic value that a later action already shows.
      confirmedRef.current = next;
      if (aliveRef.current && writeGenerationRef.current === generation) {
        stateRef.current = next;
        setState(next);
      }
    } catch (error) {
      if (aliveRef.current) {
        if (writeGenerationRef.current === generation) {
          const confirmed = confirmedRef.current;
          if (confirmed) {
            stateRef.current = confirmed;
            setState(confirmed);
          }
        }
        setActionError(describeFailure(error));
      }
    } finally {
      endWrite();
    }
  };

  const changeOptions = (patch: OptionsPatch) => {
    const current = stateRef.current;
    if (!current) {
      return;
    }
    // Slider drags must stay responsive; the shared api queue merges the
    // readings that arrive before the write reaches the backend.
    const next: PluginState = {
      ...current,
      settings: { ...current.settings, ...patch },
    };
    stateRef.current = next;
    setState(next);
    setActionError(null);
    void runMutation(
      () => updateOptions(patch),
      (error) => text("error.save", { error: describeError(error) }),
    );
  };

  const choosePreset = (presetId: string) => {
    if (!aliveRef.current) {
      return;
    }
    // The id on screen is the newest truth only while nothing is queued: an
    // earlier choice may still be on the wire, so the id it shows as active is
    // not the value this click repeats. Submit it instead and let the shared
    // queue send both in the order the user made them.
    if (
      !busyRef.current &&
      stateRef.current?.settings.active_preset_id === presetId
    ) {
      return;
    }
    void runMutation(
      () => selectPreset(presetId),
      (error) => text("error.preset", { error: describeError(error) }),
    );
  };

  const toggleControlsVisible = (visible: boolean) => {
    if (!aliveRef.current || !stateRef.current) {
      return;
    }
    void runMutation(
      () => setControlsVisible(visible),
      (error) => text("error.visibility", { error: describeError(error) }),
    );
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

  const promptDeletePreset = (preset: Preset) => {
    if (!aliveRef.current || preset.builtin) {
      return;
    }
    showModal(
      <ConfirmModal
        strTitle={text("layout.deleteConfirmTitle")}
        strDescription={text("layout.deleteConfirm", { name: preset.name })}
        strOKButtonText={text("layout.deleteConfirmOk")}
        strCancelButtonText={text("editor.cancel")}
        bDestructiveWarning
        onOK={() => {
          if (!aliveRef.current) {
            return;
          }
          void runMutation(
            () => deletePreset(preset.id),
            (error) =>
              text("error.deletePreset", { error: describeError(error) }),
          );
        }}
      />,
    );
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
                onChange={(value) => toggleControlsVisible(value)}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <div style={hintStyle}>{text("common.captureHint")}</div>
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField
                label={text("common.fullscreenRightStick")}
                checked={state.settings.right_stick_fullscreen ?? false}
                disabled={!state.settings.enabled || busy}
                onChange={(value) =>
                  changeOptions({ right_stick_fullscreen: value })
                }
              />
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
              <div className="touch-page-row">
                <ButtonItem
                  layout="below"
                  childrenContainerWidth="max"
                  disabled={previewDisabled}
                  onClick={() => void tryHaptics()}
                >
                  <span
                    style={{
                      fontSize: 14,
                      fontWeight: 600,
                      opacity: previewDisabled ? 0.55 : 1,
                    }}
                  >
                    {previewing
                      ? text("common.previewing")
                      : text("common.preview")}
                  </span>
                </ButtonItem>
              </div>
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
            <DropdownItem
              label={text("common.color")}
              selectedOption={state.settings.button_color ?? "white"}
              rgOptions={[
                { label: text("common.colorWhite"), data: "white" },
                { label: text("common.colorBlack"), data: "black" },
              ]}
              onChange={(option) =>
                changeOptions({ button_color: option.data as ButtonColor })
              }
            />
          </PanelSectionRow>
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
          <div className="touch-page-row">
            <ButtonItem
              layout="below"
              childrenContainerWidth="max"
              onClick={openEditor}
            >
              <span style={{ fontSize: 14, fontWeight: 600 }}>
                {text("layout.openEditor")}
              </span>
            </ButtonItem>
          </div>
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
          <div className="touch-tip">{text("layout.openEditorHint")}</div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div className="touch-tip">{text("layout.presetsHint")}</div>
        </PanelSectionRow>
        {presets.length > 0 ? (
          presets.map((preset) => {
            const selected = preset.id === activePresetId;
            const deletable = !preset.builtin;
            return (
              <PanelSectionRow key={preset.id}>
                <div
                  className={`touch-page-row touch-preset-grid${
                    deletable ? " touch-preset-grid--deletable" : ""
                  }`}
                >
                  <div className="touch-preset-select">
                    <ButtonItem
                      layout="below"
                      childrenContainerWidth="max"
                      onClick={() => choosePreset(preset.id)}
                    >
                      <span className="touch-preset-select-inner">
                        <span
                          className="touch-preset-name"
                          style={{ fontWeight: selected ? 700 : 500 }}
                        >
                          {preset.name}
                        </span>
                        <span
                          className="touch-preset-meta"
                          style={{
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
                  </div>
                  {deletable ? (
                    <div className="touch-preset-delete">
                      <ButtonItem
                        layout="below"
                        childrenContainerWidth="max"
                        onClick={() => promptDeletePreset(preset)}
                      >
                        <span className="touch-preset-delete-label">
                          {text("layout.delete")}
                        </span>
                      </ButtonItem>
                    </div>
                  ) : null}
                </div>
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
