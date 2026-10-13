const familyLabel = family => {
    if (Statify.locale() === 'en') return family || 'Unclassified';
    return ({ Electronic: 'Électronique', Rock: 'Rock', Pop: 'Pop', 'Hip-Hop': 'Hip-hop', Jazz: 'Jazz',
        'Funk / Soul': 'Funk / Soul', Classical: 'Classique', Blues: 'Blues',
        'Folk, World, & Country': 'Folk, musiques du monde et country', Latin: 'Latino',
        Reggae: 'Reggae', African: 'Africain', 'East Asian Pop': 'Pop est-asiatique', Experimental: 'Expérimental',
        'Brass & Military': 'Cuivres et musique militaire',
        'Stage & Screen': 'Scène et écran', "Children's": 'Musique pour enfants', 'Non-Music': 'Non musical' })[family] || 'Non classé';
};

const GraphPage = {
    graphToken: 0,
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
        document.getElementById('create-graph-btn').addEventListener('click', () => this.loadGraph(false, true));
        document.getElementById('graph-overview-btn').addEventListener('click', () => this.network?.fit({ animation: false }));
        document.getElementById('export-graphml').addEventListener('click', () => {
            if (VisualizationSource.ready) window.location.href = VisualizationSource.url('/graphs/export_graph/graphml');
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
    },
    resetSource() {
        ++this.graphToken;
        ++this.likesToken;
        ++this.selectionToken;
        ++this.paintToken;
        this.loading = false;
        this.data = null;
        this.likedCounts = null;
        this.selectedNodeId = null;
        this.cameraInitialized = false;
        this.nodeMap = new Map();
        this.genreEdges = new Map();
        this.renderedNodes.clear();
        this.renderedEdges.clear();
        this.network?.destroy();
        this.network = null;
        this.nodeSet = null;
        this.edgeSet = null;
        document.getElementById('graph-search').value = '';
        document.getElementById('graph-genre').replaceChildren(new Option(T('Tous les genres', 'All genres'), ''));
        document.getElementById('graph-select-genre').disabled = true;
        document.getElementById('graph-family-legend').replaceChildren();
        document.getElementById('graph-status').textContent = '';
        document.getElementById('graph-likes-status').textContent = '';
        this.setLoading('graph', false);
        this.setLoading('likes', false);
    },
    async loadGraph(refresh = false, refreshLikes = false) {
        if (this.loading || !VisualizationSource.ready) return;
        const token = ++this.graphToken;
        this.loading = true;
        this.setLoading('graph', true);
        const status = document.getElementById('graph-status');
        if (!refresh) showMessage(status, T('Chargement du graphe…', 'Loading graph…'));
        const started = performance.now();
        try {
            if (refreshLikes && VisualizationSource.source === 'liked') {
                await VisualizationSource.request('/graphs/liked_artist_counts', { refresh: '1' });
                await VisualizationSource.request('/users/artist_collection');
                refreshLikes = false;
            }
            const data = await VisualizationSource.request('/graphs/get_artist_genre_graph_data');
            if (token !== this.graphToken) return;
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
                : T('Aucun artiste à afficher dans cette source.', 'No artists to show in this source.'));
            if (data.nodes.length) this.loadLikedCounts(refreshLikes);
        } catch (error) {
            if (token === this.graphToken && error.name !== 'AbortError') showMessage(status, error.message);
        } finally {
            if (token === this.graphToken) {
                this.loading = false;
                this.setLoading('graph', false);
            }
        }
    },
    async loadLikedCounts(force = false) {
        const token = ++this.likesToken;
        const status = document.getElementById('graph-likes-status');
        this.setLoading('likes', true);
        showMessage(status, T('Chargement des titres likés…', 'Loading liked tracks…'));
        try {
            const result = await VisualizationSource.request('/graphs/liked_artist_counts', force ? { refresh: '1' } : {});
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
    layoutRadius(node) {
        return node.group === 'genre'
            ? 24 + 14 * Math.sqrt(Math.max(1, node.artist_count || 0))
            : 18;
    },
    layoutPositions(nodes, edges) {
        const positions = new Map();
        const genres = nodes.filter(node => node.group === 'genre');
        if (!genres.length) return positions;
        const byFamily = new Map();
        for (const genre of genres) {
            const family = genre.family || 'Non classé';
            const cluster = genre.cluster || family;
            if (!byFamily.has(family)) byFamily.set(family, new Map());
            const clusters = byFamily.get(family);
            if (!clusters.has(cluster)) clusters.set(cluster, []);
            clusters.get(cluster).push(genre);
        }
        const families = [...byFamily.entries()].map(([name, clusterMap]) => {
            const clusters = [...clusterMap.entries()].map(([cluster, items]) => ({
                name: cluster, items,
                weight: items.reduce((sum, item) => sum + Math.max(1, item.artist_count || 0), 0),
            })).sort((left, right) => right.weight - left.weight || left.name.localeCompare(right.name));
            return { name, clusters, weight: clusters.reduce((sum, cluster) => sum + cluster.weight, 0) };
        }).sort((left, right) => right.weight - left.weight || left.name.localeCompare(right.name));
        const maximumWeight = Math.max(...families.map(family => family.weight));
        families.forEach((family, familyRank) => {
            const angle = (familyRank - 1) * 2.399963229728653 - Math.PI / 2;
            const weightGap = Math.log1p(maximumWeight) - Math.log1p(family.weight);
            const center = familyRank === 0 ? { x: 0, y: 0 } : {
                x: (340 + 64 * Math.sqrt(familyRank) + 46 * weightGap) * Math.cos(angle),
                y: (235 + 46 * Math.sqrt(familyRank) + 34 * weightGap) * Math.sin(angle),
            };
            family.clusters.forEach((cluster, clusterRank) => {
                const clusterAngle = clusterRank * 2.399963229728653;
                const clusterRadius = clusterRank === 0 ? 0 : 128 + 68 * Math.sqrt(clusterRank);
                const clusterCenter = {
                    x: center.x + clusterRadius * Math.cos(clusterAngle),
                    y: center.y + clusterRadius * Math.sin(clusterAngle),
                };
                cluster.items.sort((left, right) => (right.artist_count || 0) - (left.artist_count || 0) || left.id.localeCompare(right.id)).forEach((genre, rank) => {
                    const localAngle = rank * 2.399963229728653;
                    const localRadius = rank === 0 ? 0 : 74 * Math.sqrt(rank) + this.layoutRadius(genre);
                    positions.set(genre.id, {
                        x: clusterCenter.x + localRadius * Math.cos(localAngle),
                        y: clusterCenter.y + localRadius * Math.sin(localAngle),
                    });
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
            const linked = [...new Set(neighbors.get(artist.id) || [])].sort((left, right) => {
                const leftDegree = nodes.find(node => node.id === left)?.artist_count || 0;
                const rightDegree = nodes.find(node => node.id === right)?.artist_count || 0;
                return rightDegree - leftDegree || left.localeCompare(right);
            });
            const signature = linked.join('|');
            const rank = ranks.get(signature) || 0;
            ranks.set(signature, rank + 1);
            const centroid = linked.length ? linked.reduce((sum, id) => {
                const point = positions.get(id);
                return { x: sum.x + point.x, y: sum.y + point.y };
            }, { x: 0, y: 0 }) : { x: 0, y: 0 };
            const home = linked.length ? positions.get(linked[0]) : { x: 0, y: 0 };
            const blend = linked.length > 1 ? 0.28 : 0;
            const center = {
                x: home.x * (1 - blend) + centroid.x / (linked.length || 1) * blend,
                y: home.y * (1 - blend) + centroid.y / (linked.length || 1) * blend,
            };
            const angle = rank * 2.399963229728653;
            const distance = 105 + 64 * Math.sqrt(rank);
            positions.set(artist.id, {
                x: center.x + distance * Math.cos(angle),
                y: center.y + distance * Math.sin(angle),
            });
        });
        // Keep the family geometry fixed while moving artists just enough to avoid overlaps.
        const artists = nodes.filter(node => node.group === 'artist').sort((left, right) => left.id.localeCompare(right.id));
        for (let iteration = 0; iteration < 14; iteration += 1) {
            for (const artist of artists) {
                const point = positions.get(artist.id);
                let shiftX = 0;
                let shiftY = 0;
                for (const other of nodes) {
                    if (other.id === artist.id) continue;
                    const otherPoint = positions.get(other.id);
                    const dx = point.x - otherPoint.x;
                    const dy = point.y - otherPoint.y;
                    const distance = Math.hypot(dx, dy) || 0.001;
                    const minimum = this.layoutRadius(artist) + this.layoutRadius(other) + 28;
                    if (distance >= minimum) continue;
                    const push = (minimum - distance) / minimum;
                    const direction = distance === 0.001
                        ? { x: Math.cos(iteration + artist.id.length), y: Math.sin(iteration + artist.id.length) }
                        : { x: dx / distance, y: dy / distance };
                    shiftX += direction.x * push * 24;
                    shiftY += direction.y * push * 24;
                }
                if (shiftX || shiftY) {
                    const length = Math.hypot(shiftX, shiftY);
                    const cap = 34;
                    positions.set(artist.id, {
                        x: point.x + shiftX * Math.min(1, cap / length),
                        y: point.y + shiftY * Math.min(1, cap / length),
                    });
                }
            }
        }
        return positions;
    },
    artistLabelThreshold() {
        const counts = this.data?.nodes.filter(node => node.group === 'artist')
            .map(node => this.likedCounts?.[node.id.slice(7)] || 0)
            .sort((left, right) => left - right) || [];
        return counts.length ? counts[Math.ceil(counts.length * 0.75) - 1] : Infinity;
    },
    shouldShowArtistLabel(node, visibleNodes, selectedId) {
        if (node.id === selectedId) return true;
        const count = this.likedCounts?.[node.id.slice(7)] || 0;
        if (!this.likedCounts || !count || count < this.artistLabelThreshold()) return false;
        const point = this.initialPositions.get(node.id);
        if (!point) return false;
        return visibleNodes.every(other => {
            if (other.id === node.id) return true;
            const otherPoint = this.initialPositions.get(other.id);
            if (!otherPoint) return true;
            return Math.hypot(point.x - otherPoint.x, point.y - otherPoint.y) >=
                (this.nodeSize(node) + this.nodeSize(other)) * 1.6;
        });
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
        const visibleNodes = [...artists, ...genres];
        const nodes = visibleNodes.map(node => {
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
                id: node.id, label: node.group === 'genre' || this.shouldShowArtistLabel(node, visibleNodes, selectedId) ? node.label : '',
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
                shadow: node.group === 'genre' ? { enabled: true, color: `${fill}66`, size: 10, x: 0, y: 0 } : false,
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
                    physics: false,
                    interaction: { hideEdgesOnDrag: true, hideEdgesOnZoom: true, hover: true, tooltipDelay: 180 },
                });
            this.finishLayout();
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
    finishLayout() {
        if (!this.network || this.cameraInitialized) return;
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
                const playlist = VisualizationSource.source === 'playlist' ? VisualizationSource.playlistId : Statify.recall('playlist');
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
                const result = await VisualizationSource.request(`/graphs/artist/${encodeURIComponent(id.slice(7))}/details`);
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
    async load() {
        const token = ++this.loadToken;
        const status = document.getElementById('release-status');
        document.getElementById('release-chart').hidden = true;
        document.getElementById('release-summary').textContent = '';
        document.getElementById('release-undated').textContent = '';
        document.getElementById('release-details').replaceChildren();
        if (!VisualizationSource.ready) return;
        showMessage(status, T('Chargement des titres…', 'Loading tracks…'));
        try {
            const data = await VisualizationSource.request('/graphs/release_years');
            if (token !== this.loadToken) return;
            this.data = data;
            this.selectedYear = null;
            this.render();
            const requestedYear = Number(new URLSearchParams(location.search).get('year'));
            if (requestedYear && data.years.some(entry => entry.year === requestedYear)) this.selectYear(requestedYear);
            showMessage(status, !data.track_count ? T('Aucun titre dans cette source.', 'No tracks in this source.')
                : !data.years.length ? T('Aucun titre avec une année de sortie exploitable.', 'No tracks have a usable release year.') : '');
        } catch (error) {
            if (token === this.loadToken && error.name !== 'AbortError') showMessage(status, error.message);
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
            if (VisualizationSource.source === 'playlist') {
                const link = appendText(content, 'a', T('Ouvrir la playlist triée', 'Open sorted playlist'));
                link.className = 'button button-secondary';
                link.href = Statify.contextualLink('/sorts', { playlist: VisualizationSource.playlistId });
            }
        });
        for (const bar of document.querySelectorAll('.release-bar')) {
            bar.classList.toggle('selected', bar.getAttribute('aria-label')?.startsWith(`${year}:`) || bar.getAttribute('aria-label')?.startsWith(`${year} :`));
        }
    },
};

function initGraphModes() {
    const type = document.getElementById('graph-type');
    if (new URLSearchParams(location.search).get('mode') === 'release_years') type.value = 'release_years';
    const showMode = () => {
        const release = type.value === 'release_years';
        document.getElementById('artist-graph-controls').hidden = release;
        document.getElementById('artist-graph-view').hidden = release;
        document.getElementById('release-graph-controls').hidden = !release;
        document.getElementById('release-graph-view').hidden = !release;
        if (release && VisualizationSource.ready) ReleaseYearGraph.load();
        if (!release) requestAnimationFrame(() => GraphPage.network?.redraw());
    };
    type.addEventListener('change', showMode);
    GraphPage.init();
    showMode();
    VisualizationSource.init(ready => {
        GraphPage.resetSource();
        ++ReleaseYearGraph.loadToken;
        ReleaseYearGraph.data = null;
        ReleaseYearGraph.selectedYear = null;
        for (const id of ['release-status', 'release-summary', 'release-undated', 'release-details']) {
            document.getElementById(id).replaceChildren();
        }
        document.getElementById('release-chart').hidden = true;
        if (ready) {
            GraphPage.loadGraph();
            if (type.value === 'release_years') ReleaseYearGraph.load();
        }
    });
}

document.addEventListener('DOMContentLoaded', () => {
    if (document.body.id === 'graphs') initGraphModes();
});
