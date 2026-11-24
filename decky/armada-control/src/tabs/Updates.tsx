import { ButtonItem, DialogBody, DialogButton, DialogFooter, Field, PanelSection, PanelSectionRow, showModal } from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { deleteLocalUpdate, getLocalUpdates, getUpdateProgress, getUpdates, installUpdate, refreshUpdates, restartGameMode, saveTweaks } from "../backend";
import { ArmadaModalRoot } from "../components/ArmadaModalRoot";
import { t } from "../i18n";
import { getProtonTools, setWindowsCompatTool } from "../lib/steamCompat";
import type { Config, LocalUpdate, UpdateItem, UpdateProgress, UpdatesState } from "../types";

function CompatRestartModal({ closeModal, name }: { closeModal?: () => void; name: string }) {
  return (
    <ArmadaModalRoot onCancel={closeModal}>
      <DialogBody>{t("updates.restartForCompatDiscovery", { name })}</DialogBody>
      <DialogFooter className="armada-control-dialog-footer">
        <DialogButton onClick={() => { closeModal?.(); void restartGameMode(); }}>{t("compatibility.restartGameMode")}</DialogButton>
        <span className="armada-control-dialog-divider" />
        <DialogButton onClick={closeModal}>{t("common.later")}</DialogButton>
      </DialogFooter>
    </ArmadaModalRoot>
  );
}

function UpdateText({ label, description }: { label: string; description?: string }) {
  return (
    <div className="armada-update-text">
      <div className="armada-update-label">{label}</div>
      {description ? <div className="armada-update-description">{description}</div> : null}
    </div>
  );
}

function updateSection(title: string, items: UpdateItem[], busy: string, progress: UpdateProgress | null, install: (item: UpdateItem) => void, empty: string) {
  return (
    <PanelSection title={title}>
      {items.length ? items.map((item) => (
        <div key={`${item.kind}:${item.id}`} className="armada-update-entry">
          <PanelSectionRow>
            <UpdateText label={`${item.label.split(" · ").slice(-1)[0]}${item.version ? ` · ${item.version}` : ""}`} description={item.description} />
          </PanelSectionRow>
          {!item.installed ? <PanelSectionRow>
            <div className="armada-update-action">
              <ButtonItem childrenContainerWidth="max" disabled={!!busy || item.installed} onClick={() => install(item)}>
                {busy === item.id ? t(`updates.phase.${progress?.phase || "checking"}`) : item.installed ? t("updates.installedLabel") : t("updates.get")}
              </ButtonItem>
            </div>
          </PanelSectionRow> : null}
          {busy === item.id ? <PanelSectionRow>
            <div className="armada-update-progress">
              <div className="armada-update-progress-track">
                <div className="armada-update-progress-fill" style={{ width: `${Math.max(2, Math.min(100, progress?.percent || 2))}%` }} />
              </div>
              <span>{t(`updates.phase.${progress?.phase || "checking"}`)} · {progress?.percent || 0}%</span>
            </div>
          </PanelSectionRow> : null}
        </div>
      )) : <Field label={empty} />}
    </PanelSection>
  );
}

