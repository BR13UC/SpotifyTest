/* Shared source selection and cancellation across artist visualizations. */
window.VisualizationSource = {
    source: 'followed',
    playlistId: '',
    ready: false,
    generation: 0,
    controller: new AbortController(),
    params() {
        return { source: this.source, ...(this.source === 'playlist' ? { playlist_id: this.playlistId } : {}) };
    },
    url(path, extra = {}) { return Statify.contextualLink(path, { ...this.params(), ...extra }); },
    async request(path, extra = {}) {
        const generation = this.generation;
        const data = await fetchJson(this.url(path, extra), { signal: this.controller.signal });
        if (generation !== this.generation) throw new DOMException('Source changed', 'AbortError');
        return data;
    },
    updateContext() {
        Statify.remember('source', this.source);
        if (this.playlistId) Statify.remember('source-playlist', this.playlistId);
        const url = new URL(location.href);
        if (this.generation > 1) {
            for (const key of ['artist', 'genre', 'year']) {
                url.searchParams.delete(key);
                Statify.remember(key, '');
            }
        }
        url.searchParams.set('source', this.source);
        if (this.source === 'playlist' && this.playlistId) url.searchParams.set('playlist_id', this.playlistId);
        else url.searchParams.delete('playlist_id');
        url.searchParams.delete('playlist');
        history.replaceState(null, '', url);
        for (const link of document.querySelectorAll('#site-nav a')) {
            const path = new URL(link.href, location.origin).pathname;
            if (['/graphs', '/map', '/timeline'].includes(path)) link.href = this.url(path);
        }
    },
    async init(onChange) {
        this.onChange = onChange;
        const select = document.getElementById('artist-source');
        const playlists = document.getElementById('artist-source-playlist');
        const query = new URLSearchParams(location.search);
        this.source = query.get('source') || (query.has('playlist') ? 'playlist' : Statify.recall('source')) || 'followed';
        this.playlistId = query.get('playlist_id') || query.get('playlist') || Statify.recall('source-playlist') || '';
        select.value = this.source;
        select.addEventListener('change', () => { this.source = select.value; this.change(); });
        playlists.addEventListener('change', () => { this.playlistId = playlists.value; this.change(); });
        await this.change();
    },
    async change() {
        const generation = ++this.generation;
        this.controller.abort();
        this.controller = new AbortController();
        this.ready = false;
        Statify.closeDrawer();
        const status = document.getElementById('artist-source-status');
        const playlists = document.getElementById('artist-source-playlist');
        document.getElementById('artist-source-playlist-controls').hidden = this.source !== 'playlist';
        status.textContent = Statify.t('Chargement de la source…', 'Loading source…');
        this.onChange(false);
        try {
            if (!['followed', 'liked', 'playlist'].includes(this.source)) throw new Error(Statify.t('Source inconnue.', 'Unknown source.'));
            if (this.source === 'playlist') {
                playlists.disabled = true;
                const data = await this.request('/playlists/get_playlists');
                playlists.replaceChildren();
                for (const playlist of data.playlists) playlists.add(new Option(playlist.name, playlist.id));
                if (!data.playlists.length) {
                    playlists.add(new Option(Statify.t('Aucune playlist', 'No playlists'), ''));
                    status.textContent = Statify.t('Aucune playlist trouvée.', 'No playlists found.');
                    return;
                }
                if (this.playlistId && !data.playlists.some(playlist => playlist.id === this.playlistId)) {
                    playlists.add(new Option(Statify.t('Playlist indisponible', 'Unavailable playlist'), this.playlistId));
                }
                this.playlistId ||= data.playlists[0].id;
                playlists.value = this.playlistId;
                playlists.disabled = false;
            }
            this.updateContext();
            await this.request('/users/artist_collection');
            if (generation !== this.generation) return;
            this.ready = true;
            status.textContent = '';
            this.onChange(true);
        } catch (error) {
            if (generation === this.generation && error.name !== 'AbortError') status.textContent = error.message;
        }
    },
};
