// node tests/fp2-sleep-card.test.js — the card's night math, without a browser.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.join(__dirname, "..", "custom_components", "fp2_sleep", "frontend", "fp2-sleep-card.js"),
  "utf8",
);
const sandbox = { module: { exports: {} }, HTMLElement: class {}, console, Intl, Date, Math, Number, String, Set, Array, Object, JSON, isFinite, isNaN };
vm.runInNewContext(source, sandbox);
const card = sandbox.module.exports;
// Arrays made inside the sandbox belong to another realm.
const plain = (value) => JSON.parse(JSON.stringify(value));

const t = (h, m = 0) => new Date(Date.UTC(2026, 10, h >= 18 ? 18 : 19, h, m));
const night = [
  { time: t(20, 30), stage: "light" },
  { time: t(23, 40), stage: "deep" },
  { time: t(0, 32), stage: "light" },
  { time: t(2, 0), stage: "awake" },
  { time: t(2, 10), stage: "light" },
  { time: t(5, 52), stage: "out_of_bed" },
];

// A finished night.
let r = card.analyzeNight(night, t(7));
assert.equal(r.minutes.deep, 52);
assert.equal(r.minutes.awake, 10);
assert.equal(Math.round(r.asleepHours * 60), 3 * 60 + 10 + 88 + 52 + 222);
assert.equal(r.wakeUps, 1);
assert.equal(r.sleepStart.getTime(), t(20, 30).getTime());
assert.equal(r.sleepEnd.getTime(), t(5, 52).getTime());
assert.equal(r.stillAsleep, false);
assert.ok(r.efficiency > 0.98 && r.efficiency < 1);
assert.equal(r.segments.at(-1).open, true);

// Mid-night: the open segment is not counted and there is no wake time.
r = card.analyzeNight(night.slice(0, 5), t(3));
assert.equal(r.sleepEnd, null);
assert.equal(r.stillAsleep, true);
assert.equal(Math.round(r.minutes.light), 190 + 88);

// Empty night.
r = card.analyzeNight([], t(19));
assert.equal(r.sleepStart, null);
assert.equal(r.efficiency, null);

// Parsing.
assert.equal(card.stageOf("4"), "light");
assert.equal(card.stageOf(5), "deep");
assert.equal(card.stageOf("rem"), "rem");
assert.equal(card.stageOf("unknown"), null);
assert.equal(card.formatDuration(9.367), "9h 22m");
assert.equal(card.formatDuration(0.867), "52m");
assert.equal(card.formatDuration(2), "2h");
assert.equal(card.formatDuration(null), "–");
const changes = card.changesFromAttribute([
  { time: "2026-11-19T04:40:00+00:00", stage: "deep" },
  { time: "2026-11-19T01:30:00+00:00", stage: "light" },
  { time: "bad", stage: "light" },
]);
assert.deepEqual(plain(changes.map((c) => c.stage)), ["light", "deep"]);
const fromHistory = card.changesFromHistory([
  { s: "4", lu: 1763500000 },
  { s: "4", lu: 1763500060 },
  { s: "unavailable", lu: 1763500120 },
  { s: "5", lu: 1763500180 },
]);
assert.deepEqual(plain(fromHistory.map((c) => c.stage)), ["light", "deep"]);
assert.deepEqual(plain(card.numericSeries([{ s: "66", lu: 1 }, { s: "unknown", lu: 2 }, { s: "", lu: 3 }])), [[1000, 66]]);
console.log("fp2-sleep-card tests OK");
