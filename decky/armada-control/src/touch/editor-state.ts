import type { Control } from "./types";

/** Minimum physical touch target used by the editor resize handle (~44 px). */
export const MIN_TOUCH_PX = 44;

// Shared with the plugin heartbeat loop: while any mounted editor sets this
// flag, the heartbeat must omit the app id so the daemon releases capture.
let layoutEditing = false;

export const beginLayoutEditing = (): void => {
  layoutEditing = true;
};

export const endLayoutEditing = (): void => {
  layoutEditing = false;
};

export const isLayoutEditing = (): boolean => layoutEditing;

/**
 * Mirror of the backend's effective_layout: scale each rectangle around its
 * own center, shrink an oversized rectangle proportionally, then shift it back
 * inside the unit screen without resizing. Returns copies; the input is never
 * mutated.
 */
export const effectiveLayout = (
  layout: readonly Control[],
  scale: number,
): Control[] =>
  layout.map((rect) => {
    const centerX = rect.x + rect.w / 2;
    const centerY = rect.y + rect.h / 2;
    let width = rect.w * scale;
    let height = rect.h * scale;
    let x = centerX - width / 2;
    let y = centerY - height / 2;

    const longest = Math.max(width, height);
    if (longest > 1) {
      const factor = 1 / longest;
      width *= factor;
      height *= factor;
      x = centerX - width / 2;
      y = centerY - height / 2;
    }

    if (x < 0) {
      x = 0;
    } else if (x + width > 1) {
      x = 1 - width;
    }

    if (y < 0) {
      y = 0;
    } else if (y + height > 1) {
      y = 1 - height;
    }

    return { ...rect, x, y, w: width, h: height };
  });

/** Physical pixel size of the editor canvas the draft is rendered into. */
export interface ViewportSize {
  width: number;
  height: number;
}

/** Face-button outputs of the right-hand cluster, in reporting order. */
export const RIGHT_FACE_OUTPUTS = ["a", "b", "x", "y"] as const;

/**
 * Force every stick control into a physical-pixel square.
 *
 * For each stick, the diameter is D = min(rect.w * W, W, H); then w = D / W
 * and h = D / H. The old center is preserved and the result is clamped inside
 * the normalized viewport. Because the width is canonical, an old default
 * pair (w = 0.17, h = 0.30 and w = 0.17, h = 0.28) normalizes to the same D
 * on a given viewport. Non-stick controls, their order, and the input array are
 * untouched. A 1x1 (unknown) viewport or any non-finite control size returns
 * unchanged copies.
 */
export const normalizeCircularSticks = (
  layout: readonly Control[],
  viewport: ViewportSize,
): Control[] => {
  const W = viewport.width;
  const H = viewport.height;
  const unknown = !(W > 1 && H > 1) || !Number.isFinite(W) || !Number.isFinite(H);
  return layout.map((control) => {
    if (control.kind !== "stick" || unknown) {
      return { ...control };
    }
    const rectW = control.w * W;
    if (!Number.isFinite(rectW) || rectW <= 0) {
      return { ...control };
    }
    const D = Math.min(rectW, W, H);
    if (!Number.isFinite(D) || D <= 0) {
      return { ...control };
    }
    let w = D / W;
    let h = D / H;
    if (w > 1) w = 1;
    if (h > 1) h = 1;
    const centerX = control.x + control.w / 2;
    const centerY = control.y + control.h / 2;
    let x = centerX - w / 2;
    let y = centerY - h / 2;
    if (x < 0) {
      x = 0;
    } else if (x + w > 1) {
      x = 1 - w;
    }
    if (y < 0) {
      y = 0;
    } else if (y + h > 1) {
      y = 1 - h;
    }
    return { ...control, x, y, w, h };
  });
};

/**
 * Circle-preserving drag resize for a single stick.
 *
 * The anchor is the frozen start rectangle's x/y (unchanged by the caller
 * across the whole gesture). D0 = start.w * W; the dominant pointer axis
 * supplies the signed delta; D is clamped to [min(44, available), available]
 * where available = min((1 - x) * W, (1 - y) * H). Final w = D / W and
 * h = D / H, so the rendered ellipse always has equal physical rx/ry and can
 * grow or shrink from any handle pull direction. Non-stick controls fall
 * through to the existing free resize (caller's responsibility).
 */
export const resizeCircularStick = (
  control: Control,
  viewport: ViewportSize,
  dxPx: number,
  dyPx: number,
): Control => {
  const W = viewport.width;
  const H = viewport.height;
  if (!(W > 0 && H > 0)) {
    return { ...control };
  }
  const x = control.x;
  const y = control.y;
  const D0 = control.w * W;
  const available = Math.min((1 - x) * W, (1 - y) * H);
  if (!Number.isFinite(available) || available <= 0) {
    return { ...control };
  }
  const floor = Math.min(MIN_TOUCH_PX, available);
  const delta =
    Math.abs(dxPx) >= Math.abs(dyPx) ? dxPx : dyPx;
  const raw = D0 + (Number.isFinite(delta) ? delta : 0);
  const D = clamp(raw, floor, available);
  const w = D / W;
  const h = D / H;
  return { ...control, x, y, w, h };
};

