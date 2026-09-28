const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
const T = (fr, en) => Statify.t(fr, en);
const displayTrackName = name => name === 'Titre indisponible' ? T('Titre indisponible', 'Unavailable track') : name;
const familyLabel = family => {
    if (Statify.locale() === 'en') return family || 'Unclassified';
    return ({ Electronic: 'Électronique', Rock: 'Rock', Pop: 'Pop', 'Hip-Hop': 'Hip-hop', Jazz: 'Jazz',
        'Funk / Soul': 'Funk / Soul', Classical: 'Classique', Blues: 'Blues',
        'Folk, World, & Country': 'Folk, musiques du monde et country', Latin: 'Latino',
        Reggae: 'Reggae', 'Brass & Military': 'Cuivres et musique militaire',
        'Stage & Screen': 'Scène et écran', "Children's": 'Musique pour enfants', 'Non-Music': 'Non musical' })[family] || 'Non classé';
};

async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        const error = new Error(Statify.translateError(data.error, data.error_code) || `HTTP ${response.status}`);
        error.details = data;
        throw error;
    }
    return data;
}

function postJson(url, body) {
    return fetchJson(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
        body: JSON.stringify(body),
    });
}

function showMessage(element, message) {
    element.textContent = message;
}

function appendText(parent, tag, value) {
    const element = document.createElement(tag);
    element.textContent = value;
    parent.appendChild(element);
    return element;
}

