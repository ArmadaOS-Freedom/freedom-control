import { routerHook } from "@decky/api";
import { Router } from "@decky/ui";
import { sendHeartbeat } from "./api";
import { endLayoutEditing, isLayoutEditing } from "./editor-state";
import LayoutEditor from "./LayoutEditor";
import { TOUCH_LAYOUT_ROUTE } from "./route";

const HEARTBEAT_INTERVAL_MS = 1000;

function currentAppId(): string | null {
  const router = Router as unknown as {
    MainRunningApp?: { appid?: string | number | null };
  };
  const appid = router.MainRunningApp?.appid;
  if (appid === undefined || appid === null || appid === "") {
    return null;
  }
  return String(appid);
}

/**
 * Global touch-controller lifecycle, owned by definePlugin. The heartbeat runs
 * for as long as the plugin is loaded, whether or not the Touch tab is open,
 * and the layout route lives at panel level too. Returns the cleanup that the
 * plugin calls on dismount.
 */
export function registerTouchController(): () => void {
  let stopped = false;
  let inFlight = false;
  let timer: number | undefined;

  routerHook.addRoute(TOUCH_LAYOUT_ROUTE, LayoutEditor, { exact: true });

  const tick = async () => {
    if (stopped || inFlight) {
      return;
    }
    inFlight = true;
    try {
      await sendHeartbeat(isLayoutEditing() ? null : currentAppId());
    } catch (error) {
      console.warn("[armada-control] touch heartbeat failed", error);
    } finally {
      inFlight = false;
    }
  };

  void tick();
  timer = window.setInterval(() => {
    void tick();
  }, HEARTBEAT_INTERVAL_MS);

  return () => {
    stopped = true;
    if (timer !== undefined) {
      window.clearInterval(timer);
      timer = undefined;
    }
    endLayoutEditing();
    routerHook.removeRoute(TOUCH_LAYOUT_ROUTE);
    void sendHeartbeat(null).catch((error) => {
      console.warn("[armada-control] touch final heartbeat failed", error);
    });
  };
}
