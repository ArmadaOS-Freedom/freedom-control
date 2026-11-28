import { Field, PanelSection, Tabs } from "@decky/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { getConfig, getInstalledGames, savePowerConfig, saveTweaks } from "./backend";
import { RgbLighting } from "./components/RgbLighting";
import { useDebouncedSave } from "./hooks/useDebouncedSave";
import { useLocale } from "./hooks/useLocale";
import { t } from "./i18n";
import { tabIcons } from "./icons";
import { currentGame } from "./lib/games";
import { setWindowsCompatTool } from "./lib/steamCompat";
import { styles } from "./styles";
import { Compatibility } from "./tabs/Compatibility";
import { Fans } from "./tabs/Fans";
import { Power } from "./tabs/Power";
import { Settings } from "./tabs/Settings";
import { TfLibraryContent } from "./tf/TfLibraryContent";
import type { Locale as TfLocale } from "./tf/i18n";
import { tfText } from "./tf/tf_i18n";
import { TouchContent } from "./touch/TouchContent";
import { Updates } from "./tabs/Updates";
import type { Language as TouchLanguage } from "./touch/i18n";
import type { Config, Tweaks } from "./types";

export function Content() {
  // The TF page was written for en/zh only; the other Controller locales keep
  // their own UI language and read the TF tab in English.
  const locale = useLocale();
  const tfLocale: TfLocale = locale === "zh-CN" ? "zh" : "en";
  const touchLocale: TouchLanguage = locale === "zh-CN" ? "zh" : "en";
  const [tab, setTab] = useState("Compatibility");
  const [config, setConfig] = useState<Config | null>(null);
  const [message, setMessage] = useState("Loading");
  const savedPowerSnapshot = useRef("");
  const savedTweaksSnapshot = useRef("");
  const updateTweaksPending = useRef(false);
  const tweaksRecoveryRequired = useRef(false);
  const [pauseTweaksSave, setPauseTweaksSave] = useState(false);
  const installedGamesRequested = useRef(false);
  const load = useCallback(async () => {
    try {
      const next = await getConfig();
      next.game = currentGame();
      next.selectedGame = next.game || null;
      savedPowerSnapshot.current = JSON.stringify(next.power);
      savedTweaksSnapshot.current = JSON.stringify(next.tweaks);
      setConfig((current) => ({ ...next, installedGames: current?.installedGames || next.installedGames }));
    } catch (error) {
      setMessage(String(error));
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!config || installedGamesRequested.current) return;
    installedGamesRequested.current = true;
    let cancelled = false;
    getInstalledGames()
      .then((installedGames) => {
        if (cancelled) return;
        setConfig((current) => (current ? { ...current, installedGames } : current));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [!!config]);
  useEffect(() => {
    if (!config) return;
    let cancelled = false;
    const refreshRuntime = async () => {
      try {
        const runtimeGame = currentGame();
        if (cancelled) return;
        setConfig((current) => {
          if (!current) return current;
          const currentApp = current.game?.appid || "";
          const nextApp = runtimeGame?.appid || "";
          const currentName = current.game?.name || "";
          const nextName = runtimeGame?.name || "";
          if (currentApp === nextApp && currentName === nextName) return current;
          return { ...current, game: runtimeGame };
        });
      } catch (error) {
      }
    };
    const timer = window.setInterval(refreshRuntime, 2000);
    refreshRuntime();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [!!config]);
  useDebouncedSave({ config, field: "power", snapshot: savedPowerSnapshot, save: savePowerConfig, setConfig, onError: load });
  const persistTweaks = useDebouncedSave({
    config, field: "tweaks", snapshot: savedTweaksSnapshot, save: saveTweaks, setConfig,
    paused: pauseTweaksSave, onError: () => { if (!updateTweaksPending.current) void load(); },
  });
  if (!config) return <PanelSection title="Freedom Control"><Field label={message === "Loading" ? t("common.loading") : message} /></PanelSection>;
  const recoverTweaks = async () => {
    const current = await getConfig();
    savedTweaksSnapshot.current = JSON.stringify(current.tweaks);
    setWindowsCompatTool(current.tweaks.global.windowsCompatTool || undefined);
    setConfig((stored) => stored ? { ...stored, tweaks: current.tweaks, turnipDrivers: current.turnipDrivers } : stored);
    tweaksRecoveryRequired.current = false;
    if (!updateTweaksPending.current) setPauseTweaksSave(false);
    return current.tweaks;
  };
  const flushTweaks = (tweaks: Tweaks) => (
    tweaksRecoveryRequired.current ? recoverTweaks() : persistTweaks(tweaks)
  );
  const withSavedTweaks = async (action: (tweaks: Tweaks) => Promise<void>) => {
    if (updateTweaksPending.current) return;
    updateTweaksPending.current = true;
    setPauseTweaksSave(true);
    try {
      const saved = await flushTweaks(config.tweaks);
      try {
        await action(saved);
      } catch (error) {
        tweaksRecoveryRequired.current = true;
        try {
          await recoverTweaks();
        } catch {
        }
        throw error;
      }
    } finally {
      updateTweaksPending.current = false;
      setPauseTweaksSave(tweaksRecoveryRequired.current);
    }
  };
  const applySavedTweaks = (tweaks: Tweaks) => {
    savedTweaksSnapshot.current = JSON.stringify(tweaks);
    setConfig((current) => current ? { ...current, tweaks } : current);
  };
  const refreshDriverInventory = async () => {
    const { turnipDrivers } = await getConfig();
    setConfig((current) => current ? { ...current, turnipDrivers } : current);
  };
  // The body keeps its automatic height for every page; the Touch page is a
  // normal-flow column that scrolls inside the Controller's own tab panel.
  const tabContent = (content: ReactNode) => (
    <div className="armada-control-tab-content">{content}</div>
  );
  const tfTabLabel = tfText(tfLocale, "tf.title");
  const touchTabLabel = t("touch.title");
  return (
    <div className="armada-control-tabs">
      <style>{styles}</style>
      <Tabs
        activeTab={tab}
        onShowTab={(nextTab: string) => { if (!updateTweaksPending.current) setTab(nextTab); }}
        tabs={[
          { id: "Compatibility", title: tabIcons.Compatibility, content: tabContent(<Compatibility config={config} setConfig={setConfig} flushTweaks={flushTweaks} />) },
          {
            id: "Updates",
            title: (
              <span role="img" aria-label={t("updates.title")} title={t("updates.title")}>
                {tabIcons.Updates}
              </span>
            ),
            content: tabContent(<Updates config={config} applySavedTweaks={applySavedTweaks} withSavedTweaks={withSavedTweaks} refreshDriverInventory={refreshDriverInventory} />),
          },
          { id: "Power", title: tabIcons.Power, content: tabContent(<Power config={config} setConfig={setConfig} />) },
          // Mounted only while its tab is active. The heartbeat and the layout
          // route are owned by the plugin lifecycle, not by this page, so
          // switching away never stops an enabled controller.
          {
            id: "Touch",
            title: (
              <span role="img" aria-label={touchTabLabel} title={touchTabLabel}>
                {tabIcons.Touch}
              </span>
            ),
            content: tabContent(tab === "Touch" ? <TouchContent locale={touchLocale} /> : null),
          },
          ...(config.fanSupported ? [
            { id: "Fans", title: tabIcons.Fans, content: tabContent(<Fans setConfig={setConfig} />) },
          ] : []),
          ...(config.rgbSupported ? [
            { id: "RGB", title: tabIcons.RGB, content: tabContent(<RgbLighting />) },
          ] : []),
          // Mounted only while its tab is active: switching away unmounts the
          // page and stops the card probe/status poll. The systemd conversion
          // job keeps running independently and a reopen resumes its status.
          {
            id: "TF",
            title: (
              <span role="img" aria-label={tfTabLabel} title={tfTabLabel}>
                {tabIcons.TF}
              </span>
            ),
            content: tabContent(tab === "TF" ? <TfLibraryContent locale={tfLocale} /> : null),
          },
          { id: "Advanced", title: tabIcons.Advanced, content: tabContent(<Settings config={config} setConfig={setConfig} />) },
        ]}
      />
    </div>
  );
}