export type FaceGroupStatus = "ok" | "missing" | "duplicate";

/** Result of locating the right-hand face cluster inside a draft layout. */
export interface FaceGroupLookup {
  status: FaceGroupStatus;
  /** Member ids, one per output in RIGHT_FACE_OUTPUTS order. */
  ids: string[];
  /** Member controls matching ids; first occurrence wins on duplicates. */
  controls: Control[];
  /** Outputs that are absent from the draft. */
  missing: string[];
  /** Outputs that appear more than once. */
  duplicates: string[];
}

const clamp = (value: number, min: number, max: number): number =>
  Math.min(max, Math.max(min, value));

/**
 * Locate the right-hand face cluster using kind and output only. Labels, ids
 * and coordinates are ignored: a, b, x and y must each appear exactly once
 * among button controls. Anything else disables grouping instead of guessing,
 * so a deleted key is never silently recreated.
 */
export const findRightFaceGroup = (
  layout: readonly Control[],
): FaceGroupLookup => {
  const buckets = new Map<string, Control[]>();
  for (const output of RIGHT_FACE_OUTPUTS) {
    buckets.set(output, []);
  }

  for (const control of layout) {
    if (control.kind !== "button") {
      continue;
    }
    buckets.get(control.output)?.push(control);
  }

  const missing: string[] = [];
  const duplicates: string[] = [];
  const controls: Control[] = [];

  for (const output of RIGHT_FACE_OUTPUTS) {
    const matches = buckets.get(output) ?? [];
    if (matches.length === 0) {
      missing.push(output);
      continue;
    }
    if (matches.length > 1) {
      duplicates.push(output);
    }
    controls.push(matches[0]);
  }

  const status: FaceGroupStatus =
    missing.length > 0 ? "missing" : duplicates.length > 0 ? "duplicate" : "ok";

  return {
    status,
    ids: controls.map((control) => control.id),
    controls,
    missing,
    duplicates,
  };
};

