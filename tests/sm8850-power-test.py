#!/usr/bin/env python3
"""SM8850 power-profile regression tests (T01 / a1).

Exercises the real config interfaces instead of grepping implementations:

* the daemon's ``parse_config`` / ``apply_underclock_refs`` / ``cpu_max_target``
  are driven through temporary copies of the factory config, with the
  chip-level ``power-profiles-SM8850.conf`` placed next to the copy;
* the plugin's ``parse_power`` / ``factory_power_defaults`` / ``render_power``
  read the same fixture; only the Decky/privileged transports are stubbed, so
  the config code itself runs unchanged.

Temporary directories come from ``tempfile`` (i.e. the inherited TMPDIR).

Run with the standard library only::

    python3 tests/sm8850-power-test.py
"""

import configparser
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "system_files/usr/lib/armada"
LIBEXEC = ROOT / "system_files/usr/libexec/armada"
PUBLIC_PROFILES = ROOT / "system_files/usr/share/armada/power-profiles.conf"
SM8850_PROFILES = ROOT / "system_files/usr/share/armada/power-profiles-SM8850.conf"
GAME_TWEAKS = ROOT / "system_files/usr/share/armada/game-tweaks.json"
BOARD_PROFILE = ROOT / "system_files/usr/lib/armada/devices/lenovo-tb323fu.conf"
PY_MODULES = ROOT / "decky/armada-control/py_modules"

# Firmware OPP lists observed on the device (kHz).
OPP = {
    0: [
        384000, 537600, 691200, 787200, 883200, 998400, 1113600, 1228800,
        1324800, 1440000, 1555200, 1670400, 1785600, 1900800, 1996800,
        2112000, 2227200, 2361600, 2496000, 2611200, 2745600, 2899200,
        3033600, 3187200, 3302400, 3398400, 3513600, 3628800,
    ],
    6: [
        768000, 883200, 1017600, 1132800, 1267200, 1382400, 1497600, 1632000,
        1747200, 1862400, 1977600, 2112000, 2227200, 2438400, 2668800,
        2880000, 3129600, 3398400, 3648000, 3763200, 3859200, 3974400,
        4070400, 4185600, 4262400, 4339200, 4396800,
    ],
}

# Chip layer fixture: governor policy only, so the partial-override behavior of
# the merge is what test 1 checks.  The tier values match the design contract.
CHIP_FIXTURE = """\
[profile.eco]
cpu_governor=schedutil

[profile.balanced]
cpu_governor=schedutil

[profile.performance]
cpu_governor=performance

[underclock.SM8850.large]
cpu_max_policy0=2112000
cpu_max_policy6=2438400

[underclock.SM8850.medium]
cpu_max_policy0=2899200
cpu_max_policy6=3398400

[underclock.SM8850.small]
cpu_max_policy0=3187200
cpu_max_policy6=3859200
"""


