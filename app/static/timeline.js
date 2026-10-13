(() => {
  const T = (fr, en) => Statify.t(fr, en);
  const status = document.getElementById('timeline-status');
  const retry = document.getElementById('timeline-retry');
  const chart = document.getElementById('timeline-chart');
  const names = document.getElementById('timeline-names');
  const plot = document.getElementById('timeline-plot');
  const axis = document.getElementById('timeline-axis');
  const rows = document.getElementById('timeline-rows');
  const releases = new Map();
  const failed = new Set();
  let artists = [];
  let loading = 0;
  let completed = 0;
  let polling;
  const { datePosition, sortArtists, lifeState, mergeReleases } = TimelineCore;

  async function getJson(url, params = {}) {
    return VisualizationSource.request(url, params);
  }

  function addText(parent, tag, value, className = '') {
    const element = document.createElement(tag);
    element.textContent = value;
    if (className) element.className = className;
    parent.appendChild(element);
    return element;
  }

  function openRelease(artist, release) {
    Statify.openDrawer(release.title || T('Sortie sans titre', 'Untitled release'),
      `${artist.name} · Spotify`, content => {
        const labels = { album: 'Album', single: 'Single', compilation: 'Compilation' };
        addText(content, 'p', `${T('Catégorie', 'Category')} : ${labels[release.category]}`);
        addText(content, 'p', `${T('Sortie', 'Release date')} : ${release.date}`);
        const link = addText(content, 'a', T('Ouvrir sur Spotify', 'Open on Spotify'));
        link.href = release.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.className = 'button button-secondary';
      });
  }

  function openArtist(artist) {
    const start = artist.dates?.begin;
    const end = artist.dates?.end;
    Statify.openDrawer(artist.name, T('Artiste', 'Artist'), content => {
      const isGroup = artist.type === 'Group' || artist.type === 'Orchestra' || artist.type === 'Choir';
      addText(content, 'p', `${isGroup ? T('Formation', 'Formed') : T('Naissance / début', 'Birth / start')} : ${start || '—'}`);
      if (end) addText(content, 'p', `${isGroup ? T('Dissolution', 'Disbanded') : T('Décès / fin', 'Death / end')} : ${end}`);
      const match = artist.match_status;
      if (match !== 'confirmed') {
        addText(content, 'p', T('Dates MusicBrainz en attente ou à vérifier.', 'MusicBrainz dates pending or to review.'));
        const graph = addText(content, 'a', T('Vérifier dans le graphe', 'Review in graph'));
        graph.className = 'button button-quiet';
        graph.href = Statify.contextualLink('/graphs', { artist: artist.id });
      }
      const spotifyArtist = addText(content, 'a', T('Voir l’artiste sur Spotify', 'View artist on Spotify'));
      spotifyArtist.className = 'button button-quiet';
      spotifyArtist.href = `https://open.spotify.com/artist/${encodeURIComponent(artist.id)}`;
      spotifyArtist.target = '_blank';
      spotifyArtist.rel = 'noopener noreferrer';
      const list = releases.get(artist.id) || [];
      addText(content, 'h3', T('Sorties Spotify', 'Spotify releases'));
      if (!list.length) addText(content, 'p', releases.has(artist.id) ? T('Aucune sortie trouvée.', 'No releases found.') : T('Chargement en cours.', 'Loading.'));
      for (const release of list.filter(item => datePosition(item.date) !== null).sort((a, b) => a.date.localeCompare(b.date))) {
        const button = addText(content, 'button', `${release.date} · ${release.title} (${release.category})`, 'timeline-list-button');
        button.type = 'button';
        button.addEventListener('click', () => openRelease(artist, release));
      }
    });
  }

  function makeMarker(parent, artist, release, x, index = 0) {
    const marker = addText(parent, 'button', '', `timeline-marker timeline-${release.category}`);
    marker.type = 'button';
    marker.style.left = `${x}px`;
    marker.style.top = `${8 + (index % 3) * 10}px`;
    marker.title = `${artist.name} · ${release.title} · ${release.date} · ${release.category}`;
    marker.setAttribute('aria-label', marker.title);
    marker.addEventListener('click', () => openRelease(artist, release));
  }

  function render() {
    const thisYear = new Date().getFullYear();
    const { dated, undated: missing } = sortArtists(artists);
    names.replaceChildren(); axis.replaceChildren(); rows.replaceChildren();
    chart.hidden = artists.length === 0;
    if (artists.length) {
      let firstYear = thisYear;
      for (const artist of dated) firstYear = Math.min(firstYear, Math.floor(datePosition(artist.dates.begin)));
      for (const artist of artists) for (const release of releases.get(artist.id) || []) {
        const date = datePosition(release.date);
        if (date !== null) firstYear = Math.min(firstYear, Math.floor(date));
      }
      const span = Math.max(thisYear - firstYear, 1);
      const width = Math.max(920, span * 24);
      plot.style.width = `${width}px`;
      const x = date => Math.max(0, Math.min(width, (date - firstYear) / span * width));
      const step = span > 120 ? 20 : span > 55 ? 10 : span > 25 ? 5 : 2;
      const ticks = new Set([firstYear, thisYear]);
      for (let year = Math.ceil(firstYear / step) * step; year < thisYear; year += step) ticks.add(year);
      for (const year of [...ticks].sort((a, b) => a - b)) {
        const tick = addText(axis, 'span', String(year), 'timeline-tick');
        tick.style.left = `${x(year)}px`;
        tick.style.transform = year === firstYear ? 'none' : year === thisYear ? 'translateX(-100%)' : 'translateX(-50%)';
        const line = addText(rows, 'span', '', 'timeline-gridline');
        line.style.left = `${x(year)}px`;
      }
      for (const artist of [...dated, ...missing]) {
        const label = addText(names, 'button', artist.name, 'timeline-name');
        label.type = 'button'; label.title = artist.name;
        label.addEventListener('click', () => openArtist(artist));
        const row = addText(rows, 'div', '', 'timeline-row');
        const start = datePosition(artist.dates?.begin);
        if (start !== null) {
          const end = datePosition(artist.dates.end);
          const state = lifeState(artist.dates);
          const bar = addText(row, 'span', '', `timeline-life ${state === 'ended' ? 'timeline-active-ended' : state === 'active' ? 'timeline-active-life' : 'timeline-unknown-life'}`);
          bar.style.left = `${x(start)}px`;
          bar.style.width = `${Math.max(3, x(end === null ? thisYear : end) - x(start))}px`;
          bar.title = `${artist.name} · ${artist.dates.begin} – ${artist.dates.end || (state === 'active' ? thisYear : '…')}`;
        }
        const overlaps = new Map();
        for (const release of releases.get(artist.id) || []) {
          const date = datePosition(release.date);
          if (date === null || date < firstYear || date > thisYear + 1) continue;
          const px = Math.round(x(date));
          const count = overlaps.get(px) || 0;
          overlaps.set(px, count + 1);
          makeMarker(row, artist, release, px, count);
        }
      }
    }
    const waiting = artists.filter(artist => artist.match_status === 'pending' ||
      (artist.match_status === 'confirmed' && artist.dates && !Object.hasOwn(artist.dates, 'ended'))).length;
    const reviews = artists.filter(artist => artist.match_status === 'ambiguous').length;
    status.textContent = T(
      `${artists.length} artistes · ${completed}/${artists.length} discographies Spotify chargées${loading ? '…' : ''}${failed.size ? ` · ${failed.size} échecs` : ''} · ${waiting} dates en attente · ${reviews} correspondances à vérifier`,
      `${artists.length} artists · ${completed}/${artists.length} Spotify discographies loaded${loading ? '…' : ''}${failed.size ? ` · ${failed.size} failed` : ''} · ${waiting} dates pending · ${reviews} matches to review`);
    retry.hidden = failed.size === 0 || loading > 0;
  }

  async function loadReleases() {
    if (!VisualizationSource.ready || loading > 0) return;
    const generation = VisualizationSource.generation;
    const queue = artists.filter(artist => !releases.has(artist.id));
    let next = 0;
    async function worker() {
      while (next < queue.length && generation === VisualizationSource.generation) {
        const artist = queue[next++];
        loading++;
        render();
        try {
          const pages = [];
          let offset = 0;
          do {
            const data = await getJson(`/timeline/artist/${encodeURIComponent(artist.id)}/releases`, { offset: String(offset) });
            if (generation !== VisualizationSource.generation) return;
            pages.push(data.releases);
            offset = data.next_offset;
          } while (offset !== null);
          releases.set(artist.id, mergeReleases(pages));
          failed.delete(artist.id);
          completed++;
        } catch (error) {
          if (generation !== VisualizationSource.generation || error.name === 'AbortError') return;
          failed.add(artist.id);
        }
        loading--;
        render();
      }
    }
    await Promise.all(Array.from({ length: Math.min(3, queue.length) }, worker));
  }

  async function refreshArtists() {
    const data = await getJson('/timeline/data');
    artists = data.artists;
    render();
    if (!artists.length) status.textContent = T('Aucun artiste dans cette source.', 'No artists in this source.');
    if (polling && !artists.some(artist => artist.match_status === 'pending' ||
        (artist.match_status === 'confirmed' && artist.dates && !Object.hasOwn(artist.dates, 'ended')))) {
      clearInterval(polling);
      polling = undefined;
    }
  }

  async function start() {
    const generation = VisualizationSource.generation;
    status.textContent = T('Chargement des artistes…', 'Loading artists…');
    try {
      await refreshArtists();
      if (generation !== VisualizationSource.generation) return;
      if (artists.some(artist => artist.match_status === 'pending' ||
          (artist.match_status === 'confirmed' && artist.dates && !Object.hasOwn(artist.dates, 'ended')))) {
        polling = setInterval(() => refreshArtists().catch(() => {}), 15000);
      }
      await loadReleases();
    } catch (error) {
      if (generation === VisualizationSource.generation && error.name !== 'AbortError') status.textContent = error.message;
    }
  }
  window.addEventListener('pagehide', () => {
    clearInterval(polling);
    VisualizationSource.controller.abort();
  });
  retry.addEventListener('click', loadReleases);
  VisualizationSource.init(ready => {
    clearInterval(polling);
    polling = undefined;
    releases.clear();
    failed.clear();
    artists = [];
    loading = 0;
    completed = 0;
    render();
    status.textContent = '';
    if (ready) start();
  });
})();
