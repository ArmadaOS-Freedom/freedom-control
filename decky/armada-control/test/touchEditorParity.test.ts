import test from "node:test";
import assert from "node:assert/strict";
import type { Control } from "../src/touch/types.ts";
import * as editorState from "../src/touch/editor-state.ts";

const VIEWPORT = { width: 1000, height: 1000 };

const control = (
  id: string,
  kind: Control["kind"],
  output: string,
  x: number,
  y: number,
  w: number,
  h: number,
): Control => ({ id, kind, output, x, y, w, h });

/** Deliberately uneven face buttons plus unrelated controls. */
const baseLayout = (): Control[] => [
  control("stick-left", "stick", "left", 0.05, 0.62, 0.2, 0.2),
  control("dpad-main", "dpad", "dpad", 0.05, 0.3, 0.18, 0.18),
  control("pad-right", "pad", "right", 0.4, 0.82, 0.16, 0.12),
  control("face-a", "button", "a", 0.74, 0.68, 0.1, 0.1),
  control("face-b", "button", "b", 0.86, 0.62, 0.1, 0.1),
  control("face-x", "button", "x", 0.7, 0.56, 0.12, 0.09),
  control("face-y", "button", "y", 0.84, 0.5, 0.08, 0.11),
  control("shoulder-lb", "button", "lb", 0.2, 0.08, 0.12, 0.06),
];

const centeredCluster = (): Control[] => [
  control("face-a", "button", "a", 0.44, 0.46, 0.1, 0.1),
  control("face-b", "button", "b", 0.56, 0.46, 0.1, 0.1),
  control("face-x", "button", "x", 0.44, 0.58, 0.1, 0.1),
  control("face-y", "button", "y", 0.56, 0.58, 0.1, 0.1),
];

const closeTo = (
  actual: number,
  expected: number,
  message: string,
  epsilon = 1e-9,
): void => {
  assert.ok(
    Math.abs(actual - expected) <= epsilon,
    `${message}: expected ${expected}, got ${actual}`,
  );
};

const isInside = (value: number): boolean => value >= -1e-9 && value <= 1 + 1e-9;

const requireFaceGroup = () => {
  assert.equal(
    typeof editorState.findRightFaceGroup,
    "function",
    "editor-state must export findRightFaceGroup",
  );
  assert.equal(typeof editorState.faceGroupBounds, "function", "editor-state must export faceGroupBounds");
  assert.equal(typeof editorState.faceGroupDiameterPx, "function", "editor-state must export faceGroupDiameterPx");
  assert.equal(typeof editorState.moveFaceGroup, "function", "editor-state must export moveFaceGroup");
  assert.equal(typeof editorState.resizeFaceGroup, "function", "editor-state must export resizeFaceGroup");
};

const groupIds = (layout: readonly Control[]): string[] => {
  requireFaceGroup();
  const lookup = editorState.findRightFaceGroup(layout);
  assert.equal(lookup.status, "ok", "expected a usable face group");
  return lookup.ids;
};

const members = (layout: readonly Control[], ids: readonly string[]): Control[] => {
  const idSet = new Set(ids);
  return layout.filter((item) => idSet.has(item.id));
};

test("locates exactly one a/b/x/y button regardless of ids, labels or extra non-buttons", () => {
  requireFaceGroup();
  const layout = baseLayout();
  layout[4].label = "X 键";
  layout[5].label = "A 键";
  layout.push(control("stick-fake", "stick", "a", 0.9, 0.1, 0.05, 0.05));

  const lookup = editorState.findRightFaceGroup(layout);

  assert.equal(lookup.status, "ok");
  assert.deepEqual(lookup.ids, ["face-a", "face-b", "face-x", "face-y"]);
  assert.equal(lookup.controls.length, 4);
  assert.deepEqual(lookup.missing, []);
  assert.deepEqual(lookup.duplicates, []);
});

test("reports a missing output instead of inventing a button", () => {
  requireFaceGroup();
  const layout = baseLayout().filter((item) => item.output !== "y");
  const lookup = editorState.findRightFaceGroup(layout);

  assert.equal(lookup.status, "missing");
  assert.deepEqual(lookup.missing, ["y"]);
  assert.deepEqual(lookup.ids, ["face-a", "face-b", "face-x"]);
  assert.equal(lookup.controls.length, 3);
});