def load_script(name):
    spec = importlib.util.spec_from_loader(
        name.replace("-", "_"),
        importlib.machinery.SourceFileLoader(
            name.replace("-", "_"), str(LIBEXEC / name)),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_plugin_power():
    """Import the plugin module with only the Decky transports stubbed."""
    if str(PY_MODULES) not in sys.path:
        sys.path.insert(0, str(PY_MODULES))
    import armada_control  # noqa: F401  (empty package marker)

    system = types.ModuleType("armada_control.system")
    system.ENV = {}

    def device_env():
        return dict(system.ENV)

    system.device_env = device_env

    privileged = types.ModuleType("armada_control.privileged")
    privileged.calls = []

    def call(action, **payload):
        privileged.calls.append((action, payload))
        return {}

    privileged.call = call

    sys.modules["armada_control.system"] = system
    sys.modules["armada_control.privileged"] = privileged
    from armada_control import power

    return power, system


if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

POWERD = load_script("armada-powerd")
PLUGIN_POWER, PLUGIN_SYSTEM = load_plugin_power()


def plugin_soc(soc):
    """Publish the SoC through the plugin's device_env seam."""
    PLUGIN_SYSTEM.ENV = {"ARMADA_SOC_CLASS": soc} if soc else {}
    if "device_env" in vars(PLUGIN_POWER):
        # Production may use ``from .system import device_env``.
        PLUGIN_POWER.device_env = PLUGIN_SYSTEM.device_env


def parse_device_conf(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


class DaemonConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="armada-sm8850-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.factory = self.tmp / "power-profiles.conf"
        shutil.copyfile(PUBLIC_PROFILES, self.factory)
        self.etc = self.tmp / "etc-power-profiles.conf"
        self.saved = (POWERD.FACTORY_CONFIG_FILE, POWERD.CONFIG_FILE)
        self.addCleanup(self.restore_paths)

    def restore_paths(self):
        POWERD.FACTORY_CONFIG_FILE, POWERD.CONFIG_FILE = self.saved

    def write_chip(self):
        (self.tmp / "power-profiles-SM8850.conf").write_text(
            CHIP_FIXTURE, encoding="utf-8")

    def daemon(self, soc="SM8850", etc_text=None, factory=None):
        if etc_text is not None:
            self.etc.write_text(etc_text, encoding="utf-8")
        POWERD.FACTORY_CONFIG_FILE = factory or self.factory
        POWERD.CONFIG_FILE = self.etc
        power = POWERD.ArmadaPower.__new__(POWERD.ArmadaPower)
        power.env = {"ARMADA_SOC_CLASS": soc} if soc else {}
        power.cap_miss_warned = set()
        return power

    def test_chip_layer_only_overrides_its_own_fields(self):
        """public factory -> chip factory -> /etc, field by field."""
        self.write_chip()
        power = self.daemon(etc_text=(
            "[profile.eco]\n"
            "cpu_max=0.50\n"
            "\n"
            "[profile.balanced]\n"
            "gpu_min=0.10\n"
        ))
        (default_profile, underclocks, fan_curves, profiles,
         _fan, _suspend, _system) = power.parse_config()
        eco, balanced, performance = (
            profiles["eco"], profiles["balanced"], profiles["performance"])

        self.assertEqual(default_profile, "balanced")
        # chip layer owns the governor policy only
        self.assertEqual(eco["cpu_governor"], "schedutil")
        self.assertEqual(balanced["cpu_governor"], "schedutil")
        self.assertEqual(performance["cpu_governor"], "performance")
        # /etc still wins for the field it actually sets
        self.assertAlmostEqual(eco["cpu_max"], 0.50)
        self.assertAlmostEqual(balanced["gpu_min"], 0.10)
        # fields absent from both chip and /etc keep the public factory values
        self.assertEqual(eco["label"], "Eco")
        self.assertEqual(eco["cpu_underclock"], "large")
        self.assertAlmostEqual(eco["gpu_max"], 0.60)
        self.assertEqual(eco["fan_curve"], "relaxed")
        self.assertAlmostEqual(balanced["cpu_max"], 0.60)
        self.assertEqual(underclocks["SM8850"]["large"],
                         {0: 2112000, 6: 2438400})

    def test_non_sm8850_keeps_the_public_factory(self):
        self.write_chip()
        power = self.daemon(soc="SM8750")
        (_default, underclocks, _curves, profiles,
         _fan, _suspend, _system) = power.parse_config()
        self.assertEqual(profiles["eco"]["cpu_governor"], "schedutil")
        self.assertEqual(profiles["balanced"]["cpu_governor"], "schedutil")
        self.assertEqual(profiles["performance"]["cpu_governor"], "schedutil")
        self.assertEqual(underclocks["SM8750"]["large"],
                         {0: 1785600, 6: 1958400})
        self.assertNotIn("SM8850", underclocks)

    def test_missing_chip_file_keeps_the_public_factory(self):
        # no power-profiles-SM8850.conf in this fixture directory
        profiles = self.daemon().parse_config()[3]
        self.assertEqual(profiles["eco"]["cpu_governor"], "schedutil")
        self.assertEqual(profiles["balanced"]["cpu_governor"], "schedutil")

    def test_cpu_max_cap_floors_the_policy_table(self):
        """The lower of the per-policy tier and the cpu_max ratio wins."""
        power = POWERD.ArmadaPower.__new__(POWERD.ArmadaPower)
        power.env = {"ARMADA_SOC_CLASS": "SM8850"}
        power.underclock_config = {
            "SM8850": {"large": {0: 2112000, 6: 2438400}}}
        settings = {
            "cpu_max": 0.35,
            "cpu_max_policy": {0: 2112000, 6: 2438400},
        }
        for policy_id, tier, expected in ((0, 2112000, 1228800),
                                          (6, 2438400, 1497600)):
            freqs = OPP[policy_id]
            target = power.cpu_max_target(settings, policy_id, freqs)
            self.assertLessEqual(target, tier)
            self.assertLessEqual(target, int(freqs[-1] * 0.35))
            self.assertEqual(power.choose_at_most(freqs, target), expected)

    def test_none_underclock_stays_percent_limited(self):
        power = POWERD.ArmadaPower.__new__(POWERD.ArmadaPower)
        power.env = {"ARMADA_SOC_CLASS": "SM8850"}
        power.underclock_config = {"SM8850": {"large": {0: 2112000}}}
        settings = {"cpu_max": 0.65, "cpu_max_policy": {}}
        freqs = OPP[0]
        target = power.cpu_max_target(settings, 0, freqs)
        self.assertLess(target, freqs[-1])
        self.assertEqual(power.choose_at_most(freqs, target), 2227200)

    def test_other_soc_keeps_table_priority(self):
        power = POWERD.ArmadaPower.__new__(POWERD.ArmadaPower)
        power.env = {"ARMADA_SOC_CLASS": "SM8750"}
        power.underclock_config = {
            "SM8750": {"medium": {0: 2227200, 6: 2246400}}}
        settings = {
            "cpu_max": 0.35,
            "cpu_max_policy": {0: 2227200, 6: 2246400},
        }
        self.assertEqual(
            power.cpu_max_target(settings, 0, OPP[0]), 2227200)
        self.assertEqual(
            power.cpu_max_target(settings, 6, OPP[6]), 2246400)

    def test_underclock_matches_policies_by_cluster_cpus(self):
        """A policy named after a later CPU still gets its cluster's cap.

        The six-core SM8650 (CPUs 1-2 absent) exposes policy0/3/5/7, the
        eight-core one policy0/2/5/7; the factory table uses policy2 for the
        middle cluster, so both layouts must cap it.
        """
        freqs = {
            0: [1459200, 1689600, 1939200, 2265600],
            2: [2035200, 2323200, 2764800, 2956800],
            5: [2035200, 2323200, 2764800, 2956800],
            7: [2112000, 2438400, 2937600, 3052800],
        }
        layouts = {
            "six-core": {0: [0, 1], 3: [2, 3, 4], 5: [5, 6], 7: [7]},
            "eight-core": {0: [0, 1], 2: [2, 3, 4], 5: [5, 6], 7: [7]},
        }
        for name, layout in layouts.items():
            with self.subTest(layout=name):
                power = self.daemon(soc="SM8650", factory=PUBLIC_PROFILES)
                (_default, underclocks, _curves, profiles,
                 _fan, _suspend, _system) = power.parse_config()
                power.underclock_config = underclocks
                power.profile_config = profiles
                power.profile = "balanced"
                power.apply_underclock_refs()
                self.assertEqual(profiles["balanced"]["cpu_underclock"], "medium")
                root = self.tmp / name
                power.cpu_policies = []
                for policy_id, cpus in layout.items():
                    policy = root / f"policy{policy_id}"
                    policy.mkdir(parents=True)
                    (policy / "related_cpus").write_text(
                        " ".join(map(str, cpus)) + "\n", encoding="utf-8")
                    (policy / "scaling_available_frequencies").write_text(
                        " ".join(map(str, freqs[cpus[0]])) + "\n",
                        encoding="utf-8")
                    power.cpu_policies.append(policy)
                middle = "policy3" if name == "six-core" else "policy2"
                self.assertEqual(power.resolved_cpu_caps(), {
                    "policy0": 1689600,
                    middle: 2323200,
                    "policy5": 2323200,
                    "policy7": 2438400,
                })
                power.gpu = None
                power.apply_profile()
                self.assertEqual(
                    (root / middle / "scaling_max_freq").read_text(
                        encoding="utf-8").strip(), "2323200")

    def test_policy_missing_from_selected_table_warns_once(self):
        power = POWERD.ArmadaPower.__new__(POWERD.ArmadaPower)
        power.env = {"ARMADA_SOC_CLASS": "SM8650"}
        power.underclock_config = {"SM8650": {"medium": {0: 1689600}}}
        power.cap_miss_warned = set()
        capped = {"cpu_max": 1.0, "cpu_max_policy": {0: 1689600}}
        uncapped = {"cpu_max": 1.0, "cpu_max_policy": {}}
        freqs = [2035200, 2956800]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for _ in range(3):
                self.assertEqual(
                    power.cpu_max_target(capped, 3, freqs, [2, 3, 4]), 2956800)
            power.cpu_max_target(uncapped, 5, freqs, [5, 6])
        self.assertEqual(err.getvalue().count("no cpu_max_policy entry"), 1)
        self.assertIn("policy3", err.getvalue())

    def test_factory_tiers_use_real_opp_and_are_monotonic(self):
        self.assertTrue(SM8850_PROFILES.is_file(),
                        f"missing chip factory file: {SM8850_PROFILES}")
        parser = configparser.ConfigParser()
        parser.read(SM8850_PROFILES)
        tiers = {}
        for level in ("large", "medium", "small"):
            section = f"underclock.SM8850.{level}"
            self.assertTrue(parser.has_section(section),
                            f"missing section [{section}]")
            for policy_id in (0, 6):
                key = f"cpu_max_policy{policy_id}"
                self.assertTrue(parser.has_option(section, key),
                                f"missing {key} in [{section}]")
                value = parser.getint(section, key)
                self.assertIn(value, OPP[policy_id],
                              f"{section} {key}={value} is not a device OPP")
                tiers[(level, policy_id)] = value
        for policy_id in (0, 6):
            self.assertLess(tiers[("large", policy_id)],
                            tiers[("medium", policy_id)])
            self.assertLess(tiers[("medium", policy_id)],
                            tiers[("small", policy_id)])
            self.assertLessEqual(tiers[("small", policy_id)],
                                 OPP[policy_id][-1])

    def test_extreme_profile_uses_firmware_max(self):
        self.assertTrue(SM8850_PROFILES.is_file(),
                        f"missing chip factory file: {SM8850_PROFILES}")
        chip = configparser.ConfigParser()
        chip.read(SM8850_PROFILES)
        if chip.has_section("profile.extreme"):
            self.assertFalse(
                [key for key in chip.options("profile.extreme")
                 if key.startswith("cpu_max_policy")],
                "extreme must not carry a policy table")
        for soc in ("SM8850", "SM8550", "SM8750"):
            power = self.daemon(soc=soc, factory=PUBLIC_PROFILES, etc_text=(
                "[profile.performance]\n"
                "cpu_max_policy0=1234567\n"
            ))
            (_default, underclocks, _curves, profiles,
             _fan, _suspend, _system) = power.parse_config()
            performance = profiles["performance"]
            extreme = profiles["extreme"]
            self.assertEqual(performance["cpu_max_policy"], {0: 1234567})
            self.assertEqual(extreme["cpu_max_policy"], {})
            power.underclock_config = underclocks
            power.profile = "extreme"
            freqs = OPP[0]
            target = power.cpu_max_target(extreme, 0, freqs)
            self.assertEqual(power.choose_at_most(freqs, target), freqs[-1])

    def test_explicit_extreme_fields_are_preserved(self):
        power = self.daemon(etc_text=(
            "[profile.performance]\n"
            "cpu_max_policy0=1234567\n"
            "\n"
            "[profile.extreme]\n"
            "label=Custom Extreme\n"
            "cpu_max_policy0=2000000\n"
            "vendor_extra=keep\n"
        ))
        config = configparser.ConfigParser()
        config.read(POWERD.FACTORY_CONFIG_FILE)
        config.read(POWERD.CONFIG_FILE)
        POWERD.ensure_extreme_profile(config)
        self.assertEqual(config.get("profile.extreme", "label"), "Custom Extreme")
        self.assertEqual(config.get("profile.extreme", "cpu_max_policy0"), "2000000")
        self.assertEqual(config.get("profile.extreme", "vendor_extra"), "keep")
        power.parse_config()

    def test_board_profile_primes_are_6_7(self):
        self.assertTrue(BOARD_PROFILE.is_file())
        values = parse_device_conf(BOARD_PROFILE)
        self.assertEqual(values.get("ARMADA_SOC_CLASS"), "SM8850")
        self.assertEqual(values.get("ARMADA_PRIME_CORES"), "6-7")
        self.assertEqual(values.get("ARMADA_BIG_CORES"), "0-7")
        self.assertEqual(values.get("ARMADA_LITTLE_CORES"), "")
        # description metadata only: neither factory layer assigns game masks
        global_tweaks = json.loads(GAME_TWEAKS.read_text(encoding="utf-8"))["global"]
        self.assertIsNone(global_tweaks.get("cores"))
        self.assertIsNone(global_tweaks.get("gamescopeCores"))

    def test_shared_defaults_cover_supported_soc_classes(self):
        expected = {
            "eco": ("schedutil", 0.40, "large", 0.60, 0.00, "relaxed"),
            "balanced": ("schedutil", 0.60, "medium", 0.80, 0.00, "moderate"),
            "performance": ("schedutil", 0.80, "small", 1.00, 0.00, "aggressive"),
            "extreme": ("performance", 1.00, "none", 1.00, 0.60, "aggressive"),
        }
        keys = ("cpu_governor", "cpu_max", "cpu_underclock",
                "gpu_max", "gpu_min", "fan_curve")
        for soc in ("SM8550", "SM8650", "SM8750", "SM8850"):
            power = self.daemon(soc=soc, factory=PUBLIC_PROFILES)
            result = power.parse_config()
            self.assertEqual(result[0], "balanced")
            profiles = result[3]
            self.assertEqual(set(profiles), {"eco", "balanced", "performance", "extreme"})
            for name, fields in expected.items():
                self.assertEqual(tuple(profiles[name][key] for key in keys), fields)

    def test_old_three_profile_config_synthesizes_extreme(self):
        config = configparser.ConfigParser()
        config.read(self.factory)
        config.remove_section("profile.extreme")
        with self.factory.open("w", encoding="utf-8") as handle:
            config.write(handle)
        self.write_chip()
        self.daemon(etc_text=(
            "[profile.eco]\n"
            "cpu_max=0.50\n"
            "\n"
            "[profile.performance]\n"
            "cpu_max_policy0=1234567\n"
        ))
        (_default, _under, _curves, profiles,
         _fan, _suspend, _system) = POWERD.ArmadaPower.parse_config(self.daemon())
        self.assertIn("extreme", profiles)
        self.assertEqual(profiles["extreme"]["label"], "Extreme")
        self.assertEqual(profiles["extreme"]["cpu_governor"],
                         profiles["performance"]["cpu_governor"])
        self.assertAlmostEqual(profiles["extreme"]["cpu_max"],
                               profiles["performance"]["cpu_max"])
        self.assertEqual(profiles["extreme"]["fan_curve"],
                         profiles["performance"]["fan_curve"])
        self.assertEqual(profiles["extreme"]["cpu_max_policy"], {0: 1234567})
        self.assertEqual(profiles["performance"]["label"], "Performance")
        self.assertEqual(self.daemon().parse_config()[0], "balanced")

    def test_partial_extreme_override_inherits_performance(self):
        config = configparser.ConfigParser()
        config.read(self.factory)
        config.remove_section("profile.extreme")
        with self.factory.open("w", encoding="utf-8") as handle:
            config.write(handle)
        self.write_chip()
        power = self.daemon(etc_text=(
            "[general]\n"
            "default_profile=extreme\n"
            "\n"
            "[profile.performance]\n"
            "cpu_max_policy0=1234567\n"
            "\n"
            "[profile.extreme]\n"
            "cpu_max=0.50\n"
        ))
        (default, _under, _curves, profiles,
         _fan, _suspend, _system) = power.parse_config()
        self.assertEqual(default, "extreme")
        self.assertAlmostEqual(profiles["extreme"]["cpu_max"], 0.50)
        self.assertEqual(profiles["extreme"]["cpu_governor"],
                         profiles["performance"]["cpu_governor"])
        self.assertAlmostEqual(profiles["extreme"]["gpu_max"],
                               profiles["performance"]["gpu_max"])
        self.assertEqual(profiles["extreme"]["fan_curve"],
                         profiles["performance"]["fan_curve"])
        self.assertEqual(profiles["extreme"]["cpu_max_policy"], {})


class PluginConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="armada-sm8850-plugin-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.factory = self.tmp / "power-profiles.conf"
        shutil.copyfile(PUBLIC_PROFILES, self.factory)
        (self.tmp / "power-profiles-SM8850.conf").write_text(
            CHIP_FIXTURE, encoding="utf-8")
        self.etc = self.tmp / "etc-power-profiles.conf"
        self.saved = (PLUGIN_POWER.FACTORY_POWER_CONFIG,
                      PLUGIN_POWER.POWER_CONFIG)
        self.addCleanup(self.restore)
        PLUGIN_POWER.FACTORY_POWER_CONFIG = self.factory
        PLUGIN_POWER.POWER_CONFIG = self.etc
        plugin_soc("SM8850")

    def restore(self):
        PLUGIN_POWER.FACTORY_POWER_CONFIG, PLUGIN_POWER.POWER_CONFIG = self.saved
        plugin_soc("")
        PLUGIN_SYSTEM.ENV = {}

    def write_etc(self, text):
        self.etc.write_text(text, encoding="utf-8")

    def test_effective_config_and_defaults_read_chip_layer(self):
        data = PLUGIN_POWER.parse_power()
        factory = PLUGIN_POWER.factory_power_defaults()
        for parsed in (data, factory):
            self.assertEqual(parsed["profiles"]["eco"]["cpu_governor"],
                             "schedutil")
            self.assertEqual(parsed["profiles"]["balanced"]["cpu_governor"],
                             "schedutil")
            self.assertEqual(parsed["profiles"]["performance"]["cpu_governor"],
                             "performance")
        # the chip layer is partial: public factory fields survive
        self.assertEqual(data["profiles"]["eco"]["cpu_max"], "0.40")
        self.assertEqual(data["profiles"]["eco"]["label"], "Eco")

    def test_plugin_helper_keeps_independent_factory_extreme(self):
        self.write_etc("[profile.performance]\ncpu_max_policy0=1234567\n")
        merged = configparser.ConfigParser()
        merged.read([self.factory, self.etc])
        PLUGIN_POWER.ensure_extreme_profile(merged)
        self.assertEqual(merged.get("profile.performance", "cpu_max_policy0"), "1234567")
        self.assertFalse(merged.has_option("profile.extreme", "cpu_max_policy0"))
        self.assertEqual(merged.get("profile.extreme", "label"), "Extreme")
        parsed = PLUGIN_POWER.parse_power()
        self.assertEqual(float(parsed["profiles"]["extreme"]["cpu_max"]), 1.0)

    def test_user_override_does_not_pollute_restore_defaults(self):
        self.write_etc("[profile.eco]\ncpu_governor=performance\n")
        self.assertEqual(
            PLUGIN_POWER.parse_power()["profiles"]["eco"]["cpu_governor"],
            "performance")
        self.assertEqual(
            PLUGIN_POWER.factory_power_defaults()["profiles"]["eco"]["cpu_governor"],
            "schedutil")

    def test_render_restores_factory_fields_and_keeps_unknown(self):
        self.write_etc(
            "[profile.eco]\n"
            "cpu_governor = performance\n"
            "cpu_max = 0.50\n"
            "custom_extra = hand-edited\n"
            "\n"
            "[general]\n"
            "vendor_note = keep\n"
        )
        data = PLUGIN_POWER.parse_power()
        factory = PLUGIN_POWER.factory_power_defaults()
        self.assertEqual(data["profiles"]["eco"]["cpu_governor"],
                         "performance")

        restored_text = PLUGIN_POWER.render_power(factory, factory)
        self.assertNotIn("schedutil", restored_text,
                         "factory values must not be written to /etc")
        restored = configparser.ConfigParser()
        restored.read_string(restored_text)
        self.assertTrue(restored.has_section("profile.eco"))
        self.assertFalse(restored.has_option("profile.eco", "cpu_governor"))
        self.assertFalse(restored.has_option("profile.eco", "cpu_max"))
        self.assertEqual(restored.get("profile.eco", "custom_extra"),
                         "hand-edited")
        self.assertEqual(restored.get("general", "vendor_note"), "keep")
        self.assertFalse(restored.has_section("profile.balanced"))

        edited = configparser.ConfigParser()
        edited.read_string(PLUGIN_POWER.render_power(data, factory))
        self.assertEqual(edited.get("profile.eco", "cpu_governor"),
                         "performance")
        self.assertEqual(edited.get("profile.eco", "cpu_max"), "0.50")
        self.assertEqual(edited.get("profile.eco", "custom_extra"),
                         "hand-edited")


    def test_extreme_roundtrip_and_factory_restore(self):
        self.write_etc(
            "[general]\n"
            "default_profile=balanced\n"
            "\n"
            "[profile.performance]\n"
            "cpu_max=0.80\n"
            "cpu_max_policy0=1234567\n"
            "unknown_perf_note=keep\n"
            "\n"
            "[profile.extreme]\n"
            "cpu_max=0.75\n"
            "vendor_extra=keep-extreme\n"
        )
        parsed = PLUGIN_POWER.parse_power()
        self.assertEqual(parsed["general"]["default_profile"], "balanced")
        self.assertEqual(parsed["profiles"]["performance"]["cpu_max"], "0.80")
        self.assertEqual(parsed["profiles"]["extreme"]["cpu_max"], "0.75")

        data = PLUGIN_POWER.parse_power()
        data["profiles"]["extreme"]["gpu_max"] = "0.95"
        data["general"]["default_profile"] = "extreme"
        self.write_etc(PLUGIN_POWER.render_power(data, PLUGIN_POWER.factory_power_defaults()))
        reread = PLUGIN_POWER.parse_power()
        self.assertEqual(reread["general"]["default_profile"], "extreme")
        self.assertEqual(reread["profiles"]["extreme"]["gpu_max"], "0.95")
        for name in ("eco", "balanced", "performance"):
            self.assertEqual(
                PLUGIN_POWER.profile_overrides(reread["profiles"][name]),
                PLUGIN_POWER.profile_overrides(data["profiles"][name]))

        raw = configparser.ConfigParser()
        raw.read(self.etc)
        self.assertEqual(raw.get("profile.performance", "unknown_perf_note"), "keep")
        self.assertEqual(raw.get("profile.performance", "cpu_max_policy0"), "1234567")
        self.assertEqual(raw.get("profile.extreme", "vendor_extra"), "keep-extreme")

        merged = configparser.ConfigParser()
        merged.read([self.factory, self.etc])
        PLUGIN_POWER.ensure_extreme_profile(merged)
        self.assertFalse(merged.has_option("profile.extreme", "cpu_max_policy0"))

        factory = PLUGIN_POWER.factory_power_defaults()
        reread["profiles"]["extreme"] = dict(factory["profiles"]["extreme"])
        self.write_etc(PLUGIN_POWER.render_power(reread, factory))
        reread = PLUGIN_POWER.parse_power()
        self.assertEqual(float(reread["profiles"]["extreme"]["cpu_max"]), 1.0)
        self.assertEqual(reread["profiles"]["performance"]["cpu_max"], "0.80")
        self.assertEqual(reread["general"]["default_profile"], "extreme")

        raw = configparser.ConfigParser()
        raw.read(self.etc)
        self.assertEqual(raw.get("profile.performance", "unknown_perf_note"), "keep")
        self.assertEqual(raw.get("profile.performance", "cpu_max_policy0"), "1234567")
        self.assertEqual(raw.get("profile.extreme", "vendor_extra"), "keep-extreme")
        merged = configparser.ConfigParser()
        merged.read([self.factory, self.etc])
        PLUGIN_POWER.ensure_extreme_profile(merged)
        self.assertFalse(merged.has_option("profile.extreme", "cpu_max_policy0"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
