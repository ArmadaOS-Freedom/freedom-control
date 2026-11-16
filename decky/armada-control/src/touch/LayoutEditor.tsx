import { Navigation, TextField } from "@decky/ui";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { useLocale } from "../hooks/useLocale";
import { fetchState, savePreset, sendHeartbeat } from "./api";
import {
  beginLayoutEditing,
  effectiveLayout,
  endLayoutEditing,
  MIN_TOUCH_PX,
} from "./editor-state";
import type { TranslationKey } from "./i18n";
import { touchLanguage, touchText } from "./touch_i18n";
import type { Control, State } from "./types";

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
  const key = BUTTON_KEYS[control.output];
  if (key) {
    return text(key);
  }
  return control.label?.trim() || text("editor.control.button");
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
  touchAction: "none",
  userSelect: "none",
  zIndex: 100000,
};

const svgStyle: CSSProperties = {
  display: "block",
  width: "100%",
  height: "100%",
  touchAction: "none",
};

const toolbarStyle: CSSProperties = {
  position: "absolute",
  width: "min(560px, calc(100vw - 24px))",
  maxHeight: "calc(100vh - 24px)",
  overflowY: "auto",
  boxSizing: "border-box",
  padding: "10px 12px",
  borderRadius: 14,
  border: "1px solid rgba(255,255,255,0.18)",
  background: "rgba(14,20,28,0.96)",
  boxShadow: "0 12px 40px rgba(0,0,0,0.55)",
  color: "#f4f7fb",
  zIndex: 2,
  touchAction: "manipulation",
};

const toolbarTitleStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  fontSize: 17,
  fontWeight: 700,
  lineHeight: 1.3,
};

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

const buttonRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 8,
  marginTop: 10,
};

const actionButtonStyle: CSSProperties = {
  minHeight: 44,
  padding: "0 16px",
  borderRadius: 10,
  border: "1px solid rgba(255,255,255,0.28)",
  background: "#243244",
  color: "#ffffff",
  fontSize: 15,
  fontWeight: 600,
  cursor: "pointer",
  touchAction: "manipulation",
};

const primaryButtonStyle: CSSProperties = {
  ...actionButtonStyle,
  borderColor: "#3d7bfd",
  background: "#3d7bfd",
};

const quietButtonStyle: CSSProperties = {
  ...actionButtonStyle,
  background: "transparent",
};

const compactButtonStyle: CSSProperties = {
  minHeight: 36,
  padding: "0 12px",
  borderRadius: 8,
  border: "1px solid rgba(255,255,255,0.28)",
  background: "transparent",
  color: "#ffffff",
  fontSize: 13,
  cursor: "pointer",
  touchAction: "manipulation",
};

type ToolbarPosition = "top" | "center" | "bottom";

const toolbarPositionStyle: Record<ToolbarPosition, CSSProperties> = {
  top: { top: 12, bottom: "auto", left: "50%", transform: "translateX(-50%)" },
  center: {
    top: "50%",
    bottom: "auto",
    left: "50%",
    transform: "translate(-50%, -50%)",
  },
  bottom: {
    top: "auto",
    bottom: 12,
    left: "50%",
    transform: "translateX(-50%)",
  },
};

const nextToolbarPosition: Record<ToolbarPosition, ToolbarPosition> = {
  center: "top",
  top: "bottom",
  bottom: "center",
};

const MOVE_KEYS: Record<ToolbarPosition, TranslationKey> = {
  center: "editor.moveTop",
  top: "editor.moveBottom",
  bottom: "editor.moveCenter",
};