test("reports duplicated outputs and keeps the first occurrence", () => {
  requireFaceGroup();
  const layout = baseLayout();
  layout.push(control("face-a2", "button", "a", 0.3, 0.9, 0.1, 0.1));

  const lookup = editorState.findRightFaceGroup(layout);

  assert.equal(lookup.status, "duplicate");
  assert.deepEqual(lookup.duplicates, ["a"]);
  assert.deepEqual(lookup.missing, []);
  assert.deepEqual(lookup.ids, ["face-a", "face-b", "face-x", "face-y"]);
});

test("faceGroupBounds returns the axis-aligned union of the group", () => {
  requireFaceGroup();
  const layout = baseLayout();
  const ids = groupIds(layout);
  const group = members(layout, ids);

  const bounds = editorState.faceGroupBounds(group);

  const minX = Math.min(...group.map((c) => c.x));
  const minY = Math.min(...group.map((c) => c.y));
  const maxX = Math.max(...group.map((c) => c.x + c.w));
  const maxY = Math.max(...group.map((c) => c.y + c.h));

  closeTo(bounds.x, minX, "union min x");
  closeTo(bounds.y, minY, "union min y");
  closeTo(bounds.w, maxX - minX, "union width");
  closeTo(bounds.h, maxY - minY, "union height");
});

test("faceGroupBounds returns an empty rect for an empty selection", () => {
  requireFaceGroup();
  const bounds = editorState.faceGroupBounds([]);
  assert.deepEqual(bounds, { x: 0, y: 0, w: 0, h: 0 });
});

test("resizeFaceGroup equalizes physical pixel size from unequal rectangles", () => {
  requireFaceGroup();
  const layout = baseLayout();
  const ids = groupIds(layout);
  const result = editorState.resizeFaceGroup(layout, ids, 160, VIEWPORT);

  assert.equal(result.diameterPx, 160);
  for (const item of members(result.layout, ids)) {
    closeTo(item.w * VIEWPORT.width, 160, `${item.id} physical width`);
    closeTo(item.h * VIEWPORT.height, 160, `${item.id} physical height`);
  }
  const bounds = editorState.faceGroupBounds(members(result.layout, ids));
  assert.ok(isInside(bounds.x) && isInside(bounds.y));
  assert.ok(isInside(bounds.x + bounds.w) && isInside(bounds.y + bounds.h));
});

test("resizeFaceGroup scales member centers around the group center by one factor", () => {
  requireFaceGroup();
  const layout = centeredCluster();
  const ids = groupIds(layout);
  const before = editorState.faceGroupBounds(members(layout, ids));
  const centerX = before.x + before.w / 2;
  const centerY = before.y + before.h / 2;
  const baseCenters = new Map(
    members(layout, ids).map((item) => [
      item.id,
      { x: item.x + item.w / 2, y: item.y + item.h / 2 },
    ]),
  );

  const result = editorState.resizeFaceGroup(layout, ids, 200, VIEWPORT);

  closeTo(result.factor, 2, "uniform scale factor");
  assert.equal(result.diameterPx, 200);
  for (const item of members(result.layout, ids)) {
    const base = baseCenters.get(item.id);
    assert.ok(base, `missing base center for ${item.id}`);
    const expectedX = centerX + (base.x - centerX) * result.factor;
    const expectedY = centerY + (base.y - centerY) * result.factor;
    closeTo(item.x + item.w / 2, expectedX, `${item.id} center x`);
    closeTo(item.y + item.h / 2, expectedY, `${item.id} center y`);
    closeTo(item.w * VIEWPORT.width, 200, `${item.id} width`);
    closeTo(item.h * VIEWPORT.height, 200, `${item.id} height`);
  }
});

