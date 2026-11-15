import test from "node:test";
import assert from "node:assert/strict";
import {
  applySystemInfoCpuNameFallback,
  KNOWN_GEN5_CPU_NAME,
  KNOWN_GEN5_SOC_CLASS,
} from "../src/lib/systemInfoPolicy.ts";

const nativeInfo = () => ({
  sCPUName: "Unknown",
  sCPUVendor: "ARM-64",
  nCPUCount: 8,
  nLogicalCPUCount: 8,
  nCPUFrequency: 3200,
  nCPUMaxFrequency: 4320,
  sOSName: "SteamOS",
  sOSVersionId: "3.7",
});

test("SM8850 with native Unknown name shows the known Gen5 CPU", () => {
  const info = nativeInfo();
  const next = applySystemInfoCpuNameFallback(KNOWN_GEN5_SOC_CLASS, info);
  assert.notEqual(next, info);
  assert.equal(next.sCPUName, KNOWN_GEN5_CPU_NAME);
  // The native reply is never mutated.
  assert.equal(info.sCPUName, "Unknown");
});

test("blank and case-insensitive Unknown names are filled for SM8850", () => {
  for (const name of ["", "   ", "unknown", "UNKNOWN", " Unknown "]) {
    const info = { ...nativeInfo(), sCPUName: name };
    const next = applySystemInfoCpuNameFallback(KNOWN_GEN5_SOC_CLASS, info);
    assert.equal(next.sCPUName, KNOWN_GEN5_CPU_NAME, `name ${JSON.stringify(name)}`);
  }
});

test("missing CPU name is filled for SM8850", () => {
  const info: Record<string, unknown> = nativeInfo();
  delete info.sCPUName;
  const next = applySystemInfoCpuNameFallback(KNOWN_GEN5_SOC_CLASS, info);
  assert.equal(next.sCPUName, KNOWN_GEN5_CPU_NAME);
  assert.equal(next.sCPUVendor, "ARM-64");
});

test("every other native field survives the fill unchanged", () => {
  const info = nativeInfo();
  const next = applySystemInfoCpuNameFallback("SM8850", info);
  assert.deepEqual({ ...next, sCPUName: "Unknown" }, info);
  assert.deepEqual(Object.keys(next).sort(), Object.keys(info).sort());
});

test("a valid CPU name is preserved by identity", () => {
  const info = { ...nativeInfo(), sCPUName: "AMD Custom APU 0405" };
  assert.equal(applySystemInfoCpuNameFallback(KNOWN_GEN5_SOC_CLASS, info), info);
  assert.equal(info.sCPUName, "AMD Custom APU 0405");
});

test("other device classes keep Unknown untouched and keep identity", () => {
  for (const soc of ["SM8650", "SM8750", "armada", "", undefined, null, "sm8850"]) {
    const info = nativeInfo();
    assert.equal(applySystemInfoCpuNameFallback(soc, info), info, `class ${String(soc)}`);
    assert.equal(info.sCPUName, "Unknown");
  }
});

test("non-object native replies pass through by identity", () => {
  for (const value of [undefined, null, "Unknown", 42, true]) {
    assert.equal(applySystemInfoCpuNameFallback(KNOWN_GEN5_SOC_CLASS, value), value);
  }
});
