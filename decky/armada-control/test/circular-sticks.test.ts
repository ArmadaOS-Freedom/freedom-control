import { test } from "node:test";
import assert from "node:assert/strict";
import * as geometry from "../src/touch/editor-state.ts";

type Control = any;
type ViewportSize = { width: number; height: number };

const MIN_TOUCH_PX = 44;
const STICK_KIND = "stick";

const stick = (x: number, y: number, w: number, h: number, id = "s"): Control => ({
  id, kind: STICK_KIND, x, y, w, h,
});
const other = (x: number, y: number, w: number, h: number, id = "o"): Control => ({
  id, kind: "button", x, y, w, h,
});

const viewports: readonly ViewportSize[] = [
  { width: 956, height: 599 },
  { width: 1920, height: 1080 },
  { width: 600, height: 960 },
  { width: 3040, height: 1904 },
];

const defaultLeft = (): Control => stick(0.035, 0.58, 0.17, 0.30, "left");
const defaultRight = (): Control => stick(0.79, 0.24, 0.17, 0.28, "right");

const center = (c: Control) => ({ x: c.x + c.w / 2, y: c.y + c.h / 2 });

for (const vp of viewports) {
  test(`normalizeCircularSticks produces equal pixel w/h and matching diameters at ${vp.width}x${vp.height}`, () => {
    const left = defaultLeft();
    const right = defaultRight();
    const input: Control[] = [left, right, other(0.1, 0.1, 0.2, 0.2, "aux")];
    const snapshot = JSON.parse(JSON.stringify(input));
    const out = geometry.normalizeCircularSticks(input, vp);
    assert.equal(out.length, input.length);

    // Input not mutated.
    assert.deepEqual(input, snapshot);

    const outLeft = out.find((c) => c.id === "left")!;
    const outRight = out.find((c) => c.id === "right")!;

    // Pixel width equals pixel height per stick.
    assert.ok(Math.abs(outLeft.w * vp.width - outLeft.h * vp.height) < 1e-9);
    assert.ok(Math.abs(outRight.w * vp.width - outRight.h * vp.height) < 1e-9);

    // Diameters agreed across the pair.
    assert.ok(Math.abs(outLeft.w * vp.width - outRight.w * vp.width) < 1e-9);
    assert.ok(Math.abs(outLeft.h * vp.height - outRight.h * vp.height) < 1e-9);

    // Centers preserved unless clamped.
    const clampOrPreserve = (orig: Control, res: Control) => {
      const oc = center(orig), rc = center(res);
      const clampedX = res.x <= 0 || res.x + res.w >= 1;
      const clampedY = res.y <= 0 || res.y + res.h >= 1;
      if (!clampedX) assert.ok(Math.abs(rc.x - oc.x) < 1e-9);
      if (!clampedY) assert.ok(Math.abs(rc.y - oc.y) < 1e-9);
    };
    clampOrPreserve(left, outLeft);
    clampOrPreserve(right, outRight);

    // Non-stick unchanged.
    const aux = out.find((c) => c.id === "aux")!;
    assert.deepEqual(aux, input.find((c) => c.id === "aux"));

    // Order preserved.
    assert.deepEqual(out.map((c) => c.id), input.map((c) => c.id));
  });
}

test("normalizeCircularSticks on 1x1 viewport returns unchanged copies", () => {
  const l = defaultLeft();
  const r = defaultRight();
  const input = [l, r];
  const out = geometry.normalizeCircularSticks(input, { width: 1, height: 1 });
  assert.deepEqual(out, input);
  assert.notEqual(out[0], input[0]);
  assert.notEqual(out[1], input[1]);
});

test("normalizeCircularSticks handles non-finite to non-mutation", () => {
  const bad = stick(0.1, 0.1, Number.NaN, 0.3);
  const out = geometry.normalizeCircularSticks([bad], { width: 1024, height: 768 });
  assert.deepEqual(out[0], bad);
});

const resizeVp: ViewportSize = { width: 1000, height: 1000 };

test("resizeCircularStick keeps equal pixel width/height and fixed top-left anchor", () => {
  const start = stick(0.1, 0.2, 0.15, 0.15);
  const r = geometry.resizeCircularStick(start, resizeVp, 50, 0);
  assert.equal(r.x, start.x);
  assert.equal(r.y, start.y);
  assert.ok(Math.abs(r.w * resizeVp.width - r.h * resizeVp.height) < 1e-9);
});

test("resizeCircularStick dominant axis grows and shrinks symmetrically", () => {
  const start = stick(0.1, 0.2, 0.15, 0.15);
  const growX = geometry.resizeCircularStick(start, resizeVp, 100, 10);
  const growY = geometry.resizeCircularStick(start, resizeVp, 10, 100);
  // Same dominant magnitude => same result.
  assert.ok(Math.abs(growX.w - growY.w) < 1e-9);
  assert.ok(Math.abs(growX.h - growY.h) < 1e-9);

  const shrink = geometry.resizeCircularStick(start, resizeVp, -50, -5);
  assert.ok(shrink.w < start.w);
  assert.ok(shrink.h < start.h);
  assert.ok(Math.abs(shrink.w * resizeVp.width - shrink.h * resizeVp.height) < 1e-9);
});

test("resizeCircularStick enforces 44px minimum when available", () => {
  const start = stick(0.1, 0.2, 0.15, 0.15);
  const r = geometry.resizeCircularStick(start, resizeVp, -10000, 0);
  assert.ok(Math.abs(r.w * resizeVp.width - 44) < 1e-9);
  assert.ok(Math.abs(r.h * resizeVp.height - 44) < 1e-9);
});

test("resizeCircularStick minimum shrinks to available when less than 44px", () => {
  // available = min((1-0.95)*1000, (1-0.95)*1000) = 50 ... make it tiny.
  const vp: ViewportSize = { width: 40, height: 40 };
  const start = stick(0.5, 0.5, 0.1, 0.1);
  // available = min(20,20)=20 px
  const r = geometry.resizeCircularStick(start, vp, -1000, 0);
  assert.ok(r.w * vp.width >= 19.999);
  assert.ok(Math.abs(r.w * vp.width - r.h * vp.height) < 1e-9);
});

test("resizeCircularStick rapid series from frozen start shows no drift", () => {
  const start = stick(0.05, 0.1, 0.12, 0.18);
  const a = geometry.resizeCircularStick(start, resizeVp, 30, 0);
  const b = geometry.resizeCircularStick(start, resizeVp, 30, 0);
  const c = geometry.resizeCircularStick(start, resizeVp, 30, 0);
  assert.deepEqual(a, b);
  assert.deepEqual(b, c);
  assert.equal(a.x, start.x);
  assert.equal(a.y, start.y);
});