test("resizeFaceGroup caps an oversized request by shrinking the diameter, not the pattern", () => {
  requireFaceGroup();
  const viewport = { width: 900, height: 700 };
  const layout = baseLayout();
  const ids = groupIds(layout);
  const before = members(layout, ids);
  const baseDiameter = editorState.faceGroupDiameterPx(before, viewport);
  const baseBounds = editorState.faceGroupBounds(before);
  const groupCenterX = baseBounds.x + baseBounds.w / 2;
  const groupCenterY = baseBounds.y + baseBounds.h / 2;
  const baseCenters = new Map(
    before.map((item) => [
      item.id,
      { x: item.x + item.w / 2, y: item.y + item.h / 2 },
    ]),
  );
  const baseA = baseCenters.get("face-a");
  const baseY = baseCenters.get("face-y");
  assert.ok(baseA && baseY);
  const baseSpacing = Math.hypot(baseA.x - baseY.x, baseA.y - baseY.y);

  const result = editorState.resizeFaceGroup(layout, ids, 5000, viewport);

  assert.ok(
    result.diameterPx < Math.min(viewport.width, viewport.height),
    `oversized request must cap below the short side, got ${result.diameterPx}`,
  );
  assert.ok(
    result.diameterPx >= editorState.MIN_TOUCH_PX,
    `feasible diameter keeps the touch floor, got ${result.diameterPx}`,
  );
  assert.ok(result.factor > 0, "the pattern must not collapse");
  closeTo(
    result.factor,
    result.diameterPx / baseDiameter,
    "one factor drives the diameter and the pattern",
  );

  const scaledCenters = new Map<string, { x: number; y: number }>();
  const resizedCenters = new Map<string, { x: number; y: number }>();
  for (const item of members(result.layout, ids)) {
    const base = baseCenters.get(item.id);
    assert.ok(base, `missing base center for ${item.id}`);
    const centerX = item.x + item.w / 2;
    const centerY = item.y + item.h / 2;
    closeTo(
      item.w * viewport.width,
      result.diameterPx,
      `${item.id} width follows the applied diameter`,
    );
    closeTo(
      item.h * viewport.height,
      result.diameterPx,
      `${item.id} height follows the applied diameter`,
    );
    scaledCenters.set(item.id, {
      x: groupCenterX + (base.x - groupCenterX) * result.factor,
      y: groupCenterY + (base.y - groupCenterY) * result.factor,
    });
    resizedCenters.set(item.id, { x: centerX, y: centerY });
  }

  const anchor = resizedCenters.get(ids[0]);
  const anchorScaled = scaledCenters.get(ids[0]);
  assert.ok(anchor && anchorScaled);
  const shiftX = anchor.x - anchorScaled.x;
  const shiftY = anchor.y - anchorScaled.y;
  for (const item of members(result.layout, ids)) {
    const scaled = scaledCenters.get(item.id);
    const actual = resizedCenters.get(item.id);
    assert.ok(scaled && actual);
    closeTo(
      actual.x,
      scaled.x + shiftX,
      `${item.id} center x follows one factor plus one shared translation`,
    );
    closeTo(
      actual.y,
      scaled.y + shiftY,
      `${item.id} center y follows one factor plus one shared translation`,
    );
  }

  const distinct = new Set(
    [...resizedCenters.values()].map(
      (center) => `${center.x.toFixed(6)},${center.y.toFixed(6)}`,
    ),
  );
  assert.equal(distinct.size, 4, "distinct input centers stay distinct");

  const resizedA = resizedCenters.get("face-a");
  const resizedY = resizedCenters.get("face-y");
  assert.ok(resizedA && resizedY);
  closeTo(
    Math.hypot(resizedA.x - resizedY.x, resizedA.y - resizedY.y),
    baseSpacing * result.factor,
    "relative pattern spacing uses the same factor",
  );

  const bounds = editorState.faceGroupBounds(members(result.layout, ids));
  assert.ok(isInside(bounds.x) && isInside(bounds.y));
  assert.ok(isInside(bounds.x + bounds.w) && isInside(bounds.y + bounds.h));
});

test("resizeFaceGroup enforces the 44 px minimum touch size", () => {
  requireFaceGroup();
  const layout = baseLayout();
  const ids = groupIds(layout);
  const result = editorState.resizeFaceGroup(layout, ids, 10, VIEWPORT);

  assert.equal(result.diameterPx, editorState.MIN_TOUCH_PX);
  for (const item of members(result.layout, ids)) {
    closeTo(item.w * VIEWPORT.width, editorState.MIN_TOUCH_PX, `${item.id} width`);
    closeTo(item.h * VIEWPORT.height, editorState.MIN_TOUCH_PX, `${item.id} height`);
  }
});

