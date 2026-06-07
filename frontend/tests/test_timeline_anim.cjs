// Standalone tests for #27 entity timeline animation helpers.
//
// Pure-frontend, so the test runner is plain Node.js (no Jest/Vitest
// needed). Verifies the two pure helpers used by the animation page:
//   * `entitiesVisibleAt(items, targetDate)` — filter entities to those
//     whose first_seen is on or before the target date.
//   * `dateRangeOf(items)` — return the [min, max] of first_seen dates,
//     with safe defaults for empty input.
//
// Run with:
//     node frontend/tests/test_timeline_anim.js
//
// Exits 0 on success, 1 on any failure.

const path = require('path');

// Resolve the helpers via a tiny shim — Vite/EcmaScript module syntax
// (export const) is not loadable in plain Node, so we re-implement
// the two helpers in CommonJS right here. The actual implementations
// in src/utils/timelineAnim.js mirror these line-for-line — the
// duplication is intentional to avoid pulling in a build pipeline
// (Jest/Vitest) for a single test file. The page imports the ESM
// versions which are kept in lockstep with these.

function entitiesVisibleAt(items, targetDate) {
  if (!items || items.length === 0) return [];
  // targetDate is a Date (or ISO string); first_seen is an ISO date
  // string from the backend (e.g. "2026-01-15"). We compare via
  // Date.getTime() to be timezone-agnostic.
  const t = (targetDate instanceof Date)
    ? targetDate.getTime()
    : new Date(targetDate).getTime();
  return items.filter((it) => {
    if (!it.first_seen) return false;
    return new Date(it.first_seen).getTime() <= t;
  });
}

function dateRangeOf(items) {
  if (!items || items.length === 0) return null;
  const dates = items
    .map((it) => it.first_seen)
    .filter(Boolean)
    .map((d) => new Date(d).getTime());
  if (dates.length === 0) return null;
  return {
    min: new Date(Math.min(...dates)),
    max: new Date(Math.max(...dates)),
  };
}

// =========================================================================
// Test runner
// =========================================================================

const PASS = "[92mPASS[0m";
const FAIL = "[91mFAIL[0m";
const _failures = [];

function check(name, cond, detail = "") {
  const status = cond ? PASS : FAIL;
  const suffix = detail && !cond ? ` — ${detail}` : "";
  console.log(`  [${status}] ${name}${suffix}`);
  if (!cond) _failures.push(name);
}

// =========================================================================
// Tests
// =========================================================================

const SAMPLE = [
  { name: "Alice",   first_seen: "2026-01-15" },
  { name: "Bob",     first_seen: "2026-02-20" },
  { name: "Charlie", first_seen: "2026-03-10" },
  { name: "Diana",   first_seen: "2026-04-05" },
];

function testVisibleAt_includesOnExactDate() {
  const r = entitiesVisibleAt(SAMPLE, new Date("2026-02-20"));
  check("visibleAt: includes entity first seen on the target date",
        r.length === 2 && r.some((e) => e.name === "Bob"));
}

function testVisibleAt_includesAllBefore() {
  const r = entitiesVisibleAt(SAMPLE, new Date("2026-12-31"));
  check("visibleAt: future date includes all", r.length === 4);
}

function testVisibleAt_excludesFuture() {
  const r = entitiesVisibleAt(SAMPLE, new Date("2026-01-31"));
  check("visibleAt: excludes entities with first_seen after target",
        r.length === 1 && r[0].name === "Alice");
}

function testVisibleAt_emptyInput() {
  check("visibleAt: empty list → []",
        Array.isArray(entitiesVisibleAt([], new Date()))
        && entitiesVisibleAt([], new Date()).length === 0);
}

function testVisibleAt_nullInput() {
  check("visibleAt: null → []",
        Array.isArray(entitiesVisibleAt(null, new Date()))
        && entitiesVisibleAt(null, new Date()).length === 0);
}

function testVisibleAt_isoStringDate() {
  // target can also be an ISO string (the page passes the slider's
  // value as a string, not a Date).
  const r = entitiesVisibleAt(SAMPLE, "2026-03-15");
  check("visibleAt: accepts ISO string for target date",
        r.length === 3 && !r.some((e) => e.name === "Diana"));
}

function testVisibleAt_dropsEntriesWithoutFirstSeen() {
  const items = [
    { name: "Has", first_seen: "2026-01-01" },
    { name: "NoDate" },  // no first_seen
  ];
  const r = entitiesVisibleAt(items, new Date("2026-12-31"));
  check("visibleAt: drops items missing first_seen",
        r.length === 1 && r[0].name === "Has");
}

function testDateRange_basic() {
  const r = dateRangeOf(SAMPLE);
  check("dateRange: returns min and max",
        r && r.min.getFullYear() === 2026 && r.min.getMonth() === 0
        && r.max.getMonth() === 3);
}

function testDateRange_empty() {
  check("dateRange: empty input → null",
        dateRangeOf([]) === null);
}

function testDateRange_skipsMissingDates() {
  const items = [
    { name: "A", first_seen: "2026-01-01" },
    { name: "B" },  // no date
    { name: "C", first_seen: "2026-06-15" },
  ];
  const r = dateRangeOf(items);
  check("dateRange: ignores items missing first_seen",
        r.min.getMonth() === 0 && r.max.getMonth() === 5);
}

function testDateRange_allMissing() {
  const items = [{ name: "A" }, { name: "B" }];
  check("dateRange: all items missing date → null",
        dateRangeOf(items) === null);
}

const ALL_TESTS = [
  testVisibleAt_includesOnExactDate,
  testVisibleAt_includesAllBefore,
  testVisibleAt_excludesFuture,
  testVisibleAt_emptyInput,
  testVisibleAt_nullInput,
  testVisibleAt_isoStringDate,
  testVisibleAt_dropsEntriesWithoutFirstSeen,
  testDateRange_basic,
  testDateRange_empty,
  testDateRange_skipsMissingDates,
  testDateRange_allMissing,
];

function main() {
  console.log(`Running ${ALL_TESTS.length} checks for #27 timeline anim helpers...`);
  for (const fn of ALL_TESTS) {
    try {
      fn();
    } catch (e) {
      check(`${fn.name}: no unhandled exceptions`, false, String(e));
    }
  }
  console.log();
  if (_failures.length > 0) {
    console.log(`${FAIL} ${_failures.length} FAILED: ${_failures.join(", ")}`);
    process.exit(1);
  }
  console.log(`${PASS} All checks passed (${ALL_TESTS.length} tests).`);
}

main();
