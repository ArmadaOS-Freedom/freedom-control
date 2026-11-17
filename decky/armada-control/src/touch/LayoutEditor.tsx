import { DialogButton, Focusable, Navigation, SliderField, TextField } from "@decky/ui";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { createPortal } from "react-dom";
import { useLocale } from "../hooks/useLocale";
import { fetchState, savePreset, sendHeartbeat } from "./api";
import {
  beginLayoutEditing,
  effectiveLayout,
  endLayoutEditing,
  faceGroupBounds,
  faceGroupDiameterPx,
  findRightFaceGroup,
  MIN_TOUCH_PX,
  moveFaceGroup,
  normalizeCircularSticks,
  resizeCircularStick,
  resizeFaceGroup,
} from "./editor-state";
import type { TranslationKey } from "./i18n";
import { touchLanguage, touchText } from "./touch_i18n";
import type { Control, DpadMode, State } from "./types";

type TextFn = (
  key: TranslationKey,
  variables?: Record<string, string | number>,
) => string;

const KIND_KEYS: Record<Control["kind"], TranslationKey> = {
  button: "editor.kind.button",
  stick: "editor.kind.stick",
  dpad: "editor.kind.dpad",
  pad: "editor.kind.pad",
};

const KIND_COLOR: Record<Control["kind"], string> = {
  button: "#6ea8fe",
  stick: "#8ce99a",
  dpad: "#ffd43b",
  pad: "#b197fc",
};

type EditMode = "single" | "group";

const EDIT_MODE_VALUES: readonly EditMode[] = ["single", "group"];
const DPAD_MODE_VALUES: readonly DpadMode[] = ["four_way", "eight_way"];

/**
 * DialogButton's declared props cover only style/disabled/onClick, so the ARIA
 * state travels in a structurally compatible object instead of undeclared JSX
 * attributes (and never as a `role`, which the component does not accept).
 */
type ChoiceAriaProps = {
  "aria-label": string;
  "aria-pressed": boolean;
};

const choiceAria = (label: string, selected: boolean): ChoiceAriaProps => ({
  "aria-label": label,
  "aria-pressed": selected,
});

const BUTTON_KEYS: Record<string, TranslationKey> = {
  a: "editor.control.a",
  b: "editor.control.b",
  x: "editor.control.x",
  y: "editor.control.y",
  lb: "editor.control.lb",
  rb: "editor.control.rb",
  lt: "editor.control.lt",
  rt: "editor.control.rt",
  back: "editor.control.back",
  start: "editor.control.start",
  guide: "editor.control.guide",
  qam: "editor.control.qam",
  l3: "editor.control.l3",
  r3: "editor.control.r3",
};

const clamp = (value: number, min: number, max: number): number =>
  Math.min(max, Math.max(min, value));

const describeError = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

const controlName = (control: Control, text: TextFn): string => {
  if (control.kind === "stick") {
    return control.output === "right"
      ? text("editor.control.rightStick")
      : text("editor.control.leftStick");
  }
  if (control.kind === "dpad") {
    return text("editor.control.dpad");
  }
  if (control.kind === "pad") {
    return control.output === "right"
      ? text("editor.control.rightPad")
      : text("editor.control.leftPad");
  }
  if (control.kind === "button" && control.label && control.label.trim()) {
    return control.label.trim();
  }
  const key = BUTTON_KEYS[control.output];
  if (key) {
    return text(key);
  }
  return text("editor.control.button");
};

const releaseCapture = (capture: Element, pointerId: number): void => {
  try {
    if (capture.hasPointerCapture(pointerId)) {
      capture.releasePointerCapture(pointerId);
    }
  } catch {
    // The element may already be detached after a route change.
  }
};

type Viewport = {
  width: number;
  height: number;
};

type DragState = {
  id: string;
  pointerId: number;
  offsetX: number;
  offsetY: number;
  capture: Element;
  /**
   * Frozen cluster snapshot for a right-hand four-button drag; null while a
   * single control is dragged. Keeping the snapshot makes every pointer sample
   * an absolute delta, so a group drag cannot accumulate rounding drift.
   */
  group: {
    ids: string[];
    startLayout: Control[];
    startX: number;
    startY: number;
  } | null;
};

type ResizeState = {
  id: string;
  pointerId: number;
  startX: number;
  startY: number;
  startW: number;
  startH: number;
  x: number;
  y: number;
  capture: Element;
};

const rootStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  width: "100vw",
  height: "100vh",
  overflow: "hidden",
  background: "#05080c",
  color: "#f4f7fb",
  touchAction: "auto",
  userSelect: "none",
  boxSizing: "border-box",
  zIndex: 100000,
};

const svgStyle: CSSProperties = {
  display: "block",
  width: "100%",
  height: "100%",
  touchAction: "none",
};

// Expanded inspector: docked left or right and limited to
// 320 CSS px wide and viewport-minus-16px tall. Header/body/footer are laid
// out as a flex column so the body shrinks and scrolls while the header and
// footer stay reachable.
const inspectorStyle: CSSProperties = {
  position: "absolute",
  width: "min(320px, calc(100vw - 16px))",
  maxHeight: "calc(100vh - 16px)",
  display: "flex",
  flexDirection: "column",
  minHeight: 0,
  boxSizing: "border-box",
  borderRadius: 14,
  border: "1px solid rgba(255,255,255,0.18)",
  background: "rgba(14,20,28,0.97)",
  boxShadow: "0 12px 40px rgba(0,0,0,0.55)",
  color: "#f4f7fb",
  overflow: "hidden",
  zIndex: 2,
  touchAction: "auto",
};

const inspectorHeaderStyle: CSSProperties = {
  flex: "0 0 auto",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  minWidth: 0,
  padding: "8px 10px",
  borderBottom: "1px solid rgba(255,255,255,0.12)",
  boxSizing: "border-box",
};

