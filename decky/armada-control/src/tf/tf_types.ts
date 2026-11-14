// Wire shapes returned by the TF-library backend methods. They mirror
// service.TfLibraryService / tf_inspection.TfInspector exactly: every call is
// wrapped in the shared { ok, data } / { ok, error, detail } envelope, defined
// here so this module has no dependency on the Controller's own ./types.

export type Result<T> = { ok: true; data: T } | { ok: false; error: string; detail: string };

export type TfCardState = "pending" | "connected" | "blocked" | "recovery";

export interface TfCard {
  device: string;
  size_bytes: number;
  card_cid: string;
  state: TfCardState;
  // A stable machine code (localised by the UI); `detail` stays technical and
  // is only shown behind the Details toggle.
  reason: string;
  detail: string;
  reserve_device: string | null;
  // Bytes, Android private partition only (the 16 MiB metadata is excluded).
  android_bytes: number | null;
  linux_bytes: number | null;
  mountpoint: string | null;
  library_path: string | null;
}

export interface TfInspection {
  cards: TfCard[];
}

export interface TfPreserveEntry {
  name?: string | null;
  type?: string;
  uuid?: string;
}

export interface TfPreserves {
  android_p2: TfPreserveEntry;
  android_p3: TfPreserveEntry;
}

export interface TfPlan {
  token: string;
  mode: "format" | "mount";
  device: string;
  reserve_device: string;
  card_cid: string;
  android_bytes: number | null;
  linux_bytes: number | null;
  recovery: boolean;
  preserves: TfPreserves;
  erase_scope: "reserve_only_format" | "none";
}

export type TfState = "idle" | "running" | "succeeded" | "failed";

export type TfPhase = "preparing" | "formatting" | "mounting" | "registering";

export interface TfStatus {
  state: TfState;
  // Empty when idle; one of the real phases while running.
  phase: TfPhase | "";
  detail: string;
  log_tail: string;
  library_requested: boolean;
  library_registered: boolean;
  library_pending: boolean;
  // Identity of the card this non-idle operation is bound to, reported from the
  // persisted plan when the backend could prove it. Absent when idle or when
  // the record is missing/malformed; never guessed.
  card_cid?: string | null;
  device?: string | null;
}
