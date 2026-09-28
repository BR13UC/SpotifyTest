"use strict";

const TimelineCore = (() => {
  function datePosition(value) {
    if (typeof value !== 'string' || !/^\d{4}(?:-\d{2}(?:-\d{2})?)?$/.test(value)) return null;
    const [year, month = 1, day = 1] = value.split('-').map(Number);
    const check = new Date(Date.UTC(year, month - 1, day));
    if (check.getUTCFullYear() !== year || check.getUTCMonth() + 1 !== month || check.getUTCDate() !== day) return null;
    return year + (Date.UTC(year, month - 1, day) - Date.UTC(year, 0, 1)) /
      (Date.UTC(year + 1, 0, 1) - Date.UTC(year, 0, 1));
  }

  function sortArtists(artists) {
    const dated = artists.filter(artist => datePosition(artist.dates?.begin) !== null)
      .sort((a, b) => datePosition(b.dates.begin) - datePosition(a.dates.begin) || a.name.localeCompare(b.name));
    const datedIds = new Set(dated.map(artist => artist.id));
    const undated = artists.filter(artist => !datedIds.has(artist.id)).sort((a, b) => a.name.localeCompare(b.name));
    return { dated, undated };
  }

  function lifeState(dates) {
    if (datePosition(dates?.end) !== null) return 'ended';
    if (dates?.ended === false) return 'active';
    return 'unknown';
  }

  function mergeReleases(pages) {
    const seen = new Set();
    const result = [];
    for (const page of pages) for (const release of page) {
      if (!seen.has(release.id)) { seen.add(release.id); result.push(release); }
    }
    return result;
  }

  return { datePosition, sortArtists, lifeState, mergeReleases };
})();

if (typeof module !== 'undefined') module.exports = TimelineCore;