export default function LayoutEditor() {
  const controllerLocale = useLocale();
  const locale = touchLanguage(controllerLocale);
  const text = useCallback<TextFn>(
    (key, variables) => touchText(locale, key, variables),
    [locale],
  );

  const rootRef = useRef<HTMLDivElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const resizeRef = useRef<ResizeState | null>(null);
  const busyRef = useRef(false);
  // Set by this instance's own lifecycle effect, so an async save can tell
  // whether the editor it started in is still the one on screen.
  const mountedRef = useRef(false);

  const [viewport, setViewport] = useState<Viewport>({ width: 1, height: 1 });
  const [state, setState] = useState<State | null>(null);
  const [draft, setDraft] = useState<Control[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [presetName, setPresetName] = useState(() => text("editor.customPreset"));
  const [collapsed, setCollapsed] = useState(false);
  const [toolbarPosition, setToolbarPosition] =
    useState<ToolbarPosition>("center");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

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
        setDraft(effectiveLayout(next.settings.layout, next.settings.scale));
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
    const root = rootRef.current;
    if (!root) {
      return;
    }

    const measure = () => {
      const rect = root.getBoundingClientRect();
      setViewport({
        width: Math.max(1, rect.width),
        height: Math.max(1, rect.height),
      });
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
    setSelectedId(control.id);

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
    };
  };

  const startResize = (event: ReactPointerEvent<SVGGElement>) => {
    if (
      !event.isPrimary ||
      busyRef.current ||
      !selected ||
      dragRef.current ||
      resizeRef.current
    ) {
      return;
    }
    event.stopPropagation();
    event.preventDefault();

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
    }
    const resize = resizeRef.current;
    if (resize && resize.pointerId === event.pointerId) {
      resizeRef.current = null;
      releaseCapture(resize.capture, resize.pointerId);
    }
  };

  const closeEditor = () => {
    endLayoutEditing();
    Navigation.NavigateBack();
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
      const next = await savePreset(name, draft, 1, overwriteId);
      // The backend already accepted the save, so it must finish — but this
      // instance may have been left behind for a newer page or editor. It may
      // not touch that page's history or its layout-editing flag.
      if (!mountedRef.current) {
        return;
      }
      setState(next);
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
            strokeDasharray="8 6"
          />
        ) : (
          <rect
            x={x}
            y={y}
            width={w}
            height={h}
            rx={Math.min(w, h) * 0.18}
            fill={color}
            fillOpacity={0.24}
            stroke={color}
            strokeWidth={2}
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
            fill="#ffffff"
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
    <div ref={rootRef} style={rootStyle}>
      <svg
        ref={svgRef}
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
              strokeDasharray="8 5"
              pointerEvents="none"
            />
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
          </>
        ) : null}
      </svg>

      <div
        style={{
          ...toolbarStyle,
          ...toolbarPositionStyle[toolbarPosition],
          ...(collapsed
            ? {
                width: "auto",
                maxWidth: "calc(100vw - 24px)",
                padding: "8px 10px",
              }
            : null),
        }}
      >
        {collapsed ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            <button
              type="button"
              style={actionButtonStyle}
              onClick={() =>
                setToolbarPosition(nextToolbarPosition[toolbarPosition])
              }
            >
              {text(MOVE_KEYS[toolbarPosition])}
            </button>
            <button
              type="button"
              style={compactButtonStyle}
              onClick={() => setCollapsed(false)}
            >
              {text("editor.expand")}
            </button>
          </div>
        ) : (
          <>
            <div style={toolbarTitleStyle}>
              <span>{text("editor.title")}</span>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <button
                  type="button"
                  style={actionButtonStyle}
                  onClick={() =>
                    setToolbarPosition(nextToolbarPosition[toolbarPosition])
                  }
                >
                  {text(MOVE_KEYS[toolbarPosition])}
                </button>
                <button
                  type="button"
                  style={compactButtonStyle}
                  onClick={() => setCollapsed(true)}
                >
                  {text("editor.collapse")}
                </button>
              </div>
            </div>

            {loadError ? (
              <div style={{ ...errorStyle, marginTop: 8 }}>{loadError}</div>
            ) : null}

            <div style={{ ...hintStyle, marginTop: 8 }}>
              {selected
                ? text("editor.selectedKind", {
                    name: controlName(selected, text),
                    kind: text(KIND_KEYS[selected.kind]),
                  })
                : text("editor.selected", { name: text("editor.noneSelected") })}
            </div>
            <div style={hintStyle}>
              {text("layout.currentPreset", {
                name: activePreset
                  ? `${activePreset.name}${
                      activePreset.builtin
                        ? text("common.builtinSuffix")
                        : text("common.customSuffix")
                    }`
                  : text("layout.unmatched"),
              })}
              {state
                ? text("editor.scaleNote", {
                    scale: state.settings.scale.toFixed(2),
                  })
                : ""}
            </div>
            {activePreset?.builtin ? (
              <div style={hintStyle}>{text("editor.builtinNote")}</div>
            ) : null}

            <div style={{ marginTop: 8, userSelect: "text" }}>
              <TextField
                label={text("editor.presetName")}
                value={presetName}
                onChange={(event) => setPresetName(event.target.value)}
              />
            </div>

            <div style={{ ...hintStyle, marginTop: 4 }}>
              {text("editor.dragHint")}
            </div>

            <div style={buttonRowStyle}>
              <button
                type="button"
                style={{
                  ...primaryButtonStyle,
                  opacity: saving || !draft ? 0.6 : 1,
                }}
                disabled={saving || !draft}
                onClick={() => void save(false)}
              >
                {saving ? text("editor.saving") : text("editor.save")}
              </button>
              <button
                type="button"
                style={{
                  ...actionButtonStyle,
                  opacity: saving || !draft ? 0.6 : 1,
                }}
                disabled={saving || !draft}
                onClick={() => void save(true)}
              >
                {text("editor.saveAs")}
              </button>
              <button
                type="button"
                style={quietButtonStyle}
                disabled={saving}
                onClick={closeEditor}
              >
                {text("editor.cancel")}
              </button>
            </div>

            {error ? (
              <div style={{ ...errorStyle, marginTop: 8 }}>{error}</div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
