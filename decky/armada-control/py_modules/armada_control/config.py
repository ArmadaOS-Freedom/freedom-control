from .power import factory_power_defaults, parse_power
from .rgb import rgb_supported
from .steam import installed_games
from .system import (
    bottom_screen_brightness,
    bottom_screen_active,
    bottom_screen_enabled,
    device_env,
    fan_supported,
    mtp_enabled,
    os_version,
    perf_info,
    desktop_mode,
    desktop_modes,
    sleep_modes,
    ssh_enabled,
    swipe_gestures_enabled,
    swipe_gestures_in_game_enabled,
    usb_network_state,
)
from .tweaks import (
    fex_profile_labels,
    load_env_presets,
    load_fex_contract,
    load_tweaks,
    turnip_drivers,
)


def build_config(include_games=True):
    fex_contract = load_fex_contract()
    env = device_env()
    secondary_brightness = bottom_screen_brightness()
    usb_network = usb_network_state()
    power = parse_power()
    return {
        "power": power,
        "powerDefaults": factory_power_defaults(),
        "tweaks": load_tweaks(),
        "installedGames": installed_games() if include_games else [],
        "fexProfiles": fex_profile_labels(fex_contract),
        "turnipDrivers": turnip_drivers(),
        "envPresets": load_env_presets(),
        "perf": perf_info(),
        "cpuDeviceClass": env.get("ARMADA_SOC_CLASS", ""),
        "deviceId": env.get("ARMADA_DEVICE_ID", ""),
        "rgbSupported": rgb_supported(),
        "protonDefaults": [
            default.strip()
            for default in env.get("ARMADA_PROTON_DEFAULTS", "").split(":")
            if default.strip()
        ],
        "osVersion": os_version(),
        "bottomScreenSupported": bool(
            env.get("ARMADA_SECONDARY_CONNECTOR") and env.get("ARMADA_SECONDARY_TOUCHSCREEN")
        ),
        "bottomScreenEnabled": bottom_screen_enabled(),
        "bottomScreenBrightnessSupported": secondary_brightness is not None,
        "bottomScreenActive": bottom_screen_active(),
        "bottomScreenBrightness": secondary_brightness or 0,
        "chargingFanPwm": int(power["fan"].get("charging_pwm", 0)),
        "fanSupported": fan_supported(),
        "sshEnabled": ssh_enabled(),
        "swipeGesturesEnabled": swipe_gestures_enabled(),
        "swipeGesturesInGameEnabled": swipe_gestures_in_game_enabled(),
        "mtpEnabled": mtp_enabled(),
        "usbNetworkEnabled": usb_network["enabled"],
        "usbNetworkSupported": usb_network["supported"],
        "desktopMode": desktop_mode(),
        "desktopModes": desktop_modes(),
        "sleepMode": env.get("ARMADA_SUSPEND_MODE", "s2idle"),
        "sleepModes": sleep_modes(),
    }
