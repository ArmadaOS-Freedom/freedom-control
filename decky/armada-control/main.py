import asyncio
import logging

from armada_control.config import build_config
from armada_control.display_orientation import get_display_orientation, set_display_orientation
from armada_control.power import save_power_config
from armada_control.rgb import get_rgb, set_rgb
from armada_control.steam import compat_mapped_appids, installed_games
from armada_control.system import (
    bottom_screen_active,
    reapply_perf,
    restart_game_mode,
    set_bottom_screen_brightness,
    set_bottom_screen_enabled,
    get_sleep_logs_enabled,
    set_mtp_enabled,
    set_desktop_mode,
    set_sleep_mode,
    set_sleep_logs_enabled,
    set_ssh_enabled,
    set_swipe_gestures_enabled,
    set_swipe_gestures_in_game_enabled,
    set_usb_network_enabled,
    suspend_swipe_gestures,
    swipe_gestures_enabled,
    swipe_gestures_in_game_enabled,
)
from armada_control.tweaks import load_compat_applied, load_tweaks, save_compat_applied, save_tweaks
from armada_control.updates import current as current_updates, delete_local, install as install_update, local as local_updates, local_selection_id, progress as update_progress, refresh as refresh_updates
from armada_control.fan_curves import get_state as get_fans_state, save_all as save_fan_curves, save_charging_pwm
from armada_control.fan_sensors import get_current_temp
from armada_control.home_power import HomePower
from armada_control.tf_library.service import InstallerError, TfLibraryService
from armada_control.touch_controller import TouchController

logger = logging.getLogger("armada-control")


