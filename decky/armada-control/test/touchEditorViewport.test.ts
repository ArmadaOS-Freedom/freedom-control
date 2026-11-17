// Behavioural geometry checks for the shipped resizeFaceGroup helper, run for
// both display orientations. It is a pure function on normalized rectangles, so
// each case is exercised without the editor runtime.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Buffer } from "node:buffer";
import ts from "typescript";

const compilerOptions = {
  module: ts.ModuleKind.ESNext,
  target: ts.ScriptTarget.ES2020,
};

const readSource = (relativePath) =>
  readFile(new URL(relativePath, import.meta.url), "utf8");

const editorStateJs = ts.transpileModule(
  await readSource("../src/touch/editor-state.ts"),
  { compilerOptions, fileName: "editor-state.ts" },
).outputText;
const editorStateUrl = `data:text/javascript;base64,${Buffer.from(
  editorStateJs,
  "utf8",
).toString("base64")}`;
const {
  MIN_TOUCH_PX,
  resizeFaceGroup,
  faceGroupBounds,
  faceGroupDiameterPx,
} = await import(editorStateUrl);

const PORTRAIT = { width: 1080, height: 1920 };
const LANDSCAPE = { width: 1920, height: 1080 };

// The face cluster uses the physical top/left/right/bottom order the daemon
// reports as y, x, b, a on the right pad.
const FACE_LAYOUT = [
  { id: "y", kind: "button", output: "y", x: 0.76, y: 0.10, w: 0.10, h: 0.06 },
  { id: "x", kind: "button", output: "x", x: 0.70, y: 0.22, w: 0.10, h: 0.06 },
  { id: "b", kind: "button", output: "b", x: 0.82, y: 0.22, w: 0.10, h: 0.06 },
  { id: "a", kind: "button", output: "a", x: 0.76, y: 0.34, w: 0.10, h: 0.06 },
];
const FACE_IDS = FACE_LAYOUT.map((control) => control.id);

const outward = { id: "menu", kind: "button", output: "start", x: 0.02, y: 0.02, w: 0.08, h: 0.05 };
const draft = [...FACE_LAYOUT, outward];

const findById = (layout, id) => layout.find((control) => control.id === id);

const pxSize = (control, viewport) => ({
  w: control.w * viewport.width,
  h: control.h * viewport.height,
});

const facePx = (layout, viewport) =>
  FACE_IDS.map((id) => pxSize(findById(layout, id), viewport));

const assertInside = (layout, viewport, ids, message) => {
  for (const id of ids) {
    const control = findById(layout, id);
    assert.ok(control, `${message}: ${id} present`);
    assert.ok(control.x >= -1e-9, `${message}: ${id} left inside`);
    assert.ok(control.y >= -1e-9, `${message}: ${id} top inside`);
    assert.ok(control.x + control.w <= 1 + 1e-9, `${message}: ${id} right inside`);
    assert.ok(control.y + control.h <= 1 + 1e-9, `${message}: ${id} bottom inside`);
  }
};

const faceCenterPx = (layout, viewport, id) => {
  const control = findById(layout, id);
  return {
    x: (control.x + control.w / 2) * viewport.width,
    y: (control.y + control.h / 2) * viewport.height,
  };
};

// Portrait: resize the faces to one equal physical circle per member.
{
  const result = resizeFaceGroup(draft, FACE_IDS, 90, PORTRAIT);
  assert.equal(result.factor, result.diameterPx / faceGroupDiameterPx(FACE_LAYOUT, PORTRAIT));
  const sizes = facePx(result.layout, PORTRAIT);
  for (const size of sizes) {
    assert.ok(Math.abs(size.w - result.diameterPx) < 1e-6, "portrait width equals diameter");
    assert.ok(Math.abs(size.h - result.diameterPx) < 1e-6, "portrait height equals diameter");
    assert.ok(Math.abs(size.w - size.h) < 1e-6, "portrait circles are square");
  }
  assertInside(result.layout, PORTRAIT, FACE_IDS, "portrait faces inside");
  for (const member of FACE_LAYOUT) {
    assert.equal(
      findById(result.layout, member.id).label,
      member.label,
      "portrait keeps every member label",
    );
  }
  assert.deepEqual(
    findById(result.layout, outward.id),
    outward,
    "portrait leaves non-members untouched",
  );
}

