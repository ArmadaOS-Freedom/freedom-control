import { afterPatch, findModuleExport } from "@decky/ui";
import { applySystemInfoCpuNameFallback, KNOWN_GEN5_SOC_CLASS } from "./systemInfoPolicy";

// Steam fills the About > System Information cache by calling the shared
// native SteamClient.System.GetSystemInfo. Patching that promise value is
// enough: the native object, its cache and its stored fields stay untouched.
export function installSystemInfoCpuNameFallback(cpuDeviceClass: string | undefined): () => void {
  const noop = () => {};
  if (cpuDeviceClass !== KNOWN_GEN5_SOC_CLASS) return noop;

  const system: any = (window as any).SteamClient?.System;
  // Structural lookup only: the cache owner exposes the two refresh methods,
  // so no module id or React internals are involved.
  const store: any = system && typeof system.GetSystemInfo === "function"
    ? findModuleExport(
      (value: any) =>
        typeof value?.UpdateSystemInfo === "function"
        && typeof value.UpdateSystemInfoIfNecessary === "function",
    )
    : null;
  if (!system || typeof system.GetSystemInfo !== "function" || !store) {
    console.warn("[armada-control] CPU name display fallback disabled: Steam system info APIs unavailable");
    return noop;
  }

  let active = true;

  // A rejected native query passes straight through; the patch adds no catch
  // and no retry loop. A late completion after unload returns the raw value.
  const patch: any = afterPatch(system, "GetSystemInfo", (_args: any[], ret: any) => {
    if (!ret || typeof ret.then !== "function") return ret;
    return ret.then((info: any) => (active ? applySystemInfoCpuNameFallback(cpuDeviceClass, info) : info));
  });

  const refresh = async (phase: "install" | "unpatch") => {
    try {
      await store.UpdateSystemInfo();
    } catch (error) {
      console.warn(`[armada-control] CPU name display ${phase} refresh failed`, error);
    }
  };

  void refresh("install");

  return () => {
    if (!active) return;
    active = false;
    try {
      patch?.unpatch?.();
    } catch (error) {
      console.warn("[armada-control] CPU name display unpatch failed", error);
    }
    void refresh("unpatch");
  };
}