export interface FaceGroupBounds {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Axis-aligned union of the supplied rectangles in normalized coordinates. */
export const faceGroupBounds = (
  controls: readonly Control[],
): FaceGroupBounds => {
  if (controls.length === 0) {
    return { x: 0, y: 0, w: 0, h: 0 };
  }

  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;

  for (const control of controls) {
    minX = Math.min(minX, control.x);
    minY = Math.min(minY, control.y);
    maxX = Math.max(maxX, control.x + control.w);
    maxY = Math.max(maxY, control.y + control.h);
  }

  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
};

/**
 * Largest physical extent of the members. Used as the stable reference size
 * for the group slider so a drag maps one requested diameter to one scale
 * factor instead of compounding per pointer sample.
 */
export const faceGroupDiameterPx = (
  controls: readonly Control[],
  viewport: ViewportSize,
): number => {
  let diameter = 0;
  for (const control of controls) {
    diameter = Math.max(
      diameter,
      control.w * viewport.width,
      control.h * viewport.height,
    );
  }
  return diameter;
};

export interface FaceGroupResizeResult {
  layout: Control[];
  /** Diameter actually applied after the 44 px floor and the screen limit. */
  diameterPx: number;
  /** Single ratio applied to both the member offsets and the member sizes. */
  factor: number;
}

/**
 * Resize the four face buttons to one equal physical-pixel diameter.
 *
 * The diameter and the pattern share one factor: `factor = appliedDiameter /
 * baseDiameter`. Every member keeps its center offset from the frozen group
 * center scaled by that factor, and every member is sized to the same applied
 * diameter, so the cluster is a uniform scale in physical pixels. Distinct
 * centers therefore stay distinct and shrinking the pattern cannot introduce
 * an overlap that the size does not already have.
 *
 * The requested diameter is capped at the largest diameter whose scaled
 * pattern still fits the canvas,
 * `min(w / (1 + spanX / baseD), h / (1 + spanY / baseD))`, rather than
 * shrinking the factor independently while keeping an oversized diameter
 * (which used to collapse all four centers onto one point). The 44 px floor is
 * applied to the feasible maximum, and one translation then pulls the whole
 * cluster inside the canvas without resizing it.
 */
export const resizeFaceGroup = (
  layout: readonly Control[],
  ids: readonly string[],
  targetDiameterPx: number,
  viewport: ViewportSize,
): FaceGroupResizeResult => {
  const idSet = new Set(ids);
  const members = layout.filter((control) => idSet.has(control.id));

  if (
    members.length === 0 ||
    viewport.width <= 0 ||
    viewport.height <= 0 ||
    !Number.isFinite(targetDiameterPx)
  ) {
    return { layout: layout.slice(), diameterPx: 0, factor: 1 };
  }

  const bounds = faceGroupBounds(members);
  const centerX = bounds.x + bounds.w / 2;
  const centerY = bounds.y + bounds.h / 2;

  let minOffsetX = Number.POSITIVE_INFINITY;
  let minOffsetY = Number.POSITIVE_INFINITY;
  let maxOffsetX = Number.NEGATIVE_INFINITY;
  let maxOffsetY = Number.NEGATIVE_INFINITY;

  for (const member of members) {
    const offsetX = (member.x + member.w / 2 - centerX) * viewport.width;
    const offsetY = (member.y + member.h / 2 - centerY) * viewport.height;
    minOffsetX = Math.min(minOffsetX, offsetX);
    minOffsetY = Math.min(minOffsetY, offsetY);
    maxOffsetX = Math.max(maxOffsetX, offsetX);
    maxOffsetY = Math.max(maxOffsetY, offsetY);
  }

  const spanX = maxOffsetX - minOffsetX;
  const spanY = maxOffsetY - minOffsetY;
  const baseDiameter = Math.max(faceGroupDiameterPx(members, viewport), 1);

  // Largest diameter D whose uniformly scaled pattern still fits:
  // span * (D / baseDiameter) + D <= viewport side.
  const fitX =
    viewport.width / (1 + Math.max(0, spanX) / baseDiameter);
  const fitY =
    viewport.height / (1 + Math.max(0, spanY) / baseDiameter);
  const shortSide = Math.min(viewport.width, viewport.height);
  let maxDiameter = Math.min(fitX, fitY, shortSide);
  if (!Number.isFinite(maxDiameter) || maxDiameter <= 0) {
    maxDiameter = shortSide;
  }

  const diameter = clamp(
    targetDiameterPx,
    Math.min(MIN_TOUCH_PX, maxDiameter),
    maxDiameter,
  );

  // One factor for the pattern and the size: never shrink the centers further
  // than the diameter, which would collapse the cluster instead of fitting it.
  const factor = diameter / baseDiameter;
  const width = diameter / viewport.width;
  const height = diameter / viewport.height;

  const resized = layout.map((control) => {
    if (!idSet.has(control.id)) {
      return control;
    }
    const memberCenterX =
      centerX + (control.x + control.w / 2 - centerX) * factor;
    const memberCenterY =
      centerY + (control.y + control.h / 2 - centerY) * factor;
    return {
      ...control,
      x: memberCenterX - width / 2,
      y: memberCenterY - height / 2,
      w: width,
      h: height,
    };
  });

  const resizedBounds = faceGroupBounds(
    resized.filter((control) => idSet.has(control.id)),
  );
  let shiftX = 0;
  let shiftY = 0;

  if (resizedBounds.x < 0) {
    shiftX = -resizedBounds.x;
  } else if (resizedBounds.x + resizedBounds.w > 1) {
    shiftX = 1 - (resizedBounds.x + resizedBounds.w);
  }

  if (resizedBounds.y < 0) {
    shiftY = -resizedBounds.y;
  } else if (resizedBounds.y + resizedBounds.h > 1) {
    shiftY = 1 - (resizedBounds.y + resizedBounds.h);
  }

  if (shiftX === 0 && shiftY === 0) {
    return { layout: resized, diameterPx: diameter, factor };
  }

  return {
    layout: resized.map((control) =>
      idSet.has(control.id)
        ? { ...control, x: control.x + shiftX, y: control.y + shiftY }
        : control,
    ),
    diameterPx: diameter,
    factor,
  };
};

/**
 * Move the whole cluster by one delta. The delta is clamped once against the
 * group bounds so the members keep their relative geometry and never leave the
 * canvas independently; non-members and the input array are untouched.
 */
export const moveFaceGroup = (
  layout: readonly Control[],
  ids: readonly string[],
  deltaX: number,
  deltaY: number,
): Control[] => {
  const idSet = new Set(ids);
  const members = layout.filter((control) => idSet.has(control.id));

  if (members.length === 0) {
    return layout.slice();
  }

  const bounds = faceGroupBounds(members);
  const appliedX = Number.isFinite(deltaX)
    ? clamp(deltaX, -bounds.x, 1 - (bounds.x + bounds.w))
    : 0;
  const appliedY = Number.isFinite(deltaY)
    ? clamp(deltaY, -bounds.y, 1 - (bounds.y + bounds.h))
    : 0;

  return layout.map((control) =>
    idSet.has(control.id)
      ? { ...control, x: control.x + appliedX, y: control.y + appliedY }
      : control,
  );
};
