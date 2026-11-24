import assert from "node:assert/strict";
import test from "node:test";

import { appliesLive, orientationChoices } from "../src/lib/displayOrientationChoices.ts";
import type { DisplayOrientationState } from "../src/types.ts";

const state = (orientation: DisplayOrientationState["orientation"], autoSupported: boolean): DisplayOrientationState => ({
  supported: true,
  orientation,
  defaultOrientation: "normal",
  autoSupported,
});

test("auto is offered only where the helper reports it", () => {
  assert.deepEqual(orientationChoices(state("left", false)), ["normal", "left", "right", "upsidedown"]);
  assert.deepEqual(orientationChoices(state("left", true)), ["normal", "left", "right", "upsidedown", "auto"]);
});

test("a stored auto stays selectable so it can be changed", () => {
  assert.ok(orientationChoices(state("auto", false)).includes("auto"));
});

test("only choices involving auto apply in the running session", () => {
  assert.equal(appliesLive("", "auto"), true);
  assert.equal(appliesLive("left", "auto"), true);
  assert.equal(appliesLive("auto", "right"), true);
  assert.equal(appliesLive("auto", ""), true);
  assert.equal(appliesLive("", "left"), false);
  assert.equal(appliesLive("left", "right"), false);
  assert.equal(appliesLive("right", ""), false);
});
