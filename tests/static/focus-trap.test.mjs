import assert from "node:assert/strict";
import test from "node:test";

import { nextFocusIndex } from "../../app/static/js/focus-trap.js";

test("cycles forward focus from the dialog last element", () => {
  assert.equal(nextFocusIndex(2, 3), 0);
});

test("cycles backward focus from the dialog first element", () => {
  assert.equal(nextFocusIndex(0, 3, true), 2);
});

test("moves focus in either direction within the dialog", () => {
  assert.equal(nextFocusIndex(1, 3), 2);
  assert.equal(nextFocusIndex(1, 3, true), 0);
});