// Landscape: the same request must produce the same physical circle.
{
  const portrait = resizeFaceGroup(draft, FACE_IDS, 90, PORTRAIT);
  const landscape = resizeFaceGroup(draft, FACE_IDS, 90, LANDSCAPE);
  assert.ok(
    Math.abs(portrait.diameterPx - landscape.diameterPx) < 1e-6,
    "portrait and landscape produce the same physical diameter",
  );
  const sizes = facePx(landscape.layout, LANDSCAPE);
  for (const size of sizes) {
    assert.ok(Math.abs(size.w - landscape.diameterPx) < 1e-6, "landscape width matches diameter");
    assert.ok(Math.abs(size.h - landscape.diameterPx) < 1e-6, "landscape height matches diameter");
    assert.ok(Math.abs(size.w - size.h) < 1e-6, "landscape circles are square");
  }
  assertInside(landscape.layout, LANDSCAPE, FACE_IDS, "landscape faces inside");
  assert.deepEqual(
    findById(landscape.layout, outward.id),
    outward,
    "landscape leaves non-members untouched",
  );
}

// Pattern: the cluster is one uniform scale plus a single shared translation.
for (const viewport of [PORTRAIT, LANDSCAPE]) {
  const originalBounds = faceGroupBounds(FACE_LAYOUT);
  const originalCenter = {
    x: originalBounds.x + originalBounds.w / 2,
    y: originalBounds.y + originalBounds.h / 2,
  };
  const result = resizeFaceGroup(draft, FACE_IDS, 120, viewport);
  const factor = result.factor;
  assert.ok(factor > 0, "scale factor is positive");

  // Compare center offsets before and after, allowing for one shared shift.
  const before = FACE_IDS.map((id) => ({
    x: (findById(FACE_LAYOUT, id).x + findById(FACE_LAYOUT, id).w / 2 - originalCenter.x) * viewport.width,
    y: (findById(FACE_LAYOUT, id).y + findById(FACE_LAYOUT, id).h / 2 - originalCenter.y) * viewport.height,
  }));
  const after = FACE_IDS.map((id) => faceCenterPx(result.layout, viewport, id));
  const shiftX = after[0].x - before[0].x * factor;
  const shiftY = after[0].y - before[0].y * factor;
  for (let index = 0; index < FACE_IDS.length; index += 1) {
    assert.ok(
      Math.abs(after[index].x - (before[index].x * factor + shiftX)) < 1e-6,
      "pattern X follows one shared scale and translation",
    );
    assert.ok(
      Math.abs(after[index].y - (before[index].y * factor + shiftY)) < 1e-6,
      "pattern Y follows one shared scale and translation",
    );
  }
  assertInside(result.layout, viewport, FACE_IDS, "resized cluster stays inside");
}

// Floor: within the feasible maximum, a small target is bumped up to 44 px.
for (const viewport of [PORTRAIT, LANDSCAPE]) {
  const result = resizeFaceGroup(draft, FACE_IDS, 1, viewport);
  assert.ok(
    result.diameterPx >= MIN_TOUCH_PX - 1e-6,
    `the 44 px floor applies where the canvas allows it (${viewport.width}x${viewport.height})`,
  );
  const size = pxSize(findById(result.layout, FACE_IDS[0]), viewport);
  assert.ok(Math.abs(size.w - result.diameterPx) < 1e-6);
  assert.ok(Math.abs(size.h - result.diameterPx) < 1e-6);
  assertInside(result.layout, viewport, FACE_IDS, "floored cluster stays inside");
}

// Moving the face group does not touch the outside control.
{
  const moved = (await import(editorStateUrl)).moveFaceGroup(draft, FACE_IDS, 0.02, 0.02);
  assert.deepEqual(moved.find((c) => c.id === outward.id), outward);
}

console.log("touchEditorViewport tests passed: portrait/landscape geometry, uniform pattern, 44 px floor, non-member isolation");
