// Pure helpers for the entity timeline animation page.
// Mirrors frontend/tests/test_timeline_anim.cjs — keep the two in
// lockstep. (The .cjs version is a CommonJS re-implementation so the
// test can run in plain Node without a build pipeline.)

export function entitiesVisibleAt(items, targetDate) {
  if (!items || items.length === 0) return [];
  // targetDate is a Date or ISO string; first_seen is an ISO date
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

export function dateRangeOf(items) {
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
