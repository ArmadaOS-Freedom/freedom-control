import { toaster } from "@decky/api";
import { ButtonItem, DialogBody, DialogButton, DialogFooter, ModalRoot, showModal } from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import { getDisplayOrientation, restartGameMode, setDisplayOrientation } from "../backend";
import { t } from "../i18n";
import type { TranslationKey } from "../i18n";
import type { DisplayOrientation, DisplayOrientationState } from "../types";
import { SelectEdit, ToggleRow } from "./widgets";

// Panel axes in presentation order; "normal" is the panel's own orientation.
const ORIENTATION_LABELS: Record<DisplayOrientation, TranslationKey> = {
  normal: "settings.gameModeOrientationNormal",
  left: "settings.gameModeOrientationLeft",
  right: "settings.gameModeOrientationRight",
  upsidedown: "settings.gameModeOrientationUpsidedown",
};

const ORIENTATIONS = Object.keys(ORIENTATION_LABELS) as DisplayOrientation[];

function ConfirmOrientationApplyModal({ closeModal, onApply }: { closeModal?: () => void; onApply: () => void }) {
  const apply = () => {
    closeModal?.();
    onApply();
  };
  return (
    <ModalRoot onCancel={closeModal}>
      <DialogBody>
        {t("settings.gameModeOrientationApplyPrompt")}
      </DialogBody>
      <DialogFooter>
        <DialogButton onClick={apply}>{t("settings.gameModeOrientationApplyConfirm")}</DialogButton>
        <DialogButton onClick={closeModal}>{t("common.cancel")}</DialogButton>
      </DialogFooter>
    </ModalRoot>
  );
}

export function GameModeOrientationSetting() {
  const [state, setState] = useState<DisplayOrientationState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [restarting, setRestarting] = useState(false);
  // A render state alone can't stop two clicks in one tick, so the guard is a ref.
  const busy = useRef(false);
  const restartBusy = useRef(false);

  useEffect(() => {
    let cancelled = false;
    getDisplayOrientation()
      .then((next) => { if (!cancelled) setState(next); })
      .catch((error) => {
        // No state means no supported/saved claim; the controls stay disabled.
        if (!cancelled) toaster.toast({ title: t("settings.gameModeOrientationLoadError"), body: String(error) });
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const apply = async (orientation: DisplayOrientation | "") => {
    if (busy.current || restartBusy.current || !state) return;
    busy.current = true;
    setSaving(true);
    const previous = state.orientation;
    try {
      const next = await setDisplayOrientation(orientation);
      setState(next);
      // The value is stored for the next Game Mode session, never applied live.
      if (next.orientation !== previous) {
        toaster.toast({
          title: t("settings.gameModeOrientation"),
          body: t("settings.gameModeOrientationRestart"),
        });
      }
    } catch (error) {
      toaster.toast({ title: t("settings.gameModeOrientationError"), body: String(error) });
      try {
        setState(await getDisplayOrientation());
      } catch (refreshError) {
        console.warn("[armada-control] display orientation state refresh failed", refreshError);
      }
    } finally {
      busy.current = false;
      setSaving(false);
    }
  };

  // Restarting the whole Game Mode session is the only thing that re-reads the
  // stored orientation; a Steam client restart is not enough. Which services the
  // helper bounces (for example the gamescope user session) is its own concern.
  const restartForOrientation = async () => {
    if (restartBusy.current || busy.current) return;
    restartBusy.current = true;
    setRestarting(true);
    try {
      const restarted = await restartGameMode();
      // Nothing is claimed as applied here: the session only picks it up on restart.
      if (!restarted) {
        toaster.toast({
          title: t("settings.gameModeOrientationApplyError"),
          body: t("settings.gameModeOrientationApplyFailed"),
        });
      }
    } catch (error) {
      toaster.toast({ title: t("settings.gameModeOrientationApplyError"), body: String(error) });
    } finally {
      restartBusy.current = false;
      setRestarting(false);
    }
  };

  const confirmApply = () => {
    if (restartBusy.current || busy.current) return;
    showModal(<ConfirmOrientationApplyModal onApply={() => { void restartForOrientation(); }} />);
  };

  const orientation = state?.orientation ?? "";
  // Unsupported only blocks turning it on; a stored orientation can still be cleared.
  const blocked = !!state && !state.supported && orientation === "";
  // Turning the override on starts from the stored value, else the panel default.
  const pending = orientation === "" ? (state?.defaultOrientation ?? "normal") : orientation;
  const controlsDisabled = loading || saving || restarting || !state;
  const applyDisabled = controlsDisabled;
  return (
    <>
      <ToggleRow
        label={t("settings.gameModeOrientation")}
        value={orientation !== ""}
        disabled={controlsDisabled || blocked}
        onChange={(enabled) => { void apply(enabled ? pending : ""); }}
      />
      {state && orientation !== "" && (
        <SelectEdit
          label={t("settings.gameModeOrientationDirection")}
          value={orientation}
          options={ORIENTATIONS.map((value) => ({ data: value, label: t(ORIENTATION_LABELS[value]) }))}
          disabled={controlsDisabled || !state.supported}
          onChange={(value: DisplayOrientation) => { if (value !== orientation) void apply(value); }}
        />
      )}
      {state && (
        <ButtonItem layout="below" disabled={applyDisabled} onClick={confirmApply}>
          {t("settings.gameModeOrientationApply")}
        </ButtonItem>
      )}
    </>
  );
}
