import { definePlugin } from "@decky/api";
import { getCompatApplied, getConfig, getInstalledGames, restoreHomePower, saveCompatApplied, suspendSwipeGestures, syncHomePower } from "./backend";
import { Content } from "./Content";
import {
  configureCompatPolicy,
  defaultWindowsCompatTool,
  getProtonTools,
  handledGameAppids,
  migrateWindowsCompatTool,
  registerDownloadWatcher,
  sweepInstalledGames,
} from "./lib/steamCompat";
import { registerLowPowerDownloadEntry } from "./lib/lowPowerDownloads";
import { factoryDefaultTransition } from "./lib/protonPolicy";
import { installY700FrameRateChoices } from "./lib/y700FrameRateChoices";
import { registerSteamProfileLocalization } from "./lib/steamProfileLocalization";
import { installSystemInfoCpuNameFallback } from "./lib/steamSystemInfo";
import { initializeGlobalResolution } from "./lib/steamSettings";
import { registerTouchController } from "./touch/integration";

// Gamescope's edge swipes toggle the Steam menus, so a drag inside an open
// side menu (a quick access slider moved toward the edge) would close it:
// keep them off while one is open.
function watchSideMenus(): () => void {
  // Unknown until the first poll: a Steam restart with a menu open leaves the
  // gestures suspended in gamescope, so always send the state once.
  let menuOpen: boolean | null = null;
  const timer = window.setInterval(() => {
    const store = (window as any).SteamUIStore?.ActiveWindowInstance?.MenuStore;
    if (!store) return;
    const open = Number(store.m_eOpenSideMenu ?? 0) !== 0;
    if (open === menuOpen) return;
    menuOpen = open;
    suspendSwipeGestures(open).catch(() => {});
  }, 250);
  return () => {
    window.clearInterval(timer);
    if (menuOpen) suspendSwipeGestures(false).catch(() => {});
  };
}

export default definePlugin(() => {
  // Reapply the saved Steam UI preference immediately; the compatibility sweep
  // below depends on installed games and must not delay or gate this.
  syncHomePower().catch((error) => {
    console.warn("[armada-control] home power sync failed", error);
  });
  initializeGlobalResolution().catch((error) => {
    console.warn("[armada-control] global resolution init failed", error);
  });
  let unregisterDownloadWatcher = () => {};
  let disposeFrameRateChoices = () => {};
  let disposeSystemInfo = () => {};
  const unregisterProfileLocalization = registerSteamProfileLocalization();
  const unregisterLowPowerDownloads = registerLowPowerDownloadEntry();
  const stopWatchingSideMenus = watchSideMenus();
  // The touch heartbeat and its layout route live for the whole plugin
  // session, independent of which panel tab is open.
  const disposeTouchController = registerTouchController();
  const persistHandledGames = () => {
    saveCompatApplied(handledGameAppids()).catch(() => {});
  };
  let cancelled = false;
  const handledRequest = getCompatApplied()
    .then((state) => ({ state, loaded: true }))
    .catch(() => ({ state: { appids: [] as string[], protonDefault: "" }, loaded: false }));
  // The CPU name display depends only on the device class, so it installs as
  // soon as the config arrives instead of waiting on the installed-games RPC.
  const configRequest = getConfig();
  configRequest
    .then(async (config) => {
      await (window as any).App.WaitForServicesInitialized();
      if (cancelled) return;
      try {
        disposeSystemInfo = installSystemInfoCpuNameFallback(config.cpuDeviceClass);
        if (cancelled) {
          disposeSystemInfo();
          disposeSystemInfo = () => {};
        }
      } catch (error) {
        console.warn("[armada-control] CPU name display fallback failed to install", error);
      }
    })
    .catch(() => {});
  Promise.all([configRequest, getInstalledGames(), handledRequest])
    .then(async ([config, games, handled]) => {
      await (window as any).App.WaitForServicesInitialized();
      if (cancelled) return;
      try {
        disposeFrameRateChoices = installY700FrameRateChoices(config.deviceId || "");
        if (cancelled) {
          disposeFrameRateChoices();
          disposeFrameRateChoices = () => {};
          return;
        }
      } catch (error) {
        console.warn("[armada-control] Y700 frame-rate filter failed to install", error);
      }
      const explicitTool = config.tweaks?.global?.windowsCompatTool;
      configureCompatPolicy(
        explicitTool,
        handled.loaded && config.tweaks?.global?.autoApplyCompat !== false,
        handled.state.appids,
        config.protonDefaults,
      );
      const persist = handled.loaded ? persistHandledGames : () => {};
      unregisterDownloadWatcher = registerDownloadWatcher(persist);
      window.setTimeout(() => {
        void (async () => {
          if (cancelled) return;
          await sweepInstalledGames(games);
          if (cancelled) return;
          if (handled.loaded && !explicitTool) {
            const tools = await getProtonTools();
            if (cancelled) return;
            const nextTool = defaultWindowsCompatTool(tools, config.protonDefaults);
            const transition = factoryDefaultTransition(
              explicitTool, handled.loaded, handled.state.protonDefault, nextTool,
            );
            if (transition) {
              try {
                if (transition.oldTool !== transition.newTool) {
                  await migrateWindowsCompatTool(
                    games.filter((game) => !game.nonSteam).map((game) => game.appid),
                    transition.oldTool,
                    transition.newTool,
                  );
                }
                if (cancelled) return;
                await saveCompatApplied(handledGameAppids(), transition.newTool);
                return;
              } catch (error) {
              }
            }
          }
          persist();
        })().catch(() => {});
      }, 3000);
    })
    .catch(() => {});
  return {
    name: "Freedom Control",
    content: <Content />,
    onDismount() {
      cancelled = true;
      // Best-effort only; backend unload owns the authoritative restore.
      restoreHomePower().catch((error) => {
        console.warn("[armada-control] home power restore failed", error);
      });
      unregisterDownloadWatcher();
      disposeFrameRateChoices();
      disposeSystemInfo();
      unregisterProfileLocalization();
      unregisterLowPowerDownloads();
      stopWatchingSideMenus();
      disposeTouchController();
    },
    icon: (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="24"
        height="24"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M14 17H5" />
        <path d="M19 7h-9" />
        <circle cx="17" cy="17" r="3" />
        <circle cx="7" cy="7" r="3" />
      </svg>
    ),
    alwaysRender: true,
  };
});
