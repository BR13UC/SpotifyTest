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
                link.href = Statify.contextualLink('/graphs', { mode: 'release_years', source: 'playlist', playlist_id: document.getElementById('playlist-select').value, year: item.release_date.slice(0, 4) });
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

document.addEventListener('DOMContentLoaded', () => {
    if (document.body.id === 'sorts') SortsPage.init();
});
