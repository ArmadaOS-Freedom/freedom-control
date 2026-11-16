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