const inspectorTitleStyle: CSSProperties = {
  flex: "1 1 auto",
  minWidth: 0,
  fontSize: 14,
  fontWeight: 700,
  lineHeight: 1.25,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const inspectorHeaderActionsStyle: CSSProperties = {
  flex: "0 0 auto",
  display: "flex",
  alignItems: "center",
  gap: 6,
  minWidth: 0,
};

const inspectorBodyStyle: CSSProperties = {
  flex: "1 1 auto",
  minHeight: 0,
  overflowY: "auto",
  overflowX: "hidden",
  WebkitOverflowScrolling: "touch",
  touchAction: "pan-y",
  boxSizing: "border-box",
  padding: "8px 10px",
  overscrollBehavior: "contain",
};

const inspectorFooterStyle: CSSProperties = {
  flex: "0 0 auto",
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: 8,
  minWidth: 0,
  padding: "8px 10px",
  borderTop: "1px solid rgba(255,255,255,0.12)",
  boxSizing: "border-box",
};

const collapsedStripStyle: CSSProperties = {
  position: "absolute",
  display: "flex",
  flexWrap: "nowrap",
  alignItems: "center",
  gap: 6,
  maxWidth: "calc(100vw - 16px)",
  boxSizing: "border-box",
  padding: "6px 8px",
  borderRadius: 12,
  border: "1px solid rgba(255,255,255,0.18)",
  background: "rgba(14,20,28,0.96)",
  boxShadow: "0 8px 24px rgba(0,0,0,0.45)",
  color: "#f4f7fb",
  zIndex: 2,
  touchAction: "auto",
};

const hintStyle: CSSProperties = {
  fontSize: 12,
  lineHeight: 1.5,
  opacity: 0.86,
  wordBreak: "break-word",
  overflowWrap: "anywhere",
  marginTop: 6,
};

const metaStyle: CSSProperties = {
  ...hintStyle,
  opacity: 0.72,
};

const errorStyle: CSSProperties = {
  fontSize: 12,
  lineHeight: 1.5,
  color: "#ff8a80",
  wordBreak: "break-word",
  overflowWrap: "anywhere",
  marginTop: 6,
};

const buttonRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  marginTop: 8,
  minWidth: 0,
};

const buttonRowFullStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: 6,
  marginTop: 8,
  minWidth: 0,
};

// Applies to the actual DialogButton. Steam's DialogButton defaults to
// width:100% and stretches in flex rows; the explicit width/flex/minWidth/
// padding keeps horizontal rows horizontal.
const actionButtonStyle: CSSProperties = {
  width: "auto",
  flex: "0 0 auto",
  minWidth: 0,
  minHeight: 40,
  padding: "0 10px",
  boxSizing: "border-box",
  borderRadius: 10,
  border: "1px solid rgba(255,255,255,0.28)",
  background: "#243244",
  color: "#ffffff",
  fontSize: 13,
  fontWeight: 600,
  lineHeight: 1.2,
  whiteSpace: "normal",
  textAlign: "center",
  cursor: "pointer",
  touchAction: "manipulation",
  overflowWrap: "anywhere",
};

const primaryButtonStyle: CSSProperties = {
  ...actionButtonStyle,
  borderColor: "#3d7bfd",
  background: "#3d7bfd",
};

const compactButtonStyle: CSSProperties = {
  width: "auto",
  flex: "0 0 auto",
  minWidth: 0,
  minHeight: 36,
  padding: "0 8px",
  boxSizing: "border-box",
  borderRadius: 8,
  border: "1px solid rgba(255,255,255,0.28)",
  background: "transparent",
  color: "#ffffff",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  touchAction: "manipulation",
};

// Footer actions fill their grid cells; explicit override of DialogButton's
// default width:100% behavior is required at the button level.
const footerButtonStyle: CSSProperties = {
  ...actionButtonStyle,
  width: "100%",
  flex: "1 1 auto",
};

const footerPrimaryStyle: CSSProperties = {
  ...primaryButtonStyle,
  width: "100%",
  flex: "1 1 auto",
};

const modeRowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "stretch",
  gap: 6,
  marginTop: 10,
  fontSize: 13,
  fontWeight: 600,
  minWidth: 0,
};

const choiceGroupStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: 6,
  minWidth: 0,
};

const choiceButtonStyle = (selected: boolean): CSSProperties => ({
  ...actionButtonStyle,
  width: "100%",
  flex: "1 1 auto",
  borderColor: selected ? "#3d7bfd" : "rgba(255,255,255,0.28)",
  background: selected ? "#3d7bfd" : actionButtonStyle.background,
});

const confirmBoxStyle: CSSProperties = {
  marginTop: 8,
  padding: "8px 10px",
  borderRadius: 10,
  border: "1px solid rgba(255,212,59,0.45)",
  background: "rgba(255,212,59,0.08)",
  boxSizing: "border-box",
};

const nameFieldWrapStyle: CSSProperties = {
  marginTop: 8,
  minWidth: 0,
  userSelect: "text",
  touchAction: "auto",
};

type ToolbarPosition = "left" | "right";

// Expanded inspector docks to a side: full available height, pinned left or
// right so it never squeezes the canvas. top is always 8 with the unchanged
// maxHeight; only the horizontal anchor swaps.
const inspectorPositionStyle: Record<ToolbarPosition, CSSProperties> = {
  left: { top: 8, bottom: "auto", left: 8, right: "auto" },
  right: { top: 8, bottom: "auto", left: "auto", right: 8 },
};

const collapsedPositionStyle: Record<ToolbarPosition, CSSProperties> = {
  left: { top: 8, bottom: "auto", left: 8, right: "auto" },
  right: { top: 8, bottom: "auto", left: "auto", right: 8 },
};

