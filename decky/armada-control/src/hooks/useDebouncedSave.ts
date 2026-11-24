import { useEffect, useRef } from "react";
import type { MutableRefObject, Dispatch, SetStateAction } from "react";
import type { Config } from "../types";

type SaveField = "power" | "tweaks";

interface DebouncedSaveOptions<Field extends SaveField> {
  config: Config | null;
  field: Field;
  snapshot: MutableRefObject<string>;
  save: (value: Config[Field]) => Promise<Config>;
  setConfig: Dispatch<SetStateAction<Config | null>>;
  paused?: boolean;
  onError?: (error: unknown) => void;
  delay?: number;
}

export function useDebouncedSave<Field extends SaveField>(options: DebouncedSaveOptions<Field>) {
  const { config, field, snapshot, save, setConfig, paused, onError, delay = 900 } = options;
  const value = config?.[field];
  const timer = useRef<number | undefined>(undefined);
  const pendingSave = useRef(Promise.resolve<unknown>(undefined));
  const snapshotConfirmed = useRef(true);
  const persist = (data: Config[Field]) => {
    const serialized = JSON.stringify(data);
    const request = pendingSave.current.catch(() => {}).then(async () => {
      if (snapshotConfirmed.current && serialized === snapshot.current) return data;
      snapshotConfirmed.current = false;
      const next = await save(data);
      const saved = next[field];
      snapshot.current = JSON.stringify(saved);
      snapshotConfirmed.current = true;
      setConfig((stored) => {
        if (!stored) return next;
        if (JSON.stringify(stored[field]) !== serialized) return stored;
        return { ...stored, [field]: saved };
      });
      return saved;
    });
    pendingSave.current = request;
    return request;
  };
  useEffect(() => {
    if (!value || !snapshot.current || paused) return;
    const scheduled = window.setTimeout(() => {
      void persist(value).catch((error) => onError?.(error));
    }, delay);
    timer.current = scheduled;
    return () => window.clearTimeout(scheduled);
  }, [value, paused]);
  return (data: Config[Field]) => {
    window.clearTimeout(timer.current);
    return persist(data);
  };
}
