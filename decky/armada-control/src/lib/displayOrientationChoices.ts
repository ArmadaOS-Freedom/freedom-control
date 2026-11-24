import type { DisplayOrientation, DisplayOrientationSetting, DisplayOrientationState } from "../types";

const FIXED: DisplayOrientation[] = ["normal", "left", "right", "upsidedown"];

// The choices this device offers: "auto" (follow the accelerometer) only where
// the helper reports it, and kept while stored so it can still be changed.
export function orientationChoices(state: DisplayOrientationState): DisplayOrientationSetting[] {
  return state.autoSupported || state.orientation === "auto" ? [...FIXED, "auto"] : FIXED;
}

// The service applies "auto", and leaving it, in the running session; fixed
// orientations only take effect when Game Mode restarts.
export function appliesLive(previous: DisplayOrientationSetting | "", next: DisplayOrientationSetting | ""): boolean {
  return previous === "auto" || next === "auto";
}
