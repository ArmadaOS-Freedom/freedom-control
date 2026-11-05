import test from "node:test";
import assert from "node:assert/strict";
import { normalizeSchedulerSelection } from "../src/lib/schedulerSelection.ts";

test("inherited undefined and null -> empty string", () => {
  assert.equal(normalizeSchedulerSelection(undefined, ["eevdf"]), "");
  assert.equal(normalizeSchedulerSelection(null, ["eevdf"]), "");
  assert.equal(normalizeSchedulerSelection(undefined, []), "");
});

test("standard EEVDF kernel keeps eevdf", () => {
  assert.equal(normalizeSchedulerSelection("eevdf", ["eevdf", "cosmos"]), "eevdf");
  assert.equal(normalizeSchedulerSelection("eevdf", ["eevdf"]), "eevdf");
});

test("migrated eevdf -> infinity when only infinity present", () => {
  assert.equal(normalizeSchedulerSelection("eevdf", ["infinity", "lavd"]), "infinity");
});

test("rollback infinity -> eevdf when only eevdf present", () => {
  assert.equal(normalizeSchedulerSelection("infinity", ["eevdf", "cosmos"]), "eevdf");
});

test("cosmos and lavd are retained unchanged", () => {
  assert.equal(normalizeSchedulerSelection("cosmos", ["eevdf", "cosmos", "lavd"]), "cosmos");
  assert.equal(normalizeSchedulerSelection("lavd", ["infinity", "cosmos", "lavd"]), "lavd");
});

test("empty available is safe and never yields a fake option", () => {
  assert.equal(normalizeSchedulerSelection("eevdf", []), "");
  assert.equal(normalizeSchedulerSelection("infinity", []), "");
  assert.equal(normalizeSchedulerSelection("cosmos", []), "cosmos");
});

test("explicit inherited empty string stays empty (distinct from undefined)", () => {
  // Both map to '', but the helper must accept both without throwing.
  assert.equal(normalizeSchedulerSelection("", ["eevdf"]), "");
  assert.equal(normalizeSchedulerSelection(undefined, ["eevdf"]), "");
});

test("exact match preserved when both bases absent from list", () => {
  // Only cosmos/lavd in list, value is a base -> no base found -> ''
  assert.equal(normalizeSchedulerSelection("eevdf", ["cosmos", "lavd"]), "");
  assert.equal(normalizeSchedulerSelection("infinity", ["cosmos", "lavd"]), "");
});
