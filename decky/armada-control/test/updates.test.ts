import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

test("update contract exposes only Armada Linux package kinds", () => {
  const source = readFileSync(new URL("../py_modules/armada_control/updates.py", import.meta.url), "utf8");
  assert.match(source, /kind not in \("turnip", "compatibility"\)/);
  assert.doesNotMatch(source, /android/i);
});

test("update downloads require HTTPS and reject traversal IDs", () => {
  const source = readFileSync(new URL("../py_modules/armada_control/updates.py", import.meta.url), "utf8");
  assert.match(source, /value not in \("\."\, "\.\."\)/);
  assert.match(source, /url\.startswith\("https:\/\/"\)/);
});

test("Proton catalog uses DroidDeck ARM64 release sources and archive formats", () => {
  const source = readFileSync(new URL("../py_modules/armada_control/updates.py", import.meta.url), "utf8");
  assert.match(source, /\("GE-Proton ARM64", "GloriousEggroll", "proton-ge-custom"/);
  assert.match(source, /\("CachyOS Proton ARM64", "CachyOS", "proton-cachyos"/);
  assert.match(source, /\("DroidDeck Proton ARM64", "Droid-Deck", "DroidDeck-Proton"/);
  assert.match(source, /aarch64\\\.tar/);
  assert.match(source, /arm64\\\.tar/);
  assert.match(source, /_safe_extract_tar/);
  assert.match(source, /toolmanifest\.vdf/);
  assert.match(source, /releases\.atom/);
  assert.match(source, /expanded_assets/);
  assert.match(source, /_release_page_assets/);
});

test("local update management follows Armada system and user component paths", () => {
  const source = readFileSync(new URL("../py_modules/armada_control/updates.py", import.meta.url), "utf8");
  assert.match(source, /\/usr\/share\/armada\/turnip/);
  assert.match(source, /\.local\/share\/armada\/turnip/);
  assert.match(source, /\/usr\/share\/steam\/compatibilitytools\.d/);
  assert.match(source, /\.local\/share\/Steam\/compatibilitytools\.d/);
  assert.match(source, /USER_PREFIX = "user:"/);
});