class Plugin:
    def __init__(self):
        self._home_power = HomePower()
        self._tf = TfLibraryService()
        self._touch = TouchController()
        # The canonical swipe-switch reads and the touch update they feed are
        # one critical section: without it two overlapping RPCs (or startup and
        # an RPC) could apply the older of two policies last.
        self._touch_policy_lock = asyncio.Lock()
        self._tweaks_lock = asyncio.Lock()

    async def _main(self):
        try:
            await asyncio.to_thread(self._home_power.sync)
        except Exception:
            logger.exception("Failed to apply the saved Steam UI power preference")

        try:
            # Startup applies the switches it reads while holding the same lock
            # as the RPC path, so a newer preference can never be overwritten
            # by the saved state loaded here.
            async with self._touch_policy_lock:
                enabled = await asyncio.to_thread(swipe_gestures_enabled)
                in_game = await asyncio.to_thread(swipe_gestures_in_game_enabled)
                await self._touch.startup(enabled, in_game)
        except Exception:
            logger.exception("Failed to load the saved touch controller state")

    async def _unload(self):
        try:
            await self._touch.shutdown()
        except Exception:
            logger.exception("Failed to stop the touch controller")
        try:
            await asyncio.to_thread(self._home_power.restore)
        except Exception:
            logger.exception("Failed to restore the Steam UI refresh rate on unload")

    async def _uninstall(self):
        try:
            await self._touch.shutdown()
        except Exception:
            logger.exception("Failed to stop the touch controller")
        try:
            await asyncio.to_thread(self._home_power.restore)
        except Exception:
            logger.exception("Failed to restore the Steam UI refresh rate on uninstall")

    async def get_home_power(self):
        return await asyncio.to_thread(self._home_power.get_state)

    async def set_home_power(self, enabled):
        return await asyncio.to_thread(self._home_power.set_enabled, enabled)

    async def sync_home_power(self):
        return await asyncio.to_thread(self._home_power.sync)

    async def restore_home_power(self):
        return await asyncio.to_thread(self._home_power.restore)

    async def get_display_orientation(self):
        return await asyncio.to_thread(get_display_orientation)

    async def set_display_orientation(self, orientation):
        return await asyncio.to_thread(set_display_orientation, orientation)

    # Offload blocking work to a thread so a slow call can't stall Decky's asyncio loop.
    async def get_config(self):
        return await asyncio.to_thread(build_config, False)

    async def get_installed_games(self):
        return await asyncio.to_thread(installed_games)

    async def get_compat_mapped_appids(self, tool):
        return await asyncio.to_thread(compat_mapped_appids, tool)

    async def save_power_config(self, data):
        await asyncio.to_thread(save_power_config, data)
        return await self.get_config()

    async def save_tweaks(self, data):
        async with self._tweaks_lock:
            await asyncio.to_thread(save_tweaks, data)
        return await self.get_config()

    async def get_compat_applied(self):
        return await asyncio.to_thread(load_compat_applied)

    async def save_compat_applied(self, appids, proton_default=None):
        return await asyncio.to_thread(save_compat_applied, appids, proton_default)

    async def get_updates(self):
        return await asyncio.to_thread(current_updates)

    async def refresh_updates(self):
        return await asyncio.to_thread(refresh_updates)

    async def install_update(self, update_id):
        return await asyncio.to_thread(install_update, update_id)

    async def get_update_progress(self, update_id):
        return await asyncio.to_thread(update_progress, update_id)

    async def get_local_updates(self):
        return await asyncio.to_thread(local_updates)

    async def delete_local_update(self, kind, update_id):
        async with self._tweaks_lock:
            tweaks = await asyncio.to_thread(load_tweaks)
            global_tweaks = dict(tweaks.get("global", {}))
            selected = global_tweaks.get("turnipDriver") if kind == "turnip" else global_tweaks.get("windowsCompatTool")
            field = "turnipDriver" if kind == "turnip" else "windowsCompatTool"
            installed_id = await asyncio.to_thread(local_selection_id, kind, update_id)
            matches = selected in (update_id, installed_id)
            if matches:
                updated = dict(tweaks)
                global_tweaks.pop(field, None)
                updated["global"] = global_tweaks
                await asyncio.to_thread(save_tweaks, updated)
                try:
                    result = await asyncio.to_thread(delete_local, kind, update_id)
                except Exception:
                    await asyncio.to_thread(save_tweaks, tweaks)
                    raise
                result["selectionCleared"] = True
            else:
                result = await asyncio.to_thread(delete_local, kind, update_id)
        return result

    async def set_swipe_gestures_enabled(self, enabled):
        result = await asyncio.to_thread(set_swipe_gestures_enabled, enabled)
        await self._sync_touch_swipe_policy()
        return result

    async def set_swipe_gestures_in_game_enabled(self, enabled):
        result = await asyncio.to_thread(set_swipe_gestures_in_game_enabled, enabled)
        await self._sync_touch_swipe_policy()
        return result

    async def _sync_touch_swipe_policy(self):
        # The Controller's canonical switches are the only edge-swipe
        # preference; the touch runtime derives its own value from them.  Both
        # reads and the apply form one critical section, so an RPC that read
        # the switches later can never be overwritten by an RPC that read them
        # earlier.
        async with self._touch_policy_lock:
            try:
                enabled = await asyncio.to_thread(swipe_gestures_enabled)
                in_game = await asyncio.to_thread(swipe_gestures_in_game_enabled)
                await self._touch.sync_swipe_policy(enabled, in_game)
            except Exception:
                logger.exception("Failed to apply the touch swipe policy")

    async def suspend_swipe_gestures(self, suspended):
        return await asyncio.to_thread(suspend_swipe_gestures, suspended)

    async def set_ssh_enabled(self, enabled):
        return await asyncio.to_thread(set_ssh_enabled, enabled)

    async def set_mtp_enabled(self, enabled):
        return await asyncio.to_thread(set_mtp_enabled, enabled)

    async def set_usb_network_enabled(self, enabled):
        return await asyncio.to_thread(set_usb_network_enabled, enabled)

    async def set_bottom_screen_enabled(self, enabled):
        return await asyncio.to_thread(set_bottom_screen_enabled, enabled)

    async def set_bottom_screen_brightness(self, brightness):
        return await asyncio.to_thread(set_bottom_screen_brightness, brightness)

    async def get_bottom_screen_active(self):
        return await asyncio.to_thread(bottom_screen_active)

    async def set_desktop_mode(self, value):
        return await asyncio.to_thread(set_desktop_mode, value)

    async def set_sleep_mode(self, value):
        return await asyncio.to_thread(set_sleep_mode, value)

    async def get_sleep_logs_enabled(self):
        return await asyncio.to_thread(get_sleep_logs_enabled)

    async def set_sleep_logs_enabled(self, enabled):
        return await asyncio.to_thread(set_sleep_logs_enabled, enabled)

    async def reapply_perf(self):
        return await asyncio.to_thread(reapply_perf)

    async def restart_game_mode(self):
        return await asyncio.to_thread(restart_game_mode)

    async def get_rgb(self):
        return await asyncio.to_thread(get_rgb)

    async def set_rgb(self, enabled, link_brightness, color, saturation, max_brightness, brightness):
        return await asyncio.to_thread(
            set_rgb,
            enabled,
            link_brightness,
            color,
            saturation,
            max_brightness,
            brightness,
        )

    async def get_fans_state(self):
        return await asyncio.to_thread(get_fans_state)

    async def save_fan_curves(self, fan_curves, fan_settings):
        return await asyncio.to_thread(save_fan_curves, fan_curves, fan_settings)

    async def set_charging_fan_pwm(self, pwm):
        return await asyncio.to_thread(save_charging_pwm, pwm)

    # Polled separately from get_fans_state -- see hooks/useCurrentTemp.
    async def get_current_temp(self):
        return await asyncio.to_thread(get_current_temp)

    # TF-card library page: the migrated installer backend behind one envelope
    # so the UI never sees a traceback, with the blocking work offloaded to a
    # thread like every other RPC above.
    async def _call_tf(self, function, *args):
        try:
            data = await asyncio.to_thread(function, *args)
        except InstallerError as error:
            return {"ok": False, "error": error.code, "detail": error.detail}
        except Exception:
            # Never surface an unexpected traceback or internal detail to the UI.
            return {"ok": False, "error": "internal_error", "detail": ""}
        return {"ok": True, "data": data}

    async def get_tf_inspection(self):
        return await self._call_tf(self._tf.inspect)

    async def prepare_tf_library(self, device):
        return await self._call_tf(self._tf.prepare, device)

    async def start_tf_library(self, token, confirmed=False):
        return await self._call_tf(self._tf.start, token, confirmed)

    async def get_tf_status(self):
        return await self._call_tf(self._tf.status)

    # Touch page: the Freedom Touch runtime is owned by this plugin, so the
    # frontend reaches it through these prefixed names only.
    async def get_touch_state(self):
        return await self._touch.get_state()

    async def set_touch_settings(self, settings):
        return await self._touch.set_settings(settings)

    async def update_touch_options(self, patch):
        return await self._touch.update_options(patch)

    async def select_touch_preset(self, preset_id):
        return await self._touch.select_preset(preset_id)

    async def save_touch_preset(
        self,
        name,
        layout,
        scale,
        overwrite_id=None,
        dpad_mode=None,
        base_preset_id=None,
    ):
        return await self._touch.save_preset(
            name, layout, scale, overwrite_id, dpad_mode, base_preset_id
        )

    async def delete_touch_preset(self, preset_id):
        return await self._touch.delete_preset(preset_id)

    async def reset_touch_layout(self):
        return await self._touch.reset_layout()

    async def touch_heartbeat(self, appid=None):
        return await self._touch.heartbeat(appid)

    async def preview_touch_haptics(self):
        return await self._touch.preview_haptics()

    async def set_touch_controls_visible(self, visible):
        return await self._touch.set_controls_visible(visible)