const nextToolbarPosition: Record<ToolbarPosition, ToolbarPosition> = {
  left: "right",
  right: "left",
};

// Label names the destination side, not the current one.
const MOVE_KEYS: Record<ToolbarPosition, TranslationKey> = {
  left: "editor.moveRight",
  right: "editor.moveLeft",
};

export function LayoutEditor() {
  const controllerLocale = useLocale();
  const locale = touchLanguage(controllerLocale);
  const text = useCallback<TextFn>(
    (key, variables) => touchText(locale, key, variables),
    [locale],
  );

  const rootRef = useRef<HTMLDivElement | null>(null);
  // Focusable's ref is a focus-manager handle, not the DOM node, so the DOM
  // element is captured separately for measure/pointer math and cleanup.
  const rootDomRef = useRef<HTMLDivElement | null>(null);
  const viewportRef = useRef<Viewport>({ width: 1, height: 1 });
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const resizeRef = useRef<ResizeState | null>(null);
  const groupBaselineRef = useRef<{
    layout: Control[];
    ids: string[];
  } | null>(null);
  const busyRef = useRef(false);
  // Set by this instance's own lifecycle effect, so an async save can tell
  // whether the editor it started in is still the one on screen.
  const mountedRef = useRef(false);

  const [viewport, setViewport] = useState<Viewport>({ width: 1, height: 1 });
  const [state, setState] = useState<State | null>(null);
  const [draft, setDraft] = useState<Control[] | null>(null);
  const [draftMode, setDraftMode] = useState<DpadMode>("four_way");
  const [draftBase, setDraftBase] = useState("tablet_standard");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editMode, setEditMode] = useState<EditMode>("single");
  const [pressedId, setPressedId] = useState<string | null>(null);
  const [presetName, setPresetName] = useState(() => text("editor.customPreset"));
  const [collapsed, setCollapsed] = useState(true);
  const [toolbarPosition, setToolbarPosition] =
    useState<ToolbarPosition>("left");
  const [saving, setSaving] = useState(false);
  const [confirmingReset, setConfirmingReset] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const clearGestureCaptures = () => {
    const drag = dragRef.current;
    if (drag) {
      releaseCapture(drag.capture, drag.pointerId);
    }
    const resize = resizeRef.current;
    if (resize) {
      releaseCapture(resize.capture, resize.pointerId);
    }
    dragRef.current = null;
    resizeRef.current = null;
    groupBaselineRef.current = null;
  };

  const changeEditMode = (next: EditMode) => {
    if (next === editMode || saving || busyRef.current) {
      return;
    }
    // Release whatever the canvas still holds before the selection and the
    // cluster baseline change, so no stale gesture keeps steering the draft.
    clearGestureCaptures();
    setPressedId(null);
    setSelectedId(null);
    setEditMode(next);
  };

  const changeDpadMode = (next: DpadMode) => {
    if (next === draftMode || saving || busyRef.current) {
      return;
    }
    // Draft-only: the mode is persisted with the preset on save.
    clearGestureCaptures();
    setPressedId(null);
    setDraftMode(next);
  };

  useEffect(() => {
    let disposed = false;
    mountedRef.current = true;

    beginLayoutEditing();
    void sendHeartbeat(null).catch((heartbeatError) => {
      console.warn(
        "[armada-control] touch editor heartbeat failed",
        heartbeatError,
      );
    });

    void fetchState()
      .then((next) => {
        if (disposed) {
          return;
        }
        const preset =
          next.presets.find(
            (item) => item.id === next.settings.active_preset_id,
          ) ?? null;

        setState(next);
        setDraft(
          normalizeCircularSticks(
            effectiveLayout(next.settings.layout, next.settings.scale),
            viewportRef.current,
          ),
        );
        setDraftMode(
          next.settings.dpad_mode ?? preset?.dpad_mode ?? "four_way",
        );
        setDraftBase(
          preset?.base_preset_id ??
            (preset?.builtin ? preset.id : "tablet_standard"),
        );
        setConfirmingReset(false);
        if (!preset) {
          setPresetName(text("editor.customPreset"));
        } else if (preset.builtin) {
          setPresetName(`${preset.name}${text("editor.copySuffix")}`);
        } else {
          setPresetName(preset.name);
        }
      })
      .catch((loadFailure) => {
        if (!disposed) {
          setLoadError(
            text("editor.loadError", { error: describeError(loadFailure) }),
          );
        }
      });

    return () => {
      disposed = true;
      mountedRef.current = false;
      endLayoutEditing();
    };
  }, []);

  useLayoutEffect(() => {
    const root = rootDomRef.current;
    if (!root) {
      return;
    }

    const coordinateEPS = 0.5;
    const measure = () => {
      const rect = root.getBoundingClientRect();
      const next: Viewport = {
        width: Math.max(1, rect.width),
        height: Math.max(1, rect.height),
      };
      const previous = viewportRef.current;
      viewportRef.current = next;
      setViewport(next);
      // Re-normalize the existing draft against the freshly measured root so
      // circular sticks keep a canonical width/centered position.
      if (
        Math.abs(previous.width - next.width) > coordinateEPS ||
        Math.abs(previous.height - next.height) > coordinateEPS
      ) {
        setDraft((current) =>
          current ? normalizeCircularSticks(current, next) : current,
        );
      }
    };

    measure();
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(measure);
    observer?.observe(root);
    window.addEventListener("resize", measure);
    window.addEventListener("orientationchange", measure);

    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
      window.removeEventListener("orientationchange", measure);
    };
  }, []);

  useEffect(
    () => () => {
      clearGestureCaptures();
    },
    [],
  );

  const activePresetId = state?.settings.active_preset_id ?? null;
  const activePreset =
    state && activePresetId !== null
      ? (state.presets.find((preset) => preset.id === activePresetId) ??
        null)
      : null;
  const selected =
    draft?.find((control) => control.id === selectedId) ?? null;
  const restoreBase =
    state?.presets.find((preset) => preset.id === draftBase) ??
    state?.presets.find((preset) => preset.id === "tablet_standard") ??
    null;

  const faceGroup = draft ? findRightFaceGroup(draft) : null;
  const group = faceGroup && faceGroup.status === "ok" ? faceGroup : null;
  const groupActive = editMode === "group" && group !== null;
  const groupBounds = group ? faceGroupBounds(group.controls) : null;
  const groupDiameter = group
    ? faceGroupDiameterPx(group.controls, viewport)
    : 0;
  // Ask the pure resizer for the largest feasible diameter so the slider
  // travel matches what resizeFaceGroup will actually apply.
  const groupMaxDiameter =
    draft && group
      ? Math.max(
          MIN_TOUCH_PX,
          Math.floor(
            resizeFaceGroup(
              draft,
              group.ids,
              Math.min(viewport.width, viewport.height),
              viewport,
            ).diameterPx,
          ),
        )
      : Math.max(
          MIN_TOUCH_PX,
          Math.round(Math.min(viewport.width, viewport.height)),
        );
  const groupIssueText =
    faceGroup && faceGroup.status === "missing"
      ? text("editor.groupMissing", {
          outputs: faceGroup.missing
            .map((output) => output.toUpperCase())
            .join(", "),
        })
      : faceGroup && faceGroup.status === "duplicate"
        ? text("editor.groupDuplicate", {
            outputs: faceGroup.duplicates
              .map((output) => output.toUpperCase())
              .join(", "),
          })
        : "";
  const blackButtons = state?.settings.button_color === "black";

  const freezeGroupBaseline = () => {
    if (!draft || !group || !groupActive) {
      return;
    }
    groupBaselineRef.current = { layout: draft, ids: [...group.ids] };
  };

  const clearGroupBaseline = () => {
    groupBaselineRef.current = null;
  };

  const applyGroupSize = (target: number) => {
    if (!draft || !group || !groupActive) {
      return;
    }
    const baseline = groupBaselineRef.current ?? {
      layout: draft,
      ids: [...group.ids],
    };
    groupBaselineRef.current = baseline;
    setDraft(
      resizeFaceGroup(baseline.layout, baseline.ids, target, viewport).layout,
    );
  };

  const toNormalized = (
    clientX: number,
    clientY: number,
  ): { x: number; y: number } | null => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0 || rect.height === 0) {
      return null;
    }
    return {
      x: clamp((clientX - rect.left) / rect.width, 0, 1),
      y: clamp((clientY - rect.top) / rect.height, 0, 1),
    };
  };

  const startDrag = (
    event: ReactPointerEvent<SVGGElement>,
    control: Control,
  ) => {
    if (
      !event.isPrimary ||
      busyRef.current ||
      dragRef.current ||
      resizeRef.current
    ) {
      return;
    }
    event.stopPropagation();
    event.preventDefault();
    // The canvas owns the next layout change; a frozen size baseline from an
    // earlier controller/touch slider session must not be reused.
    groupBaselineRef.current = null;
    setSelectedId(control.id);
    setPressedId(control.id);

    const point = toNormalized(event.clientX, event.clientY);
    if (!point) {
      return;
    }
    const capture = event.currentTarget;
    try {
      capture.setPointerCapture(event.pointerId);
    } catch {
      // Pointer capture is optional on some embedded browser versions.
    }
    dragRef.current = {
      id: control.id,
      pointerId: event.pointerId,
      offsetX: point.x - control.x,
      offsetY: point.y - control.y,
      capture,
      group:
        groupActive &&
        group &&
        draft &&
        group.ids.includes(control.id)
          ? {
              ids: [...group.ids],
              startLayout: draft,
              startX: point.x,
              startY: point.y,
            }
          : null,
    };
  };

  const startResize = (event: ReactPointerEvent<SVGGElement>) => {
    if (
      !event.isPrimary ||
      busyRef.current ||
      groupActive ||
      !selected ||
      dragRef.current ||
      resizeRef.current
    ) {
      return;
    }
    event.stopPropagation();
    event.preventDefault();
    // Resizing replaces the frozen cluster snapshot with fresh geometry.
    groupBaselineRef.current = null;

    const point = toNormalized(event.clientX, event.clientY);
    if (!point) {
      return;
    }
    const capture = event.currentTarget;
    try {
      capture.setPointerCapture(event.pointerId);
    } catch {
      // Pointer capture is optional on some embedded browser versions.
    }
    resizeRef.current = {
      id: selected.id,
      pointerId: event.pointerId,
      startX: point.x,
      startY: point.y,
      startW: selected.w,
      startH: selected.h,
      x: selected.x,
      y: selected.y,
      capture,
    };
  };

  const moveGesture = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (drag) {
      if (drag.pointerId !== event.pointerId) {
        return;
      }
      const point = toNormalized(event.clientX, event.clientY);
      if (!point) {
        return;
      }
      event.preventDefault();
      if (drag.group) {
        const deltaX = point.x - drag.group.startX;
        const deltaY = point.y - drag.group.startY;
        setDraft(
          moveFaceGroup(drag.group.startLayout, drag.group.ids, deltaX, deltaY),
        );
        return;
      }
      setDraft((previous) =>
        previous
          ? previous.map((control) =>
              control.id === drag.id
                ? {
                    ...control,
                    x: clamp(point.x - drag.offsetX, 0, 1 - control.w),
                    y: clamp(point.y - drag.offsetY, 0, 1 - control.h),
                  }
                : control,
            )
          : previous,
      );
      return;
    }

    const resize = resizeRef.current;
    if (!resize || resize.pointerId !== event.pointerId) {
      return;
    }
    const point = toNormalized(event.clientX, event.clientY);
    if (!point) {
      return;
    }
    event.preventDefault();

    const stickTarget = draft?.find((control) => control.id === resize.id);
    if (stickTarget && stickTarget.kind === "stick") {
      const resized = resizeCircularStick(
        {
          ...stickTarget,
          x: resize.x,
          y: resize.y,
          w: resize.startW,
          h: resize.startH,
        },
        viewport,
        (point.x - resize.startX) * viewport.width,
        (point.y - resize.startY) * viewport.height,
      );
      setDraft((previous) =>
        previous
          ? previous.map((control) =>
              control.id === resize.id ? resized : control,
            )
          : previous,
      );
      return;
    }

    const maxWidth = 1 - resize.x;
    const maxHeight = 1 - resize.y;
    const minWidth = Math.min(MIN_TOUCH_PX / viewport.width, maxWidth);
    const minHeight = Math.min(MIN_TOUCH_PX / viewport.height, maxHeight);
    const width = clamp(
      resize.startW + (point.x - resize.startX),
      minWidth,
      maxWidth,
    );
    const height = clamp(
      resize.startH + (point.y - resize.startY),
      minHeight,
      maxHeight,
    );

    setDraft((previous) =>
      previous
        ? previous.map((control) =>
            control.id === resize.id
              ? { ...control, w: width, h: height }
              : control,
          )
        : previous,
    );
  };

  const endGesture = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (drag && drag.pointerId === event.pointerId) {
      dragRef.current = null;
      releaseCapture(drag.capture, drag.pointerId);
      setPressedId(null);
    }
    const resize = resizeRef.current;
    if (resize && resize.pointerId === event.pointerId) {
      resizeRef.current = null;
      releaseCapture(resize.capture, resize.pointerId);
    }
  };

  const deleteSelected = () => {
    if (!selected || saving || busyRef.current || groupActive) {
      return;
    }
    // Release the pointer before the owning <g> leaves the tree; otherwise the
    // removed node could keep an implicit capture for the rest of the gesture.
    clearGestureCaptures();
    setPressedId(null);
    setDraft((previous) =>
      previous
        ? previous.filter((control) => control.id !== selected.id)
        : previous,
    );
    setSelectedId(null);
    setConfirmingReset(false);
  };

  const resetDraftToBase = () => {
    if (!restoreBase || saving || busyRef.current) {
      return;
    }
    clearGestureCaptures();
    setPressedId(null);
    setDraft(
      normalizeCircularSticks(
        effectiveLayout(restoreBase.layout, restoreBase.scale),
        viewportRef.current,
      ),
    );
    setDraftMode(restoreBase.dpad_mode ?? "four_way");
    setDraftBase(restoreBase.id);
    setSelectedId(null);
    setConfirmingReset(false);
    setError(null);
  };

  const closeEditor = () => {
    clearGestureCaptures();
    setPressedId(null);
    endLayoutEditing();
    Navigation.NavigateBack();
  };

  // Controller Back is owned by the root Focusable so a single press cannot
  // both dismiss the reset confirmation and navigate back.
  const handleCancelButton = (event: CustomEvent) => {
    event.stopPropagation();
    event.preventDefault();
    if (saving || busyRef.current) {
      return;
    }
    if (confirmingReset) {
      setConfirmingReset(false);
      return;
    }
    closeEditor();
  };

  const save = async (alwaysNew: boolean) => {
    if (busyRef.current || !state || !draft) {
      return;
    }

    setError(null);
    let name = presetName.trim();
    if (!name) {
      setError(text("editor.nameRequired"));
      return;
    }

    if (alwaysNew) {
      if (activePreset && name === activePreset.name) {
        name = `${activePreset.name}${text("editor.copySuffix")}`;
        setPresetName(name);
      }
    }

    const overwriteId =
      !alwaysNew && activePreset && !activePreset.builtin
        ? activePreset.id
        : null;

    busyRef.current = true;
    setSaving(true);
    try {
      const next = await savePreset(
        name,
        draft,
        1,
        overwriteId,
        draftMode,
        draftBase,
      );
      // The backend already accepted the save, so it must finish — but this
      // instance may have been left behind for a newer page or editor. It may
      // not touch that page's history or its layout-editing flag.
      if (!mountedRef.current) {
        return;
      }
      setState(next);
      clearGestureCaptures();
      setPressedId(null);
      endLayoutEditing();
      Navigation.NavigateBack();
    } catch (saveFailure) {
      if (!mountedRef.current) {
        return;
      }
      setError(text("editor.saveError", { error: describeError(saveFailure) }));
    } finally {
      busyRef.current = false;
      if (mountedRef.current) {
        setSaving(false);
      }
    }
  };

  const renderControl = (control: Control) => {
    const width = viewport.width;
    const height = viewport.height;
    const x = control.x * width;
    const y = control.y * height;
    const w = control.w * width;
    const h = control.h * height;
    const centerX = x + w / 2;
    const centerY = y + h / 2;
    const color = KIND_COLOR[control.kind];
    const name = controlName(control, text);
    const textSize = clamp(
      Math.min(w / 4.2, h / 3.2, (w - 6) / Math.max(1, name.length)),
      7,
      17,
    );

    return (
      <g
        key={control.id}
        data-control-id={control.id}
        data-control-kind={control.kind}
        style={{ cursor: "grab" }}
        onPointerDown={(event) => startDrag(event, control)}
      >
        <rect
          x={x}
          y={y}
          width={w}
          height={h}
          fill="rgba(0,0,0,0.001)"
        />
        {control.kind === "stick" ? (
          <>
            <ellipse
              cx={centerX}
              cy={centerY}
              rx={w / 2}
              ry={h / 2}
              fill={color}
              fillOpacity={0.18}
              stroke={color}
              strokeWidth={2}
            />
            <ellipse
              cx={centerX}
              cy={centerY}
              rx={w * 0.2}
              ry={h * 0.2}
              fill="none"
              stroke={color}
              strokeWidth={2}
            />
            <line
              x1={x + w * 0.18}
              y1={centerY}
              x2={x + w * 0.82}
              y2={centerY}
              stroke={color}
              strokeOpacity={0.45}
              strokeWidth={1.5}
            />
            <line
              x1={centerX}
              y1={y + h * 0.18}
              x2={centerX}
              y2={y + h * 0.82}
              stroke={color}
              strokeOpacity={0.45}
              strokeWidth={1.5}
            />
          </>
        ) : control.kind === "dpad" ? (
          <>
            <rect
              x={x}
              y={y}
              width={w}
              height={h}
              rx={Math.min(w, h) * 0.12}
              fill={color}
              fillOpacity={0.12}
              stroke={color}
              strokeWidth={2}
            />
            <rect
              x={centerX - w * 0.12}
              y={y + h * 0.18}
              width={w * 0.24}
              height={h * 0.64}
              fill={color}
              fillOpacity={0.52}
            />
            <rect
              x={x + w * 0.18}
              y={centerY - h * 0.12}
              width={w * 0.64}
              height={h * 0.24}
              fill={color}
              fillOpacity={0.52}
            />
          </>
        ) : control.kind === "pad" ? (
          <rect
            x={x}
            y={y}
            width={w}
            height={h}
            rx={6}
            fill={color}
            fillOpacity={0.08}
            stroke={color}
            strokeWidth={2}
          />
        ) : (
          <rect
            x={x}
            y={y}
            width={w}
            height={h}
            rx={Math.min(w, h) * 0.18}
            fill={blackButtons ? "#05080c" : color}
            fillOpacity={blackButtons ? 0.65 : 0.24}
            stroke={
              blackButtons
                ? pressedId === control.id
                  ? "#22d3ee"
                  : "#e8f0fb"
                : color
            }
            strokeOpacity={blackButtons ? 0.92 : 1}
            strokeWidth={blackButtons && pressedId === control.id ? 3 : 2}
          />
        )}
        {w >= 32 && h >= 20 ? (
          <text
            x={control.kind === "button" || control.kind === "stick"
              ? centerX
              : x + 7}
            y={control.kind === "button" || control.kind === "stick"
              ? centerY + textSize * 0.35
              : control.kind === "pad"
                ? y + textSize + 6
                : y + h - 7}
            textAnchor={
              control.kind === "button" || control.kind === "stick"
                ? "middle"
                : "start"
            }
            fontSize={textSize}
            fontWeight={600}
            fill={
              blackButtons && control.kind === "button" ? "#f2f8ff" : "#ffffff"
            }
            fillOpacity={0.94}
            pointerEvents="none"
          >
            {name}
          </text>
        ) : null}
      </g>
    );
  };

  return (
    <Focusable
      ref={rootRef}
      className="touch-layout-editor"
      style={rootStyle}
      onCancelButton={handleCancelButton}
    >
      <div
        ref={rootDomRef}
        className="layout-editor-canvas-wrap"
        data-layout-editor="canvas-wrap"
        style={{ position: "absolute", inset: 0, minWidth: 0, minHeight: 0, touchAction: "auto" }}
      >
      <svg
        ref={svgRef}
        className="layout-editor-canvas"
        data-layout-editor="canvas"
        aria-label={text("editor.titleShort")}
        viewBox={`0 0 ${viewport.width} ${viewport.height}`}
        preserveAspectRatio="none"
        style={svgStyle}
        onPointerMove={moveGesture}
        onPointerUp={endGesture}
        onPointerCancel={endGesture}
        onLostPointerCapture={endGesture}
      >
        <rect
          x={0}
          y={0}
          width={viewport.width}
          height={viewport.height}
          fill="#070b10"
          onPointerDown={() => {
            if (!dragRef.current && !resizeRef.current) {
              setSelectedId(null);
            }
          }}
        />
        {[0.25, 0.5, 0.75].map((fraction) => (
          <g key={fraction} pointerEvents="none">
            <line
              x1={fraction * viewport.width}
              y1={0}
              x2={fraction * viewport.width}
              y2={viewport.height}
              stroke="#ffffff"
              strokeOpacity={0.06}
              strokeWidth={1}
            />
            <line
              x1={0}
              y1={fraction * viewport.height}
              x2={viewport.width}
              y2={fraction * viewport.height}
              stroke="#ffffff"
              strokeOpacity={0.06}
              strokeWidth={1}
            />
          </g>
        ))}

        {draft?.map(renderControl)}

        {groupActive && group && groupBounds ? (
          <>
            {group.controls.map((control) => (
              <rect
                key={`group-${control.id}`}
                x={control.x * viewport.width}
                y={control.y * viewport.height}
                width={control.w * viewport.width}
                height={control.h * viewport.height}
                fill="none"
                stroke="#22d3ee"
                strokeOpacity={0.6}
                strokeWidth={2}
                pointerEvents="none"
              />
            ))}
            <rect
              x={groupBounds.x * viewport.width}
              y={groupBounds.y * viewport.height}
              width={groupBounds.w * viewport.width}
              height={groupBounds.h * viewport.height}
              fill="none"
              stroke="#22d3ee"
              strokeWidth={2}
              pointerEvents="none"
            />
          </>
        ) : null}

        {selected ? (
          <>
            <rect
              x={selected.x * viewport.width}
              y={selected.y * viewport.height}
              width={selected.w * viewport.width}
              height={selected.h * viewport.height}
              fill="none"
              stroke="#ffffff"
              strokeWidth={2}
              pointerEvents="none"
            />
            {groupActive ? null : (
              <g
                style={{ cursor: "nwse-resize" }}
                onPointerDown={startResize}
              >
                <rect
                  x={(selected.x + selected.w) * viewport.width - 22}
                  y={(selected.y + selected.h) * viewport.height - 22}
                  width={MIN_TOUCH_PX}
                  height={MIN_TOUCH_PX}
                  fill="rgba(0,0,0,0.001)"
                />
                <rect
                  x={(selected.x + selected.w) * viewport.width - 9}
                  y={(selected.y + selected.h) * viewport.height - 9}
                  width={18}
                  height={18}
                  rx={4}
                  fill="#ffffff"
                  stroke="#101820"
                  strokeWidth={2}
                  pointerEvents="none"
                />
              </g>
            )}
          </>
        ) : null}
      </svg>
      </div>

      {collapsed ? (
        <div
          className="layout-editor-strip"
          data-layout-editor="strip"
          style={{ ...collapsedStripStyle, ...collapsedPositionStyle[toolbarPosition] }}
        >
          <Focusable
            key="strip-actions"
            flow-children="row"
            preferredFocus
            style={{
              display: "flex",
              flexWrap: "nowrap",
              alignItems: "center",
              gap: 6,
              minWidth: 0,
            }}
          >
            <DialogButton
              className="layout-editor-strip-tools"
              data-layout-action="tools"
              style={compactButtonStyle}
              onClick={() => setCollapsed(false)}
            >
              {text("editor.tools")}
            </DialogButton>
            <DialogButton
              className="layout-editor-strip-move"
              data-layout-action="move"
              style={compactButtonStyle}
              onClick={() =>
                setToolbarPosition(nextToolbarPosition[toolbarPosition])
              }
            >
              {text(MOVE_KEYS[toolbarPosition])}
            </DialogButton>
            <DialogButton
              className="layout-editor-strip-save"
              data-layout-action="save-quick"
              style={{ ...compactButtonStyle, borderColor: "#3d7bfd", background: "#3d7bfd" }}
              disabled={saving || !draft}
              onClick={() => void save(false)}
            >
              {saving ? text("editor.saving") : text("editor.save")}
            </DialogButton>
            <DialogButton
              className="layout-editor-strip-cancel"
              data-layout-action="cancel-quick"
              style={compactButtonStyle}
              disabled={saving}
              onClick={() => {
                if (saving || busyRef.current) {
                  return;
                }
                if (confirmingReset) {
                  setConfirmingReset(false);
                  return;
                }
                closeEditor();
              }}
            >
              {text("editor.cancel")}
            </DialogButton>
          </Focusable>
        </div>
      ) : (
        <Focusable
          className="layout-editor-inspector"
          data-layout-editor="inspector"
          flow-children="column"
          preferredFocus
          style={{ ...inspectorStyle, ...inspectorPositionStyle[toolbarPosition] }}
        >
          <div
            className="layout-editor-header"
            data-layout-editor="header"
            style={inspectorHeaderStyle}
          >
            <div style={inspectorTitleStyle}>{text("editor.titleShort")}</div>
            <Focusable flow-children="row" style={inspectorHeaderActionsStyle}>
              <DialogButton
                className="layout-editor-header-move"
                data-layout-action="move"
                style={compactButtonStyle}
                onClick={() =>
                  setToolbarPosition(nextToolbarPosition[toolbarPosition])
                }
              >
                {text(MOVE_KEYS[toolbarPosition])}
              </DialogButton>
              <DialogButton
                className="layout-editor-header-collapse"
                data-layout-action="collapse"
                style={compactButtonStyle}
                onClick={() => setCollapsed(true)}
              >
                {text("editor.collapse")}
              </DialogButton>
            </Focusable>
          </div>

          <div
            className="layout-editor-body"
            data-layout-editor="body"
            style={inspectorBodyStyle}
          >
            {loadError ? (
              <div
                className="layout-editor-error"
                data-layout-status="load-error"
                style={errorStyle}
              >
                {loadError}
              </div>
            ) : null}

            <div style={hintStyle}>
              {groupActive
                ? text("editor.selectedGroup")
                : selected
                  ? text("editor.selectedKind", {
                      name: controlName(selected, text),
                      kind: text(KIND_KEYS[selected.kind]),
                    })
                  : text("editor.selected", {
                      name: text("editor.noneSelected"),
                    })}
            </div>
            <div style={metaStyle}>
              {text("layout.currentPreset", {
                name: activePreset
                  ? `${activePreset.name}${
                      activePreset.builtin
                        ? text("common.builtinSuffix")
                        : text("common.customSuffix")
                    }`
                  : text("layout.unmatched"),
              })}
            </div>

            <div style={nameFieldWrapStyle}>
              <TextField
                label={text("editor.presetName")}
                value={presetName}
                onChange={(event) => setPresetName(event.target.value)}
              />
            </div>

            <div style={modeRowStyle}>
              <span>{text("editor.editMode")}</span>
              <Focusable
                flow-children="row"
                role="group"
                aria-label={text("editor.editMode")}
                style={choiceGroupStyle}
              >
                {EDIT_MODE_VALUES.map((mode) => {
                  const label = text(`editor.editMode.${mode}`);
                  return (
                    <DialogButton
                      key={mode}
                      className="layout-editor-choice"
                      data-layout-choice={`mode-${mode}`}
                      {...choiceAria(label, editMode === mode)}
                      style={choiceButtonStyle(editMode === mode)}
                      disabled={saving}
                      onClick={() => changeEditMode(mode)}
                    >
                      {label}
                    </DialogButton>
                  );
                })}
              </Focusable>
            </div>

            <div style={modeRowStyle}>
              <span>{text("editor.dpadMode")}</span>
              <Focusable
                flow-children="row"
                role="group"
                aria-label={text("editor.dpadMode")}
                style={choiceGroupStyle}
              >
                {DPAD_MODE_VALUES.map((mode) => {
                  const label = text(`editor.dpadMode.${mode}`);
                  return (
                    <DialogButton
                      key={mode}
                      className="layout-editor-choice"
                      data-layout-choice={`dpad-${mode}`}
                      {...choiceAria(label, draftMode === mode)}
                      style={choiceButtonStyle(draftMode === mode)}
                      disabled={saving}
                      onClick={() => changeDpadMode(mode)}
                    >
                      {label}
                    </DialogButton>
                  );
                })}
              </Focusable>
            </div>

            {editMode === "group" ? (
              group && groupActive ? (
                <>
                  <Focusable
                    style={{ marginTop: 10 }}
                    onPointerDownCapture={freezeGroupBaseline}
                    onGamepadFocus={freezeGroupBaseline}
                    onGamepadBlur={clearGroupBaseline}
                  >
                    <SliderField
                      label={text("editor.groupSize")}
                      value={Math.min(
                        groupMaxDiameter,
                        Math.round(groupDiameter),
                      )}
                      min={MIN_TOUCH_PX}
                      max={groupMaxDiameter}
                      step={1}
                      showValue
                      valueSuffix=" px"
                      disabled={saving}
                      onChange={applyGroupSize}
                    />
                  </Focusable>
                  <div style={hintStyle}>
                    {text("editor.groupHint", {
                      current: Math.round(groupDiameter),
                      min: MIN_TOUCH_PX,
                    })}
                  </div>
                </>
              ) : (
                <div style={errorStyle}>{groupIssueText}</div>
              )
            ) : null}

            <div style={hintStyle}>{text("editor.dragHintShort")}</div>

            <Focusable flow-children="row" style={buttonRowStyle}>
              <DialogButton
                className="layout-editor-action"
                data-layout-action="delete-control"
                style={{
                  ...actionButtonStyle,
                  opacity: selected && !saving && !groupActive ? 1 : 0.6,
                }}
                disabled={!selected || saving || !draft || groupActive}
                onClick={deleteSelected}
              >
                {text("editor.deleteControl")}
              </DialogButton>
              <DialogButton
                className="layout-editor-action"
                data-layout-action="restore-base"
                style={{
                  ...actionButtonStyle,
                  opacity: draft && restoreBase && !saving ? 1 : 0.6,
                }}
                disabled={saving || !draft || !restoreBase}
                onClick={() => {
                  setError(null);
                  setConfirmingReset(true);
                }}
              >
                {text("editor.restoreBase")}
              </DialogButton>
            </Focusable>

            {confirmingReset ? (
              <Focusable
                flow-children="column"
                preferredFocus
                className="layout-editor-confirm"
                data-layout-status="restore-confirm"
                style={confirmBoxStyle}
              >
                <div style={hintStyle}>
                  {text("editor.restoreConfirm", {
                    name: restoreBase?.name ?? text("editor.customPreset"),
                  })}
                </div>
                <div style={buttonRowFullStyle}>
                  <DialogButton
                    className="layout-editor-action"
                    data-layout-action="restore-confirm"
                    style={{
                      ...choiceButtonStyle(true),
                      opacity: restoreBase && !saving ? 1 : 0.6,
                    }}
                    disabled={saving || !restoreBase}
                    onClick={resetDraftToBase}
                  >
                    {text("editor.restore")}
                  </DialogButton>
                  <DialogButton
                    className="layout-editor-action"
                    data-layout-action="restore-cancel"
                    style={choiceButtonStyle(false)}
                    disabled={saving}
                    onClick={() => setConfirmingReset(false)}
                  >
                    {text("editor.cancel")}
                  </DialogButton>
                </div>
              </Focusable>
            ) : null}

            <Focusable flow-children="row" style={buttonRowStyle}>
              <DialogButton
                className="layout-editor-action"
                data-layout-action="save-as"
                style={{
                  ...actionButtonStyle,
                  opacity: saving || !draft ? 0.6 : 1,
                }}
                disabled={saving || !draft}
                onClick={() => void save(true)}
              >
                {text("editor.saveAs")}
              </DialogButton>
            </Focusable>

            {error ? (
              <div
                className="layout-editor-error"
                data-layout-status="save-error"
                style={errorStyle}
              >
                {error}
              </div>
            ) : null}
          </div>

          <Focusable
            flow-children="row"
            className="layout-editor-footer"
            data-layout-editor="footer"
            style={inspectorFooterStyle}
          >
            <DialogButton
              className="layout-editor-footer-primary"
              data-layout-action="save"
              style={{
                ...footerPrimaryStyle,
                opacity: saving || !draft ? 0.6 : 1,
              }}
              disabled={saving || !draft}
              onClick={() => void save(false)}
            >
              {saving ? text("editor.saving") : text("editor.save")}
            </DialogButton>
            <DialogButton
              className="layout-editor-footer-cancel"
              data-layout-action="cancel"
              style={footerButtonStyle}
              disabled={saving}
              onClick={closeEditor}
            >
              {text("editor.cancel")}
            </DialogButton>
          </Focusable>
        </Focusable>
      )}
    </Focusable>
  );
}

/**
 * Fullscreen editor host. The outer `display:contents` div mounts in the
 * correct Steam MainWindow, then its owner document's body becomes the portal
 * target so the editor's fixed positioning escapes any transformed ancestor
 * and its zIndex 100000 actually paints above Steam chrome. The inner editor
 * must not mount until the body is known, so its first measurement layout
 * effect already observes the portaled root.
 */
export default function FullscreenLayoutEditor() {
  const [host, setHost] = useState<HTMLElement | null>(null);

  const hostRef = useCallback((node: HTMLDivElement | null) => {
    if (!node) {
      return;
    }
    const body = node.ownerDocument?.body;
    if (body) {
      setHost(body);
    }
  }, []);

  return (
    <div style={{ display: "contents" }} ref={hostRef}>
      {host ? createPortal(<LayoutEditor />, host) : null}
    </div>
  );
}