test("resizeFaceGroup and moveFaceGroup leave other controls untouched and never mutate the input", () => {
  requireFaceGroup();
  const layout = baseLayout();
  const ids = groupIds(layout);
  const snapshot = JSON.stringify(layout);
  const idSet = new Set(ids);
  const untouched = layout
    .filter((item) => !idSet.has(item.id))
    .map((item) => JSON.stringify(item));

  const result = editorState.resizeFaceGroup(layout, ids, 180, VIEWPORT);

  assert.equal(JSON.stringify(layout), snapshot);
  const resultUntouched = result.layout
    .filter((item) => !idSet.has(item.id))
    .map((item) => JSON.stringify(item));
  assert.deepEqual(resultUntouched, untouched);
  assert.notEqual(result.layout, layout);

  const moved = editorState.moveFaceGroup(layout, ids, 0.01, -0.01);
  assert.equal(JSON.stringify(layout), snapshot);
  assert.deepEqual(
    moved
      .filter((item) => !idSet.has(item.id))
      .map((item) => JSON.stringify(item)),
    untouched,
  );
});

test("moveFaceGroup moves all four members by one clamped delta and preserves size", () => {
  requireFaceGroup();
  const layout = baseLayout();
  const ids = groupIds(layout);
  const before = members(layout, ids);
  const bounds = editorState.faceGroupBounds(before);
  const limitX = 1 - (bounds.x + bounds.w);

  const moved = editorState.moveFaceGroup(layout, ids, 0.5, 0);
  const after = members(moved, ids);

  assert.equal(after.length, 4);
  for (let index = 0; index < before.length; index += 1) {
    closeTo(after[index].x - before[index].x, limitX, "clamped delta x");
    closeTo(after[index].y - before[index].y, 0, "delta y stays zero");
    closeTo(after[index].w, before[index].w, "width unchanged");
    closeTo(after[index].h, before[index].h, "height unchanged");
  }
  const movedBounds = editorState.faceGroupBounds(after);
  assert.ok(isInside(movedBounds.x + movedBounds.w));
});

test("moveFaceGroup clamps negative moves at the left edge", () => {
  requireFaceGroup();
  const layout = centeredCluster().map((item) => ({
    ...item,
    x: item.x - 0.4,
  }));
  const ids = groupIds(layout);
  const before = members(layout, ids);
  const bounds = editorState.faceGroupBounds(before);

  const moved = editorState.moveFaceGroup(layout, ids, -1, 0);
  const after = members(moved, ids);

  for (let index = 0; index < before.length; index += 1) {
    closeTo(after[index].x - before[index].x, -bounds.x, "clamp at left edge");
    closeTo(after[index].y, before[index].y, "delta y stays zero");
  }
  assert.ok(isInside(editorState.faceGroupBounds(after).x));
});

test("faceGroupDiameterPx reports the reference diameter in physical pixels", () => {
  requireFaceGroup();
  const layout = centeredCluster();
  const ids = groupIds(layout);
  closeTo(
    editorState.faceGroupDiameterPx(members(layout, ids), VIEWPORT),
    100,
    "reference diameter",
  );
});

test("moveFaceGroup ignores non-finite deltas and preserves the input array", () => {
  requireFaceGroup();
  const layout = baseLayout();
  const ids = groupIds(layout);
  const snapshot = JSON.stringify(layout);

  const moved = editorState.moveFaceGroup(layout, ids, Number.NaN, Number.POSITIVE_INFINITY);

  assert.equal(JSON.stringify(layout), snapshot);
  assert.notEqual(moved, layout);
  for (let index = 0; index < layout.length; index += 1) {
    assert.equal(moved[index].id, layout[index].id);
    closeTo(moved[index].x, layout[index].x, `${layout[index].id} x unchanged`);
    closeTo(moved[index].y, layout[index].y, `${layout[index].id} y unchanged`);
  }
});