export function Updates({ config, setConfig }: { config: Config; setConfig: Dispatch<SetStateAction<Config | null>> }) {
  const [state, setState] = useState<UpdatesState | null>(null);
  const [local, setLocal] = useState<LocalUpdate[]>([]);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [progress, setProgress] = useState<UpdateProgress | null>(null);
  const selectionRequest = useRef(0);
  const load = async (refresh = false) => {
    setBusy(refresh ? "refresh" : "load");
    setMessage("");
    try {
      const next = await (refresh ? refreshUpdates() : getUpdates());
      setState(next);
      try {
        setLocal(await getLocalUpdates());
      } catch (error) {
        setMessage(String(error));
      }
    } catch (error) {
      setMessage(String(error));
    } finally {
      setLoading(false);
      setBusy("");
    }
  };
  const selectLocal = async (item: LocalUpdate) => {
    const requestId = ++selectionRequest.current;
    const field = item.kind === "turnip" ? "turnipDriver" : "windowsCompatTool";
    const driverId = item.id.startsWith("user:") ? item.id.slice("user:".length) : item.id;
    const driver = item.kind === "turnip" ? (driverId === "stable" ? undefined : driverId) : undefined;
    const selectionId = item.kind === "compatibility" ? item.selectionId || item.installedId || item.id : driver;
    const next = { ...config.tweaks, global: { ...config.tweaks.global, [field]: selectionId } };
    setMessage("");
    try {
      await saveTweaks(next);
      if (requestId !== selectionRequest.current) return;
      setConfig((current) => current ? { ...current, tweaks: next } : current);
      if (item.kind === "turnip") {
        setMessage(t("updates.driverAppliesNextLaunch", { name: item.label }));
        return;
      }
      setWindowsCompatTool(typeof selectionId === "string" ? selectionId : undefined);
      setMessage(t("updates.selected", { name: item.label }));
      const tools = await getProtonTools(true);
      if (requestId === selectionRequest.current && typeof selectionId === "string" && !tools.some((tool) => tool.id === selectionId)) {
        showModal(<CompatRestartModal name={item.label} />);
      }
    } catch (error) {
      if (requestId === selectionRequest.current) setMessage(String(error));
    }
  };
  const removeLocal = async (item: LocalUpdate) => {
    if (!item.removable) return;
    setBusy(`delete:${item.id}`);
    try {
      const result = await deleteLocalUpdate(item.kind, item.id, item.installedId);
      const field = item.kind === "turnip" ? "turnipDriver" : "windowsCompatTool";
      if (result.selectionCleared) {
        const next = { ...config.tweaks, global: { ...config.tweaks.global } };
        delete next.global[field];
        if (item.kind === "compatibility") setWindowsCompatTool(undefined);
        setConfig((current) => current ? { ...current, tweaks: next } : current);
      }
      await load();
    } catch (error) {
      setMessage(String(error));
      setBusy("");
    }
  };
  useEffect(() => { void load(); }, []);
  useEffect(() => {
    if (!busy || busy === "load" || busy === "refresh" || busy.startsWith("delete:")) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const next = await getUpdateProgress(busy);
        if (!cancelled) setProgress(next);
      } catch {
      }
    };
    void poll();
    const timer = window.setInterval(poll, 500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [busy]);
  const install = async (item: UpdateItem) => {
    setBusy(item.id);
    setProgress({ id: item.id, phase: "checking", percent: 0, downloaded: 0, total: 0 });
    setMessage("");
    try {
      const installed = await installUpdate(item.id);
      setProgress({ id: item.id, phase: "complete", percent: 100, downloaded: 0, total: 0 });
      setMessage(t("updates.installed", { name: item.label }));
      await load();
      if (item.kind === "compatibility") {
        const tools = await getProtonTools(true);
        const installedTool = tools.some((tool) => tool.id === (installed.selectionId || item.selectionId || installed.installedId || item.id));
        if (!installedTool) {
          showModal(<CompatRestartModal name={item.label} />);
        }
      }
    } catch (error) {
      setMessage(String(error));
      setBusy("");
      setProgress(null);
    }
  };
  const selectedDriverId = config.tweaks.global.turnipDriver || "stable";
  const selectedCompatId = config.tweaks.global.windowsCompatTool || "";
  const selectedDriver = local.find((item) => item.kind === "turnip" && item.id.replace(/^user:/, "") === selectedDriverId);
  const selectedCompat = local.find((item) => item.kind === "compatibility" && (item.selectionId || item.installedId || item.id) === selectedCompatId);
  if (!state) return (
    <PanelSection title={t("updates.title")}>
      <Field label={loading ? t("updates.loading") : t("updates.unavailable")} description={message || (loading ? "" : t("updates.checkFailed"))} />
      <PanelSectionRow>
        <ButtonItem layout="below" childrenContainerWidth="max" disabled={!!busy} onClick={() => { void load(true); }}>
          {busy === "refresh" ? t("updates.checking") : t("updates.check")}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
  return (
    <>
      <PanelSection title={t("updates.title")}>
        <Field description={t("updates.description")} />
        <PanelSectionRow>
          <ButtonItem layout="below" childrenContainerWidth="max" disabled={!!busy} onClick={() => { void load(true); }}>
            {busy === "refresh" ? t("updates.checking") : t("updates.check")}
          </ButtonItem>
        </PanelSectionRow>
        {!state.ok ? <Field label={t("updates.unavailable")} description={state.error || t("updates.checkFailed")} /> : null}
        {message ? <Field label={t("common.status")} description={message} /> : null}
      </PanelSection>
      <PanelSection title={t("updates.currentSelection")}>
        <Field label={t("updates.currentDriver")} description={selectedDriver?.label || (selectedDriverId === "stable" ? t("updates.builtInDriver") : selectedDriverId || t("updates.noneSelected"))} />
        <Field label={t("updates.currentCompat")} description={selectedCompat?.label || selectedCompatId || t("updates.noneSelected")} />
      </PanelSection>
      {updateSection(t("updates.onlineCompat"), state.updates.filter((item) => item.kind === "compatibility"), busy, progress, (item) => { void install(item); }, t("updates.noUpdates"))}
      <PanelSection title={t("updates.localCompat")}>
        {local.filter((item) => item.kind === "compatibility").length ? local.filter((item) => item.kind === "compatibility").map((item) => (
          <div key={`compatibility:${item.installedId || item.id}`} className="armada-update-entry">
            <PanelSectionRow>
              <UpdateText label={item.label} description={item.version || (selectedCompatId === (item.selectionId || item.installedId || item.id) ? t("updates.preferred") : "")} />
            </PanelSectionRow>
            <PanelSectionRow>
              <div className="armada-update-actions">
                <div className="armada-update-action"><ButtonItem childrenContainerWidth="max" disabled={!!busy || selectedCompatId === (item.selectionId || item.installedId || item.id)} onClick={() => { void selectLocal(item); }}>{selectedCompatId === (item.selectionId || item.installedId || item.id) ? t("updates.preferred") : t("updates.select")}</ButtonItem></div>
                {item.removable ? <div className="armada-update-action"><ButtonItem childrenContainerWidth="max" disabled={!!busy} onClick={() => { void removeLocal(item); }}>{t("updates.remove")}</ButtonItem></div> : null}
              </div>
            </PanelSectionRow>
          </div>
        )) : <Field label={t("updates.noLocal")} />}
      </PanelSection>
      {updateSection(t("updates.onlineDrivers"), state.updates.filter((item) => item.kind === "turnip"), busy, progress, (item) => { void install(item); }, t("updates.noUpdates"))}
      <PanelSection title={t("updates.localDrivers")}>
        {local.filter((item) => item.kind === "turnip").length ? local.filter((item) => item.kind === "turnip").map((item) => {
          return (
            <div key={`turnip:${item.id}`} className="armada-update-entry">
              <PanelSectionRow>
                <UpdateText label={item.label} description={item.version || (selectedDriverId === item.id ? t("updates.preferred") : "")} />
              </PanelSectionRow>
              <PanelSectionRow>
                <div className="armada-update-actions">
                  <div className="armada-update-action"><ButtonItem childrenContainerWidth="max" disabled={!!busy || selectedDriverId === item.id.replace(/^user:/, "")} onClick={() => { void selectLocal(item); }}>{selectedDriverId === item.id.replace(/^user:/, "") ? t("updates.preferred") : t("updates.select")}</ButtonItem></div>
                  {item.removable ? <div className="armada-update-action"><ButtonItem childrenContainerWidth="max" disabled={!!busy} onClick={() => { void removeLocal(item); }}>{t("updates.remove")}</ButtonItem></div> : null}
                </div>
              </PanelSectionRow>
            </div>
          );
        }) : <Field label={t("updates.noLocal")} />}
      </PanelSection>
    </>
  );
}
