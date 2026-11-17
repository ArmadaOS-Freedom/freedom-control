export type ControlKind = "button" | "stick" | "dpad" | "pad";

/**
 * A touch target in normalized pad coordinates. x/y are the top-left corner
 * and w/h the size, all in the 0..1 range used by the backend renderer.
 */
export interface Control {
  id: string;
  kind: ControlKind;
  output: string;
  x: number;
  y: number;
  w: number;
  h: number;
  label?: string;
}

export type DpadMode = "four_way" | "eight_way";
export type ButtonColor = "white" | "black";

/** A user-created preset. The backend owns layout validation and ids. */
export interface CustomPreset {
  id: string;
  name: string;
  layout: Control[];
  scale: number;
  dpad_mode: DpadMode;
  base_preset_id: string;
}

/** A catalog entry: builtin templates plus saved custom presets. */
export interface Preset extends CustomPreset {
  builtin: boolean;
}

export interface Settings {
  version: 2;
  enabled: boolean;
  opacity: number;
  scale: number;
  deadzone: number;
  edge_swipes: boolean;
  haptics_enabled: boolean;
  haptics_strength: number;
  active_preset_id: string;
  custom_presets: CustomPreset[];
  layout: Control[];
  dpad_mode: DpadMode;
  button_color: ButtonColor;
  right_stick_fullscreen?: boolean;
}

/** The only fields update_options accepts; every other field is left alone. */
export interface OptionsPatch {
  enabled?: boolean;
  opacity?: number;
  scale?: number;
  deadzone?: number;
  edge_swipes?: boolean;
  haptics_enabled?: boolean;
  haptics_strength?: number;
  dpad_mode?: DpadMode;
  button_color?: ButtonColor;
  right_stick_fullscreen?: boolean;
}

export interface Status {
  state: string;
  detail: string;
  capturing: boolean;
  source_path?: string;
  composite_path?: string;
  device?: string;
  rotation?: number;
  haptics_available?: boolean;
  haptics_detail?: string;
  /** Undefined while the daemon is unavailable or still starting. */
  controls_visible?: boolean;
}

export interface State {
  settings: Settings;
  status: Status;
  presets: Preset[];
}

/** Alias kept for call sites that prefer an explicit plugin-state name. */
export type PluginState = State;