function sameArtistName(left, right) {
    const normalize = value => (value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase().trim();
    return normalize(left) === normalize(right);
}

function renderMatch(parent, match, label) {
    if (!match) return;
    const box = document.createElement('div');
    box.className = 'mb-match';
    const status = { confirmed: T('confirmé', 'confirmed'), ambiguous: T('à vérifier', 'to review'), not_found: T('introuvable', 'not found') }[match.status] || T('en attente', 'pending');
    appendText(box, 'strong', `${label} — ${status}`);
    const method = { spotify_url: T('lien Spotify vérifié', 'verified Spotify link'), albums: T('deux albums concordants', 'two matching albums'), manual: T('choix manuel', 'manual choice'),
        isrc: T('titre et artiste uniques via ISRC', 'unique track and artist via ISRC'), name: T('titre et artiste uniques', 'unique track and artist'), album: T('titre sur un album', 'track on an album'),
        ep: T('titre sur un EP', 'track on an EP'), contribution: T('participation créditée', 'credited contribution'), score: T('score de confiance', 'confidence score') }[match.match_method];
    if (method) appendText(box, 'p', T(`Correspondance : ${method}.`, `Match: ${method}.`));
    if (match.match_evidence?.common_albums?.length) appendText(box, 'p', T(`Albums communs : ${match.match_evidence.common_albums.join(', ')}`, `Albums in common: ${match.match_evidence.common_albums.join(', ')}`));
    if (match.match_evidence?.release) appendText(box, 'p', T(`Sortie concordante : ${match.match_evidence.release}.`, `Matching release: ${match.match_evidence.release}.`));
    if (match.match_evidence?.spotify_url) appendText(box, 'p', T(`Lien confirmé : ${match.match_evidence.spotify_url}`, `Confirmed link: ${match.match_evidence.spotify_url}`));
    if (match.match_evidence?.ranked?.length) {
        const ranked = match.match_evidence.ranked;
        appendText(box, 'p', T(`Score : ${ranked[0].score}/100${ranked.length > 1 ? ` · deuxième : ${ranked[1].score}/100` : ''}.`, `Score: ${ranked[0].score}/100${ranked.length > 1 ? ` · second: ${ranked[1].score}/100` : ''}.`));
    }
    const data = match.enriched;
    if (data) {
        if (data.dates?.begin) appendText(box, 'p', T(`Début : ${data.dates.begin} (MusicBrainz)`, `Start: ${data.dates.begin} (MusicBrainz)`));
        if (data.first_release_date) appendText(box, 'p', T(`Première sortie : ${data.first_release_date} (MusicBrainz)`, `First release: ${data.first_release_date} (MusicBrainz)`));
        if (data.origin) appendText(box, 'p', T(`${data.origin.type === 'birth' ? 'Naissance' : 'Formation'} : ${data.origin.name} (MusicBrainz, lieu déclaré)`, `${data.origin.type === 'birth' ? 'Birth' : 'Formation'}: ${data.origin.name} (MusicBrainz, reported place)`));
        if (data.genres?.length) appendText(box, 'p', T(`Genres MusicBrainz : ${data.genres.map(genre => genre.name).join(', ')}`, `MusicBrainz genres: ${data.genres.map(genre => genre.name).join(', ')}`));
    }
    if (match.mbid) appendText(box, 'p', `MBID : ${match.mbid}`);
    if (match.candidates?.length || match.status === 'ambiguous') {
        const comparison = document.createElement('div');
        comparison.className = 'mb-comparison';
        const spotify = document.createElement('section');
        const choices = document.createElement('section');
        spotify.className = 'mb-side';
        choices.className = 'mb-side';
        appendText(spotify, 'h4', 'Spotify');
        appendText(spotify, 'strong', match.spotify.name || label);
        if (match.kind === 'artist') {
            if (match.spotify.genres?.length) appendText(spotify, 'p', T(`Genres : ${match.spotify.genres.join(', ')}`, `Genres: ${match.spotify.genres.join(', ')}`));
            const link = document.createElement('a');
            link.href = `https://open.spotify.com/artist/${encodeURIComponent(match.spotify_id)}`;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = T('Ouvrir sur Spotify', 'Open on Spotify');
            spotify.appendChild(link);
        } else {
            appendText(spotify, 'p', T(`Artistes : ${(match.spotify.artists || []).map(artist => artist.name).join(', ') || 'inconnus'}`, `Artists: ${(match.spotify.artists || []).map(artist => artist.name).join(', ') || 'unknown'}`));
            if (match.spotify.album_name) appendText(spotify, 'p', T(`Sortie : ${match.spotify.album_name}${match.spotify.release_date ? ` (${match.spotify.release_date})` : ''}`, `Release: ${match.spotify.album_name}${match.spotify.release_date ? ` (${match.spotify.release_date})` : ''}`));
            if (match.spotify.duration_ms) appendText(spotify, 'p', T(`Durée : ${formatDuration(match.spotify.duration_ms)}`, `Length: ${formatDuration(match.spotify.duration_ms)}`));
            if (match.spotify.isrc) appendText(spotify, 'p', `ISRC : ${match.spotify.isrc}`);
        }
        appendText(choices, 'h4', 'MusicBrainz');
        const candidates = [...(match.candidates || [])];
        if (match.kind === 'recording' && match.match_evidence?.ranked) {
            const scores = new Map(match.match_evidence.ranked.map(item => [item.id, item.score]));
            candidates.sort((a, b) => (scores.get(b.id) || 0) - (scores.get(a.id) || 0));
        }
        const exact = candidates.filter(candidate => sameArtistName(candidate.name, match.spotify.name));
        const others = candidates.filter(candidate => !sameArtistName(candidate.name, match.spotify.name));
        const message = appendText(box, 'p', '');
        const renderCandidate = (target, candidate) => {
            const card = document.createElement('div');
            card.className = 'mb-candidate';
            appendText(card, 'strong', candidate.name);
            const context = [candidate.disambiguation, candidate.type, candidate.area].filter(Boolean).join(' · ');
            if (context) appendText(card, 'p', context);
            if (match.kind === 'recording') {
                if (candidate.artist_names?.length) appendText(card, 'p', T(`Artistes : ${candidate.artist_names.join(', ')}`, `Artists: ${candidate.artist_names.join(', ')}`));
                if (candidate.length) appendText(card, 'p', T(`Durée : ${formatDuration(candidate.length)}`, `Length: ${formatDuration(candidate.length)}`));
                const assessment = match.match_evidence?.ranked?.find(item => item.id === candidate.id);
                const signalNames = { titre: 'title', 'artiste crédité': 'credited artist', durée: 'length', 'année de sortie': 'release year' };
                const signals = assessment?.signals?.map(signal => Statify.locale() === 'en' ? (signal.startsWith('sortie : ') ? `release: ${signal.slice(9)}` : signalNames[signal] || signal) : signal);
                if (assessment) appendText(card, 'p', `${assessment.score}/100${signals?.length ? ` · ${signals.join(', ')}` : ''}${assessment.conflict ? T(' · conflit détecté', ' · conflict detected') : ''}`);
            }
            const link = document.createElement('a');
            link.href = `https://musicbrainz.org/${match.kind}/${encodeURIComponent(candidate.id)}`;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = T('Voir la fiche', 'View profile');
            card.appendChild(link);
            if (match.kind === 'artist') {
                const albums = document.createElement('button');
                albums.type = 'button';
                albums.textContent = T('Comparer les albums', 'Compare albums');
                const result = appendText(card, 'div', '');
                albums.addEventListener('click', async () => {
                    albums.disabled = true;
                    result.textContent = T('Chargement des albums…', 'Loading albums…');
                    try {
                        const data = await fetchJson(`/musicbrainz/comparison/artist/${encodeURIComponent(match.spotify_id)}/${encodeURIComponent(candidate.id)}`);
                        result.replaceChildren();
                        appendText(result, 'p', T(`Albums communs : ${data.common_albums.join(', ') || 'aucun'}`, `Albums in common: ${data.common_albums.join(', ') || 'none'}`));
                        appendText(result, 'p', `Spotify : ${data.spotify_albums.join(', ') || T('aucun album disponible', 'no albums available')}`);
                        appendText(result, 'p', `MusicBrainz : ${data.musicbrainz_albums.join(', ') || T('aucun album disponible', 'no albums available')}`);
                    } catch (error) { result.textContent = error.message; }
                    albums.disabled = false;
                });
                card.appendChild(albums);
            }
            const select = document.createElement('button');
            select.type = 'button';
            select.textContent = match.mbid === candidate.id ? T('Choix actuel', 'Current choice') : T('C’est le bon', 'Choose this one');
            select.addEventListener('click', () => saveMatch(match, candidate.id, message));
            card.appendChild(select);
            target.appendChild(card);
        };
        exact.forEach(candidate => renderCandidate(choices, candidate));
        if (others.length) {
            const more = document.createElement('details');
            const summary = document.createElement('summary');
            summary.textContent = T(`Autres candidats (${others.length})`, `Other candidates (${others.length})`);
            more.appendChild(summary);
            more.addEventListener('toggle', () => {
                if (more.open && !more.dataset.rendered) {
                    others.forEach(candidate => renderCandidate(more, candidate));
                    more.dataset.rendered = 'true';
                }
            });
            choices.appendChild(more);
        }
        comparison.append(spotify, choices);
        box.appendChild(comparison);
    }
    const form = document.createElement('form');
    form.className = 'mb-choice';
    const input = document.createElement('input');
    input.placeholder = T('MBID ou URL MusicBrainz', 'MBID or MusicBrainz URL');
    input.setAttribute('aria-label', T(`Autre MBID pour ${label}`, `Other MBID for ${label}`));
    const button = document.createElement('button');
    button.type = 'submit';
    button.textContent = T('Confirmer', 'Confirm');
    const none = document.createElement('button');
    none.type = 'button';
    none.textContent = T('Aucun candidat', 'No candidate');
    const message = appendText(form, 'span', '');
    form.prepend(input, button, none);
    form.addEventListener('submit', async event => {
        event.preventDefault();
        const raw = input.value.trim();
        const mbid = raw ? raw.split('/').filter(Boolean).pop() : '';
        if (!mbid) { showMessage(message, T('Choisissez un candidat ou saisissez un MBID.', 'Choose a candidate or enter an MBID.')); return; }
        await saveMatch(match, mbid, message);
    });
    none.addEventListener('click', () => saveMatch(match, null, message));
    box.appendChild(form);
    parent.appendChild(box);
}

function formatDuration(milliseconds) {
    const seconds = Math.round(milliseconds / 1000);
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

async function saveMatch(match, mbid, message) {
    try {
        await postJson(`/musicbrainz/match/${encodeURIComponent(match.kind)}/${encodeURIComponent(match.spotify_id)}`, { mbid });
        showMessage(message, T('Choix enregistré. Les détails seront actualisés prochainement.', 'Choice saved. Details will update shortly.'));
        await MusicBrainzPanel.refresh();
        document.dispatchEvent(new CustomEvent('musicbrainz-match-saved'));
    } catch (error) {
        showMessage(message, error.message);
    }
}

const MusicBrainzPanel = {
    previousRevision: undefined,
    onChange: null,
    async refresh() {
        try {
            const data = await fetchJson('/musicbrainz/status');
            const counts = data.counts;
            if (counts.revision !== this.previousRevision) {
                this.previousRevision = counts.revision;
                if (this.onChange) this.onChange(data);
            }
        } catch (error) {
            // Page-specific requests show their own errors; retry on the next poll.
        }
    },
    start(onChange) {
        this.onChange = onChange;
        this.refresh();
        window.setInterval(() => this.refresh(), 8000);
    },
};

async function loadProfile() {
    const target = document.querySelector('.profile');
    if (!target) return;
    try {
        const profile = await fetchJson('/users/get_profile');
        target.replaceChildren();
        const imageUrl = profile.images?.[0]?.url;
        if (imageUrl) {
            const image = document.createElement('img');
            image.src = imageUrl;
            image.alt = '';
            image.width = 40;
            image.height = 40;
            target.appendChild(image);
        }
        const label = document.createElement('span');
        label.textContent = profile.display_name || profile.id || T('Mon compte', 'My account');
        target.appendChild(label);
    } catch (error) {
        showMessage(target, error.message);
    }
}

const SortsPage = {
    currentItems: [],
    selectedKey: null,
    async init() {
        const playlistSelect = document.getElementById('playlist-select');
        const sortSelect = document.getElementById('sort-options');
        const status = document.getElementById('sort-status');
        const createButton = document.getElementById('create-playlist-btn');
        document.getElementById('start-sort-btn').addEventListener('click', () => this.sort());
        createButton.addEventListener('click', () => this.createPlaylist());
        playlistSelect.addEventListener('change', () => {
            Statify.remember('playlist', playlistSelect.value);
            this.currentItems = [];
            createButton.disabled = true;
            document.getElementById('track-titles').replaceChildren();
            showMessage(status, T('Choisissez un tri pour afficher les titres.', 'Choose a sort option to display tracks.'));
            const name = playlistSelect.selectedOptions[0]?.textContent || 'Playlist';
            document.getElementById('playlist-name').value = `${name} — ${T('triée', 'sorted')}`;
        });
        sortSelect.addEventListener('change', () => {
            createButton.disabled = true;
            this.currentItems = [];
            document.getElementById('track-titles').replaceChildren();
            showMessage(status, T('Affichez le nouveau tri pour voir les titres.', 'Show the new sort to see tracks.'));
        });
        MusicBrainzPanel.start(() => { if (this.currentItems.length) this.sort(true); });
        try {
            const [playlists, options] = await Promise.all([
                fetchJson('/playlists/get_playlists'), fetchJson('/sorts/get_sort_options'),
            ]);
            for (const playlist of playlists.playlists) {
                const option = new Option(playlist.name, playlist.id);
                playlistSelect.add(option);
            }
            const fields = { name: T('Titre', 'Title'), artist: T('Artiste principal', 'Main artist'),
                album: T('Album', 'Album'), release_date: T('Date de sortie', 'Release date'),
                duration_ms: T('Durée', 'Duration'), added_at: T("Date d'ajout", 'Date added') };
            for (const item of options) {
                const direction = item.id.endsWith('_asc') ? T('croissant', 'ascending') : T('décroissant', 'descending');
                const field = item.id.replace(/_(asc|desc)$/, '');
                sortSelect.add(new Option(`${fields[field] || item.name} (${direction})`, item.id));
            }
            const requested = new URLSearchParams(location.search).get('playlist') || Statify.recall('playlist');
            if (requested && [...playlistSelect.options].some(option => option.value === requested)) playlistSelect.value = requested;
            if (playlists.total) {
                playlistSelect.dispatchEvent(new Event('change'));
                if (new URLSearchParams(location.search).has('playlist')) this.sort();
            } else {
                showMessage(status, T('Aucune playlist trouvée.', 'No playlists found.'));
            }
        } catch (error) {
            showMessage(status, error.message);
        }
    },
    async sort(refresh = false) {
        const playlistId = document.getElementById('playlist-select').value;
        const sortOption = document.getElementById('sort-options').value;
        const status = document.getElementById('sort-status');
        const container = document.getElementById('track-titles');
        const createButton = document.getElementById('create-playlist-btn');
        if (!playlistId || !sortOption) return;
        createButton.disabled = true;
        if (!refresh) showMessage(status, T('Chargement et tri des titres…', 'Loading and sorting tracks…'));
        try {
            const result = await postJson('/sorts/sort_playlist_tracks', {
                playlist_id: playlistId, sort_option: sortOption,
            });
            if (playlistId !== document.getElementById('playlist-select').value ||
                sortOption !== document.getElementById('sort-options').value) return;
            this.currentItems = result.tracks;
            container.replaceChildren();
            const heading = document.createElement('div');
            heading.className = 'track-table-head';
            [T('N°', 'No.'), T('Titre', 'Title'), T('Artiste', 'Artist'), T('Album', 'Album'),
                T('Durée', 'Length'), T('Sortie', 'Release')].forEach(label => appendText(heading, 'span', label));
            container.appendChild(heading);
            result.tracks.forEach((item, index) => {
                const row = document.createElement('button');
                row.type = 'button'; row.className = 'track-row';
                const key = item.spotify_id || String(item.position);
                row.dataset.trackKey = key;
                row.setAttribute('aria-label', `${displayTrackName(item.name)} — ${item.artist_names.join(', ')}`);
                appendText(row, 'span', String(index + 1)).className = 'track-muted';
                appendText(row, 'span', displayTrackName(item.name)).className = item.exportable ? 'track-title' : 'track-title track-warning';
                appendText(row, 'span', item.artist_names.join(', ') || '—');
                appendText(row, 'span', item.album_name || '—').className = 'track-muted';
                appendText(row, 'span', item.duration_ms ? formatDuration(item.duration_ms) : '—').className = 'track-muted';
                appendText(row, 'span', item.release_date || '—').className = 'track-muted';
                row.addEventListener('click', () => this.showTrack(item, row));
                container.appendChild(row);
            });
            const remembered = new URLSearchParams(location.search).get('track') || Statify.recall('track');
            const target = [...container.querySelectorAll('.track-row')].find(row => row.dataset.trackKey === remembered);
            if (target && new URLSearchParams(location.search).has('track')) target.click();
            showMessage(status, result.tracks.length
                ? T(`${result.tracks.length} éléments, dont ${result.skipped_count} ne pourront pas être ajoutés à la nouvelle playlist.`, `${result.tracks.length} items; ${result.skipped_count} cannot be added to the new playlist.`)
                : T('Cette playlist est vide.', 'This playlist is empty.'));
            createButton.disabled = result.tracks.length === result.skipped_count;
        } catch (error) {
            this.currentItems = [];
            container.replaceChildren();
            showMessage(status, error.message);
        }
    },
    showTrack(item, row) {
        document.querySelectorAll('.track-row').forEach(button => button.classList.toggle('is-selected', button === row));
        document.querySelectorAll('.track-row').forEach(button => button.setAttribute('aria-pressed', String(button === row)));
        const key = item.spotify_id || String(item.position);
        Statify.remember('track', key);
        const enrichment = item.musicbrainz;
        Statify.openDrawer(displayTrackName(item.name), T('Titre de playlist', 'Playlist track'), content => {
            const add = (label, value) => { if (value) appendText(content, 'p', `${label} : ${value}`); };
            add(T('Artistes', 'Artists'), item.artist_names.join(', '));
            add(T('Album', 'Album'), item.album_name);
            add(T('Sortie', 'Release'), item.release_date);
            add(T('Durée', 'Length'), item.duration_ms ? formatDuration(item.duration_ms) : null);
            add(T("Date d’ajout", 'Date added'), item.added_at);
            if (!item.exportable) appendText(content, 'p', T('Ce titre ne peut pas être ajouté à une playlist Spotify.', 'This item cannot be added to a Spotify playlist.'));
            if (item.spotify_id) {
                const link = appendText(content, 'a', T('Ouvrir dans Spotify', 'Open in Spotify'));
                link.className = 'button button-secondary'; link.href = `https://open.spotify.com/track/${encodeURIComponent(item.spotify_id)}`;
                link.target = '_blank'; link.rel = 'noopener noreferrer';
            }
            for (const artist of item.artists || []) {
                const link = appendText(content, 'a', T(`Voir ${artist.name} dans le graphe`, `View ${artist.name} in the graph`));
                link.className = 'button button-quiet'; link.href = Statify.contextualLink('/graphs', { artist: artist.id });
            }
            if (item.release_date && /^\d{4}/.test(item.release_date)) {
                const link = appendText(content, 'a', T('Voir cette année dans le graphe', 'View this year in the graph'));
                link.className = 'button button-quiet';
                link.href = Statify.contextualLink('/graphs', { mode: 'release_years', playlist: document.getElementById('playlist-select').value, year: item.release_date.slice(0, 4) });
            }
            if (enrichment?.genres?.length) {
                appendText(content, 'h3', T('Genres et sources', 'Genres and sources'));
                const list = appendText(content, 'ul', '');
                enrichment.genres.forEach(genre => appendText(list, 'li', `${genre.name} — ${genre.origins.map(origin => origin.source).join(' + ')}`));
            }
            if (enrichment) {
                for (const match of [enrichment.recording, ...enrichment.artists]) {
                    if (match) renderMatch(content, match, match.spotify.name || item.name);
                }
            }
        });
    },
    async createPlaylist() {
        const button = document.getElementById('create-playlist-btn');
        const status = document.getElementById('sort-status');
        button.disabled = true;
        showMessage(status, T('Création de la playlist privée…', 'Creating private playlist…'));
        try {
            const result = await postJson('/playlists/create_sorted_playlist', {
                playlist_id: document.getElementById('playlist-select').value,
                sort_option: document.getElementById('sort-options').value,
                playlist_name: document.getElementById('playlist-name').value,
            });
            status.replaceChildren();
            const summary = document.createElement('span');
            summary.textContent = T(`Playlist privée créée : ${result.created_count} titres ajoutés, ${result.skipped_count} ignorés. `, `Private playlist created: ${result.created_count} tracks added, ${result.skipped_count} skipped. `);
            status.appendChild(summary);
            if (result.url) {
                const link = document.createElement('a');
                link.href = result.url;
                link.textContent = T('Ouvrir dans Spotify', 'Open in Spotify');
                link.rel = 'noopener noreferrer';
                link.target = '_blank';
                status.appendChild(link);
            }
        } catch (error) {
            if (error.details?.url) {
                status.replaceChildren();
                const message = document.createElement('span');
                message.textContent = T(`${error.message} ${error.details.created_count} titres ajoutés. `, `${error.message} ${error.details.created_count} tracks added. `);
                const link = document.createElement('a');
                link.href = error.details.url;
                link.textContent = T('Voir la playlist incomplète', 'View incomplete playlist');
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                status.append(message, link);
            } else {
                showMessage(status, error.message);
            }
            button.disabled = false;
        }
    },
};

const GraphPage = {
    data: null,
    likedCounts: null,
    maxLikedCount: 1,
    maxGenreCount: 1,
    likesToken: 0,
    network: null,
    nodeSet: null,
    edgeSet: null,
    renderedNodes: new Map(),
    renderedEdges: new Map(),
    nodeMap: new Map(),
    genreEdges: new Map(),
    initialPositions: new Map(),
    layoutTimer: null,
    cameraInitialized: false,
    loading: false,
    selectionToken: 0,
    apiMs: null,
    paintMs: null,
    interactionP95: null,
    paintToken: 0,
    setLoading(stage, active) {
        document.getElementById(stage === 'likes' ? 'graph-likes-loading' : 'graph-loading').hidden = !active;
        if (stage === 'graph') document.getElementById('graph-container').setAttribute('aria-busy', String(active));
    },
    async init() {
        const status = document.getElementById('graph-status');
        document.getElementById('create-graph-btn').addEventListener('click', () => this.loadGraph(false, true));
        document.getElementById('graph-overview-btn').addEventListener('click', () => this.network?.fit({ animation: false }));
        document.getElementById('export-graphml').addEventListener('click', () => {
            window.location.href = '/graphs/export_graph/graphml';
        });
        document.getElementById('graph-genre').addEventListener('change', () => {
            document.getElementById('graph-select-genre').disabled = !document.getElementById('graph-genre').value;
            this.render();
        });
        document.getElementById('graph-select-genre').addEventListener('click', () => {
            const id = document.getElementById('graph-genre').value;
            if (id) this.selectNode(id);
        });
        let searchTimer;
        document.getElementById('graph-search').addEventListener('input', () => {
            window.clearTimeout(searchTimer);
            searchTimer = window.setTimeout(() => this.render(), 150);
        });
        document.getElementById('graph-search').addEventListener('keydown', event => {
            if (event.key !== 'Enter') return;
            const artist = this.filtered().artists[0];
            if (artist) { event.preventDefault(); this.selectNode(artist.id); }
        });
        MusicBrainzPanel.start(({ counts }) => {
            if (this.data && counts.artist_revision !== this.data.revision) this.loadGraph(true);
        });
        try {
            this.setLoading('graph', true);
            showMessage(status, T('Chargement des artistes suivis…', 'Loading followed artists…'));
            const artists = await fetchJson('/users/get_followed_artists');
            showMessage(status, T(`${artists.total} artistes suivis. Préparation du graphe…`, `${artists.total} followed artists. Preparing graph…`));
            await this.loadGraph();
        } catch (error) {
            showMessage(status, error.message);
        } finally {
            this.setLoading('graph', false);
        }
    },
    async loadGraph(refresh = false, refreshLikes = false) {
        if (this.loading) return;
        this.loading = true;
        this.setLoading('graph', true);
        const status = document.getElementById('graph-status');
        if (!refresh) showMessage(status, T('Chargement du graphe…', 'Loading graph…'));
        const started = performance.now();
        try {
            const data = await fetchJson('/graphs/get_artist_genre_graph_data');
            const apiMs = performance.now() - started;
            this.apiMs = apiMs;
            this.loadStartedAt = started;
            this.data = data;
            this.nodeMap = new Map(data.nodes.map(node => [node.id, node]));
            this.maxGenreCount = Math.max(1, ...data.nodes.filter(node => node.group === 'genre').map(node => node.artist_count || 0));
            this.genreEdges = new Map();
            for (const edge of data.edges) {
                const genreId = edge.from.startsWith('genre:') ? edge.from : edge.to;
                if (!this.genreEdges.has(genreId)) this.genreEdges.set(genreId, []);
                this.genreEdges.get(genreId).push(edge);
            }
            this.initialPositions = this.layoutPositions(data.nodes, data.edges);
            const genreSelect = document.getElementById('graph-genre');
            const selectedGenre = genreSelect.value;
            genreSelect.replaceChildren(new Option(T('Tous les genres', 'All genres'), ''));
            for (const genre of data.nodes.filter(node => node.group === 'genre').sort((a, b) => a.label.localeCompare(b.label, 'fr'))) {
                genreSelect.add(new Option(genre.label, genre.id));
            }
            genreSelect.value = selectedGenre;
            document.getElementById('graph-select-genre').disabled = !genreSelect.value;
            this.renderLegend();
            this.render();
            const requestedArtist = new URLSearchParams(location.search).get('artist') || Statify.recall('artist');
            const requestedGenre = new URLSearchParams(location.search).get('genre');
            const requested = requestedArtist ? `artist:${requestedArtist}` : requestedGenre ? `genre:${requestedGenre}` : null;
            if (requested && this.nodeMap.has(requested) && !this.selectedNodeId) this.selectNode(requested);
            showMessage(status, data.nodes.length
                ? T(`${data.nodes.filter(node => node.group === 'artist').length} artistes · ${this.genreEdges.size} genres · ${data.edges.length} liens${refresh ? ' · données actualisées' : ''}.${this.networkUnavailable ? ' Moteur du graphe indisponible.' : ''}`, `${data.nodes.filter(node => node.group === 'artist').length} artists · ${this.genreEdges.size} genres · ${data.edges.length} links${refresh ? ' · updated' : ''}.${this.networkUnavailable ? ' Graph engine unavailable.' : ''}`)
                : T('Aucun artiste suivi à afficher.', 'No followed artists to show.'));
            if (data.nodes.length) this.loadLikedCounts(refreshLikes);
        } catch (error) {
            showMessage(status, error.message);
        } finally {
            this.loading = false;
            this.setLoading('graph', false);
        }
    },
    async loadLikedCounts(force = false) {
        const token = ++this.likesToken;
        const status = document.getElementById('graph-likes-status');
        this.setLoading('likes', true);
        showMessage(status, T('Chargement des titres likés…', 'Loading liked tracks…'));
        try {
            const result = await fetchJson(`/graphs/liked_artist_counts${force ? '?refresh=1' : ''}`);
            if (token !== this.likesToken) return;
            this.likedCounts = result.counts;
            this.maxLikedCount = Math.max(1, ...this.data.nodes.filter(node => node.group === 'artist').map(node => result.counts[node.id.slice(7)] || 0));
            this.render();
            this.updateSelectedCount();
            showMessage(status, T('Taille des artistes actualisée selon vos titres likés.', 'Artist sizes updated using liked tracks.'));
        } catch (error) {
            if (token !== this.likesToken) return;
            this.likedCounts = null;
            this.maxLikedCount = 1;
            this.render();
            this.updateSelectedCount();
            showMessage(status, T(`Titres likés indisponibles : ${error.message}`, `Liked tracks unavailable: ${error.message}`));
        } finally {
            if (token === this.likesToken) this.setLoading('likes', false);
        }
    },
    renderLegend() {
        const target = document.getElementById('graph-family-legend');
        target.replaceChildren();
        const families = new Map();
        for (const node of this.data.nodes) {
            if (node.group === 'genre') families.set(node.family || 'Non classé', node);
        }
        const list = document.createElement('ul');
        for (const [family, node] of [...families].sort((a, b) => a[0].localeCompare(b[0], 'fr'))) {
            const item = document.createElement('li');
            const swatch = document.createElement('span');
            swatch.className = 'graph-swatch';
            swatch.style.backgroundColor = node.color;
            swatch.style.borderColor = node.border_color;
            item.append(swatch, document.createTextNode(familyLabel(family === 'Non classé' ? null : family)));
            list.appendChild(item);
        }
        target.appendChild(list);
    },
    nodeSize(node) {
        if (node.group === 'genre') {
            return 18 + 22 * Math.sqrt((node.artist_count || 0) / this.maxGenreCount);
        }
        const count = this.likedCounts?.[node.id.slice(7)] || 0;
        return 10 + 18 * Math.log1p(count) / Math.log1p(this.maxLikedCount);
    },
    layoutPositions(nodes, edges) {
        const positions = new Map();
        const genres = nodes.filter(node => node.group === 'genre');
        const families = [...new Set(genres.map(node => node.family || 'Non classé'))].sort();
        const radius = Math.max(450, families.length * 130);
        const byFamily = new Map(families.map(family => [family, []]));
        for (const genre of genres) byFamily.get(genre.family || 'Non classé').push(genre);
        families.forEach((family, index) => {
            const angle = 2 * Math.PI * index / families.length;
            const centerX = radius * Math.cos(angle);
            const centerY = radius * Math.sin(angle);
            byFamily.get(family).sort((left, right) => left.id.localeCompare(right.id)).forEach((genre, rank) => {
                const localAngle = rank * 2.399963229728653;
                const localRadius = 75 * Math.sqrt(rank);
                positions.set(genre.id, {
                    x: centerX + localRadius * Math.cos(localAngle),
                    y: centerY + localRadius * Math.sin(localAngle),
                });
            });
        });
        const neighbors = new Map();
        for (const edge of edges) {
            const artistId = edge.from.startsWith('artist:') ? edge.from : edge.to;
            const genreId = artistId === edge.from ? edge.to : edge.from;
            if (!neighbors.has(artistId)) neighbors.set(artistId, []);
            if (positions.has(genreId)) neighbors.get(artistId).push(genreId);
        }
        const ranks = new Map();
        nodes.filter(node => node.group === 'artist').sort((left, right) => left.id.localeCompare(right.id)).forEach(artist => {
            const linked = [...new Set(neighbors.get(artist.id) || [])].sort();
            const signature = linked.join('|');
            const rank = ranks.get(signature) || 0;
            ranks.set(signature, rank + 1);
            const center = linked.length ? linked.reduce((sum, id) => {
                const point = positions.get(id);
                return { x: sum.x + point.x, y: sum.y + point.y };
            }, { x: 0, y: 0 }) : { x: 0, y: 0 };
            const angle = rank * 2.399963229728653;
            const distance = 65 * Math.sqrt(rank + 1);
            positions.set(artist.id, {
                x: center.x / (linked.length || 1) + distance * Math.cos(angle),
                y: center.y / (linked.length || 1) + distance * Math.sin(angle),
            });
        });
        return positions;
    },
    filtered() {
        if (!this.data) return { artists: [], genres: [], edges: [] };
        const query = document.getElementById('graph-search').value.trim().toLocaleLowerCase('fr');
        const genreId = document.getElementById('graph-genre').value;
        const genreEdges = genreId ? this.genreEdges.get(genreId) || [] : this.data.edges;
        const allowed = genreId ? new Set(genreEdges.map(edge => edge.from.startsWith('artist:') ? edge.from : edge.to)) : null;
        const artists = this.data.nodes.filter(node => node.group === 'artist' &&
            (!allowed || allowed.has(node.id)) && node.label.toLocaleLowerCase('fr').includes(query));
        const artistIds = new Set(artists.map(node => node.id));
        const edges = genreEdges.filter(edge => artistIds.has(edge.from) || artistIds.has(edge.to));
        const genreIds = new Set(edges.map(edge => edge.from.startsWith('genre:') ? edge.from : edge.to));
        const genres = this.data.nodes.filter(node => genreIds.has(node.id));
        return { artists, genres, edges };
    },
    render() {
        const started = performance.now();
        const token = ++this.paintToken;
        this.renderNetwork(this.filtered());
        if (this.network) this.network.redraw();
        requestAnimationFrame(() => requestAnimationFrame(() => {
            if (token !== this.paintToken) return;
            this.paintMs = performance.now() - (this.loadStartedAt || started);
            this.loadStartedAt = null;
            this.updateMetrics();
            if (this.paintMs > 3000 && (this.data?.nodes.length || 0) > 300) {
                showMessage(document.getElementById('graph-status'), T('Le réseau complet met du temps à s’afficher. Filtrez un genre pour alléger la vue.', 'The full network is taking time to render. Filter by genre to simplify the view.'));
            }
        }));
    },
    updateMetrics() {
        const parts = [];
        if (this.apiMs !== null) parts.push(`API ${this.apiMs.toFixed(0)} ms`);
        if (this.paintMs !== null) parts.push(`${T('affichage', 'render')} ${this.paintMs.toFixed(0)} ms`);
        if (this.interactionP95 !== null) parts.push(`interaction p95 ${this.interactionP95.toFixed(0)} ms`);
        document.getElementById('graph-metrics').textContent = parts.join(' · ');
    },
    renderNetwork({ artists, genres, edges }) {
        const selectedId = this.selectedNodeId;
        const nodes = [...artists, ...genres].map(node => {
            const artistThemeColor = typeof getComputedStyle === 'function' && node.group === 'artist'
                ? getComputedStyle(document.documentElement).getPropertyValue('--text').trim() : null;
            const fill = artistThemeColor || node.color;
            const border = artistThemeColor || node.border_color || node.color;
            const title = document.createElement?.('div');
            if (title) {
                const count = node.group === 'artist' ? this.likedCounts?.[node.id.slice(7)] : node.artist_count;
                title.textContent = node.group === 'artist'
                    ? T(`${node.label} · artiste${count === undefined ? '' : ` · ${count} titres likés`}`, `${node.label} · artist${count === undefined ? '' : ` · ${count} liked tracks`}`)
                    : T(`${node.label} · genre · ${count || 0} artistes reliés`, `${node.label} · genre · ${count || 0} linked artists`);
            }
            return {
                id: node.id, label: node.group === 'genre' || node.id === selectedId ? node.label : '',
                title,
                font: { color: typeof getComputedStyle === 'function'
                    ? getComputedStyle(document.documentElement).getPropertyValue('--text').trim() : '#17231d' },
                color: {
                    background: fill,
                    border,
                    highlight: { background: fill, border },
                    hover: { background: fill, border },
                },
                borderWidth: 1.5,
                borderWidthSelected: 2,
                shape: 'dot',
                size: this.nodeSize(node),
                ...(!this.renderedNodes.has(node.id) ? this.initialPositions.get(node.id) : {}),
            };
        });
        const links = edges.map(edge => ({ id: JSON.stringify([edge.from, edge.to]), from: edge.from, to: edge.to }));
        if (!this.network) {
            if (!window.vis?.Network) {
                this.networkUnavailable = true;
                return;
            }
            this.nodeSet = new vis.DataSet(nodes);
            this.edgeSet = new vis.DataSet(links);
            this.network = new vis.Network(document.getElementById('graph-container'),
                { nodes: this.nodeSet, edges: this.edgeSet }, {
                    nodes: { font: { size: 13 } },
                    edges: { smooth: false, color: { color: '#a7ada9', opacity: 0.45 } },
                    physics: {
                        solver: 'barnesHut',
                        barnesHut: { gravitationalConstant: -6500, centralGravity: 0.015,
                            springLength: 170, springConstant: 0.025, damping: 0.2, avoidOverlap: 0.7 },
                        stabilization: { enabled: true, iterations: 300, updateInterval: 50, fit: false },
                    },
                    interaction: { hideEdgesOnDrag: true, hideEdgesOnZoom: true, hover: true, tooltipDelay: 180 },
                });
            this.network.on('stabilized', () => this.finishLayout());
            this.layoutTimer = window.setTimeout(() => this.finishLayout(), 4000);
            this.network.on('click', params => { if (params.nodes[0]) this.selectNode(params.nodes[0]); });
            this.network.on('dragStart', () => { this.cameraInitialized = true; this.sampleFrames(); });
            this.network.on('zoom', () => this.sampleFrames());
        } else {
            const nodeChanges = this.dataSetChanges(this.renderedNodes, nodes);
            const edgeChanges = this.dataSetChanges(this.renderedEdges, links);
            if (edgeChanges.removed.length) this.edgeSet.remove(edgeChanges.removed);
            if (nodeChanges.removed.length) this.nodeSet.remove(nodeChanges.removed);
            if (nodeChanges.changed.length) this.nodeSet.update(nodeChanges.changed);
            if (edgeChanges.changed.length) this.edgeSet.update(edgeChanges.changed);
        }
        this.renderedNodes = new Map(nodes.map(node => [node.id, JSON.stringify(node)]));
        this.renderedEdges = new Map(links.map(edge => [edge.id, JSON.stringify(edge)]));
    },
    stopPhysics() {
        if (!this.network || this.physicsStopped) return;
        window.clearTimeout(this.layoutTimer);
        this.network.setOptions({ physics: { enabled: false } });
        this.physicsStopped = true;
    },
    finishLayout() {
        if (!this.network || this.physicsStopped) return;
        this.stopPhysics();
        if (this.cameraInitialized) return;
        this.cameraInitialized = true;
        this.network.fit({ animation: false });
        if (this.network.getScale() >= 0.55) return;
        const focus = this.data.nodes.filter(node => node.group === 'genre')
            .sort((left, right) => (right.artist_count || 0) - (left.artist_count || 0))[0] || this.data.nodes[0];
        if (!focus) return;
        const position = this.network.getPositions([focus.id])[focus.id] || this.initialPositions.get(focus.id);
        if (position) this.network.moveTo({ position, scale: 0.65, animation: false });
    },
    dataSetChanges(previous, items) {
        const next = new Map(items.map(item => [item.id, JSON.stringify(item)]));
        const removed = [...previous.keys()].filter(id => !next.has(id));
        const changed = items.filter(item => previous.get(item.id) !== next.get(item.id));
        return { removed, changed };
    },
    sampleFrames() {
        if (this.sampling) return;
        this.sampling = true;
        const samples = [];
        let previous = performance.now();
        const until = previous + 1000;
        const tick = now => {
            samples.push(now - previous);
            previous = now;
            if (now < until) return requestAnimationFrame(tick);
            this.sampling = false;
            const sorted = samples.slice(1).sort((a, b) => a - b);
            if (sorted.length) {
                this.interactionP95 = sorted[Math.floor(0.95 * (sorted.length - 1))];
                this.updateMetrics();
            }
        };
        requestAnimationFrame(tick);
    },
    sources(edge) {
        return edge.origins.map(origin => origin.source === 'musicbrainz' ? 'MusicBrainz' : 'Spotify').filter((source, index, all) => all.indexOf(source) === index).join(' + ');
    },
    async selectNode(id) {
        const node = this.nodeMap.get(id);
        if (!node) return;
        this.selectedNodeId = id;
        if (node.group === 'artist') Statify.remember('artist', id.slice(7));
        else Statify.remember('genre', id.slice(6));
        const token = ++this.selectionToken;
        const details = Statify.openDrawer(node.label, node.group === 'artist' ? T('Artiste', 'Artist') : T('Genre', 'Genre'), content => {
            if (node.group === 'artist') {
                const spotify = appendText(content, 'a', T('Ouvrir sur Spotify', 'Open on Spotify'));
                spotify.className = 'button button-secondary';
                spotify.href = `https://open.spotify.com/artist/${encodeURIComponent(id.slice(7))}`;
                spotify.target = '_blank'; spotify.rel = 'noopener noreferrer';
                const map = appendText(content, 'a', T('Voir sur la carte', 'View on map'));
                map.className = 'button button-quiet'; map.href = Statify.contextualLink('/map', { artist: id.slice(7) });
                const playlist = Statify.recall('playlist');
                if (playlist) {
                    const link = appendText(content, 'a', T('Revenir à la playlist', 'Return to playlist'));
                    link.className = 'button button-quiet'; link.href = Statify.contextualLink('/sorts', { playlist });
                }
            }
        });
        appendText(details, 'p', '').id = 'graph-selected-count';
        this.updateSelectedCount();
        if (node.group === 'genre') {
            for (const edge of this.genreEdges.get(id) || []) {
                const artistId = edge.from.startsWith('artist:') ? edge.from : edge.to;
                const artist = this.nodeMap.get(artistId);
                const row = document.createElement('p');
                const button = appendText(row, 'button', artist?.label || T('Artiste', 'Artist'));
                button.type = 'button'; button.className = 'graph-text-button';
                button.addEventListener('click', () => this.selectNode(artistId));
                appendText(row, 'small', ` — ${this.sources(edge)}`);
                details.appendChild(row);
            }
        } else {
            this.renderArtistGenres(details, id);
            const loading = appendText(details, 'p', T('Chargement des informations MusicBrainz…', 'Loading MusicBrainz information…'));
            try {
                const result = await fetchJson(`/graphs/artist/${encodeURIComponent(id.slice(7))}/details`);
                if (token !== this.selectionToken) return;
                loading.remove();
                if (result.match) renderMatch(details, result.match, node.label);
                else appendText(details, 'p', T('Aucune autre information MusicBrainz disponible.', 'No further MusicBrainz information available.'));
            } catch (error) {
                if (token === this.selectionToken) showMessage(loading, error.message);
            }
        }
        if (this.network) this.renderNetwork(this.filtered());
    },
    updateSelectedCount() {
        const target = document.getElementById('graph-selected-count');
        const node = this.nodeMap.get(this.selectedNodeId);
        if (!target || !node) return;
        target.textContent = node.group === 'genre'
            ? T(`${node.artist_count} artistes reliés · ${familyLabel(node.family)}`, `${node.artist_count} linked artists · ${familyLabel(node.family)}`)
            : this.likedCounts === null
                ? T('Nombre de titres likés indisponible.', 'Liked track count unavailable.')
                : T(`${this.likedCounts[node.id.slice(7)] || 0} titres likés`, `${this.likedCounts[node.id.slice(7)] || 0} liked tracks`);
    },
    renderArtistGenres(details, artistId) {
        const links = this.data.edges.filter(edge => edge.from === artistId || edge.to === artistId);
        if (!links.length) { appendText(details, 'p', T('Aucun genre renseigné.', 'No genre available.')); return; }
        const list = document.createElement('ul');
        for (const edge of links.sort((a, b) => {
            const left = this.nodeMap.get(a.from === artistId ? a.to : a.from)?.label || '';
            const right = this.nodeMap.get(b.from === artistId ? b.to : b.from)?.label || '';
            return left.localeCompare(right, 'fr');
        })) {
            const genreId = edge.from === artistId ? edge.to : edge.from;
            const genre = this.nodeMap.get(genreId);
            const row = appendText(list, 'li', '');
            const link = appendText(row, 'button', genre?.label || T('Genre', 'Genre'));
            link.type = 'button'; link.className = 'graph-text-button';
            link.addEventListener('click', () => this.selectNode(genreId));
            appendText(row, 'small', ` — ${this.sources(edge)}`);
        }
        details.appendChild(list);
    },
};

const ReleaseYearGraph = {
    loadToken: 0,
    data: null,
    selectedYear: null,
    trackWord(count) { return count === 1 ? 'titre' : 'titres'; },
    async init() {
        const select = document.getElementById('release-playlist');
        select.addEventListener('change', () => { Statify.remember('playlist', select.value); this.load(); });
        try {
            const result = await fetchJson('/playlists/get_playlists');
            select.replaceChildren();
            if (!result.playlists.length) {
                select.add(new Option(T('Aucune playlist', 'No playlists'), ''));
                showMessage(document.getElementById('release-status'), T('Aucune playlist trouvée.', 'No playlists found.'));
                return;
            }
            for (const playlist of result.playlists) select.add(new Option(playlist.name, playlist.id));
            const requested = new URLSearchParams(location.search).get('playlist') || Statify.recall('playlist');
            if (requested && [...select.options].some(option => option.value === requested)) select.value = requested;
            select.disabled = false;
            await this.load();
        } catch (error) {
            showMessage(document.getElementById('release-status'), error.message);
        }
    },
    async load() {
        const token = ++this.loadToken;
        const playlistId = document.getElementById('release-playlist').value;
        const status = document.getElementById('release-status');
        document.getElementById('release-chart').hidden = true;
        document.getElementById('release-summary').textContent = '';
        document.getElementById('release-undated').textContent = '';
        document.getElementById('release-details').replaceChildren();
        if (!playlistId) return;
        showMessage(status, T('Chargement des titres de la playlist…', 'Loading playlist tracks…'));
        try {
            const data = await postJson('/graphs/playlist_release_years', { playlist_id: playlistId });
            if (token !== this.loadToken) return;
            this.data = data;
            this.selectedYear = null;
            this.render();
            const requestedYear = Number(new URLSearchParams(location.search).get('year'));
            if (requestedYear && data.years.some(entry => entry.year === requestedYear)) this.selectYear(requestedYear);
            showMessage(status, !data.track_count ? T('Cette playlist ne contient aucun titre.', 'This playlist has no tracks.')
                : !data.years.length ? T('Aucun titre avec une année de sortie exploitable.', 'No tracks have a usable release year.') : '');
        } catch (error) {
            if (token === this.loadToken) showMessage(status, error.message);
        }
    },
    svgElement(name, attributes = {}, content = null) {
        const node = document.createElementNS('http://www.w3.org/2000/svg', name);
        for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
        if (content !== null) node.textContent = String(content);
        return node;
    },
    render() {
        const data = this.data;
        const chart = document.getElementById('release-chart');
        const axis = document.getElementById('release-axis');
        const bars = document.getElementById('release-bars');
        axis.replaceChildren();
        bars.replaceChildren();
        const datedCount = data.years.reduce((sum, group) => sum + group.tracks.length, 0);
        document.getElementById('release-summary').textContent = T(`${datedCount} ${this.trackWord(datedCount)} daté${datedCount === 1 ? '' : 's'} sur ${data.track_count}.`, `${datedCount} dated tracks out of ${data.track_count}.`);
        document.getElementById('release-undated').textContent = T(`${data.undated_count} ${this.trackWord(data.undated_count)} sans année de sortie exploitable.`, `${data.undated_count} tracks without a usable release year.`);
        if (!data.years.length) { chart.hidden = true; return; }

        const first = data.years[0].year;
        const last = data.years[data.years.length - 1].year;
        const countByYear = new Map(data.years.map(group => [group.year, group]));
        const columnWidth = 38;
        const barWidth = 24;
        const plotTop = 52;
        const plotBottom = 340;
        const plotHeight = plotBottom - plotTop;
        const width = (last - first + 1) * columnWidth;
        const largest = Math.max(...data.years.map(group => group.tracks.length));
        const roughStep = Math.max(1, largest / 5);
        const power = 10 ** Math.floor(Math.log10(roughStep));
        const step = [1, 2, 5, 10].map(value => value * power).find(value => value >= roughStep);
        const ceiling = Math.ceil(largest / step) * step;
        bars.setAttribute('width', width);
        bars.setAttribute('viewBox', `0 0 ${width} 350`);
        bars.setAttribute('preserveAspectRatio', 'xMinYMin meet');

        for (let tick = 0; tick <= ceiling; tick += step) {
            const y = plotBottom - tick / ceiling * plotHeight;
            bars.appendChild(this.svgElement('line', { x1: 0, x2: width, y1: y, y2: y, class: 'release-grid' }));
            axis.appendChild(this.svgElement('text', { x: 46, y: y + 4, 'text-anchor': 'end', class: 'release-axis-label' }, tick));
        }
        for (let year = first; year <= last; year++) {
            const x = (year - first) * columnWidth;
            const decade = year % 10 === 0;
            if (decade) bars.appendChild(this.svgElement('line', { x1: x + columnWidth / 2,
                x2: x + columnWidth / 2, y1: 34, y2: plotBottom, class: 'release-decade' }));
            bars.appendChild(this.svgElement('text', { x: x + columnWidth / 2, y: 25,
                'text-anchor': 'middle', class: decade ? 'release-year release-year-decade' : 'release-year' }, year));
            const group = countByYear.get(year);
            if (!group) continue;
            const count = group.tracks.length;
            const height = Math.max(2, count / ceiling * plotHeight);
            const y = plotBottom - height;
            const rect = this.svgElement('rect', { x: x + (columnWidth - barWidth) / 2, y, width: barWidth, height,
                rx: 3, class: 'release-bar', tabindex: 0, role: 'button',
                'aria-label': T(`${year} : ${count} ${this.trackWord(count)}`, `${year}: ${count} tracks`) });
            rect.appendChild(this.svgElement('title', {}, T(`${year} : ${count} ${this.trackWord(count)}`, `${year}: ${count} tracks`)));
            rect.addEventListener('click', () => this.selectYear(year));
            rect.addEventListener('keydown', event => {
                if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.selectYear(year); }
            });
            bars.appendChild(rect);
            bars.appendChild(this.svgElement('text', { x: x + columnWidth / 2, y: Math.max(45, y - 7),
                'text-anchor': 'middle', class: 'release-count' }, count));
        }
        chart.hidden = false;
        document.getElementById('release-scroll').scrollLeft = 0;
    },
    selectYear(year) {
        const group = this.data.years.find(entry => entry.year === year);
        if (!group) return;
        this.selectedYear = year;
        const details = document.getElementById('release-details');
        details.replaceChildren();
        Statify.remember('year', String(year));
        Statify.openDrawer(String(year), T('Année de sortie', 'Release year'), content => {
            appendText(content, 'p', T(`${group.tracks.length} ${this.trackWord(group.tracks.length)}`, `${group.tracks.length} tracks`));
            const list = appendText(content, 'ol', '');
            for (const track of group.tracks) {
                appendText(list, 'li', `${displayTrackName(track.name)} — ${track.artist_names.join(', ') || T('Artiste inconnu', 'Unknown artist')} · ${track.album_name || T('Album inconnu', 'Unknown album')}`);
            }
            const link = appendText(content, 'a', T('Ouvrir la playlist triée', 'Open sorted playlist'));
            link.className = 'button button-secondary';
            link.href = Statify.contextualLink('/sorts', { playlist: document.getElementById('release-playlist').value });
        });
        for (const bar of document.querySelectorAll('.release-bar')) {
            bar.classList.toggle('selected', bar.getAttribute('aria-label')?.startsWith(`${year}:`) || bar.getAttribute('aria-label')?.startsWith(`${year} :`));
        }
    },
};

function initGraphModes() {
    const type = document.getElementById('graph-type');
    let releaseInitialized = false;
    type.addEventListener('change', () => {
        const release = type.value === 'release_years';
        document.getElementById('artist-graph-controls').hidden = release;
        document.getElementById('artist-graph-view').hidden = release;
        document.getElementById('release-graph-controls').hidden = !release;
        document.getElementById('release-graph-view').hidden = !release;
        if (release && !releaseInitialized) {
            releaseInitialized = true;
            ReleaseYearGraph.init();
        }
        if (!release) requestAnimationFrame(() => GraphPage.network?.redraw());
    });
    GraphPage.init();
    if (new URLSearchParams(location.search).get('mode') === 'release_years') {
        type.value = 'release_years';
        type.dispatchEvent(new Event('change'));
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadProfile();
    if (document.body.id === 'sorts') SortsPage.init();
    if (document.body.id === 'graphs') initGraphModes();
});
