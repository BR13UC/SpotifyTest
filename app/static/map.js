const mapStatus = document.getElementById('map-status');
const mapCounts = document.getElementById('map-counts');
const map = L.map('origin-map', { minZoom: 1, maxZoom: 10, worldCopyJump: true, preferCanvas: true });
map.setView([15, 0], 2);
document.getElementById('map-reset').addEventListener('click', () => map.setView([15, 0], 2));

const countryLayer = L.geoJSON(null, { style: () => countryStyle(false) }).addTo(map);
const markerLayer = L.layerGroup().addTo(map);
let countryArtists = new Map();
let lastData = '';
let restoredSelection = false;
let listMarkers = [];
const listSort = document.getElementById('map-list-sort');
listSort.addEventListener('change', () => renderPlaceList(listMarkers));

function countryStyle(hasArtists) {
    const dark = document.documentElement.dataset.theme === 'dark' ||
        (document.documentElement.dataset.theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
    return { color: dark ? '#506f60' : '#8da99a', weight: 0.8,
        fillColor: hasArtists ? (dark ? '#35694c' : '#b8dfc3') : (dark ? '#263b34' : '#e6f0e9'), fillOpacity: 1 };
}
function countryCode(feature) {
    const props = feature.properties || {};
    return [props.ISO_A2, props.ISO_A2_EH].find(value => value && value !== '-99' && value.length === 2);
}
function openArtist(artist, place) {
    Statify.remember('artist', artist.id);
    Statify.openDrawer(artist.name, Statify.t('Artiste', 'Artist'), content => {
        appendText(content, 'p', `${Statify.t('Lieu', 'Place')} : ${artist.origin_name || place.label}`);
        appendText(content, 'p', `${Statify.t('Précision', 'Precision')} : ${artist.precision === 'city' ? Statify.t('ville localisée', 'located city') : Statify.t('pays approximatif', 'approximate country')}`);
        appendText(content, 'p', `${Statify.t('Type', 'Type')} : ${artist.origin_type === 'birth' ? Statify.t('naissance', 'birth') : artist.origin_type === 'formation' ? Statify.t('formation', 'formation') : Statify.t('pays indiqué', 'reported country')}`);
        appendText(content, 'p', `${Statify.t('Source', 'Source')} : MusicBrainz`);
        const graph = appendText(content, 'a', Statify.t('Voir dans le graphe', 'View in graph'));
        graph.className = 'button button-secondary'; graph.href = Statify.contextualLink('/graphs', { artist: artist.id });
        const spotify = appendText(content, 'a', Statify.t('Ouvrir sur Spotify', 'Open on Spotify'));
        spotify.className = 'button button-quiet'; spotify.href = `https://open.spotify.com/artist/${encodeURIComponent(artist.id)}`;
        spotify.target = '_blank'; spotify.rel = 'noopener noreferrer';
    });
}
function openPlace(place) {
    Statify.openDrawer(place.label, place.precision === 'city' ? Statify.t('Ville localisée', 'Located city') : Statify.t('Pays approximatif', 'Approximate country'), content => {
        appendText(content, 'p', Statify.t(`${place.artists.length} artiste${place.artists.length > 1 ? 's' : ''} · source : MusicBrainz`, `${place.artists.length} artists · source: MusicBrainz`));
        if (place.precision !== 'city') appendText(content, 'p', Statify.t('Ce repère indique le pays, pas une ville précise.', 'This marker indicates a country, not an exact city.'));
        if (!place.artists.length) appendText(content, 'p', Statify.t('Aucun artiste placé dans ce pays.', 'No artists placed in this country.'));
        const list = appendText(content, 'ul', '');
        for (const artist of place.artists) {
            const item = appendText(list, 'li', '');
            const button = appendText(item, 'button', artist.name);
            button.type = 'button'; button.className = 'graph-text-button';
            button.addEventListener('click', () => openArtist(artist, place));
            appendText(item, 'small', ` — ${artist.origin_name || place.label}`);
        }
    });
}
function renderCountries() {
    countryLayer.eachLayer(layer => {
        const code = countryCode(layer.feature);
        const artists = countryArtists.get(code) || [];
        layer.setStyle(countryStyle(artists.length > 0));
        layer.off('click');
        layer.on('click', () => openPlace({ label: layer.feature.properties.ADMIN || code || '—', precision: 'country', artists }));
    });
}
const continents = [
    ['Africa', 'Afrique'], ['Asia', 'Asie'], ['Europe', 'Europe'],
    ['North America', 'Amérique du Nord'], ['South America', 'Amérique du Sud'],
    ['Oceania', 'Océanie'], ['Antarctica', 'Antarctique'],
];
function renderPlaceList(markers) {
    const container = document.getElementById('map-place-list');
    const expanded = new Set([...container.querySelectorAll('details[open]')].map(item => item.dataset.key));
    const collator = new Intl.Collator(Statify.locale(), { sensitivity: 'base', numeric: true });
    const countryNames = new Intl.DisplayNames([Statify.locale()], { type: 'region' });
    const compare = (a, b) => (listSort.value === 'count' ? b.count - a.count : 0) ||
        collator.compare(a.label, b.label) || collator.compare(a.key, b.key);
    const groups = new Map();
    for (const place of markers) {
        const continent = continents.some(([name]) => name === place.continent) ? place.continent : 'unknown';
        if (!groups.has(continent)) groups.set(continent, new Map());
        const countries = groups.get(continent);
        const code = place.country_code;
        const key = code || 'unknown';
        const label = code && /^[A-Z]{2}$/.test(code) ? countryNames.of(code)
            : place.country || Statify.t('Pays inconnu', 'Unknown country');
        if (!countries.has(key)) countries.set(key, { key: JSON.stringify([continent, key]), label, count: 0, cities: new Map() });
        const country = countries.get(key);
        country.count += place.artists.length;
        const cityKey = place.precision === 'city' ? place.id : 'unknown';
        if (!country.cities.has(cityKey)) country.cities.set(cityKey, {
            key: JSON.stringify([continent, key, cityKey]),
            label: place.precision === 'city' ? place.label : Statify.t('Ville inconnue', 'Unknown city'),
            count: 0, entries: [],
        });
        const city = country.cities.get(cityKey);
        city.count += place.artists.length;
        city.entries.push(...place.artists.map(artist => ({ artist, place })));
    }
    container.replaceChildren();
    if (!markers.length) {
        appendText(container, 'p', Statify.t('Aucun lieu confirmé à afficher.', 'No confirmed places to show.'));
        return;
    }
    const countLabel = count => Statify.t(`${count} artiste${count === 1 ? '' : 's'}`, `${count} artist${count === 1 ? '' : 's'}`);
    const details = (parent, group, className) => {
        const element = appendText(parent, 'details', '');
        element.className = className;
        element.dataset.key = group.key;
        element.open = expanded.has(group.key);
        const summary = appendText(element, 'summary', '');
        appendText(summary, 'span', group.label);
        appendText(summary, 'small', countLabel(group.count));
        return element;
    };
    for (const [name, french] of [...continents, ['unknown', 'Continent inconnu']]) {
        if (!groups.has(name)) continue;
        const countries = [...groups.get(name).values()].sort(compare);
        const column = appendText(container, 'section', '');
        column.className = 'map-continent';
        const heading = appendText(column, 'h3', Statify.t(french, name === 'unknown' ? 'Unknown continent' : name));
        appendText(heading, 'small', countLabel(countries.reduce((sum, country) => sum + country.count, 0)));
        for (const country of countries) {
            const countryDetails = details(column, country, 'map-country');
            for (const city of [...country.cities.values()].sort(compare)) {
                const cityDetails = details(countryDetails, city, 'map-city');
                const artists = appendText(cityDetails, 'ul', '');
                for (const { artist, place } of city.entries.sort((a, b) => collator.compare(a.artist.name, b.artist.name))) {
                    const item = appendText(artists, 'li', '');
                    const button = appendText(item, 'button', artist.name);
                    button.type = 'button';
                    button.addEventListener('click', () => openArtist(artist, place));
                }
            }
        }
    }
}
function renderData(data) {
    const signature = JSON.stringify(data);
    if (signature === lastData) return;
    lastData = signature;
    markerLayer.clearLayers();
    countryArtists = new Map();
    listMarkers = data.markers;
    renderPlaceList(listMarkers);
    for (const place of data.markers) {
        if (place.country_code) {
            if (!countryArtists.has(place.country_code)) countryArtists.set(place.country_code, []);
            countryArtists.get(place.country_code).push(...place.artists);
        }
        const isCity = place.precision === 'city';
        const marker = L.circleMarker([place.latitude, place.longitude], {
            radius: Math.min(15, 6 + Math.sqrt(place.artists.length) * 2),
            color: isCity ? '#075e35' : '#5a3a92', fillColor: isCity ? '#1db954' : '#8056bd',
            fillOpacity: 0.94, weight: 1.5, bubblingMouseEvents: false,
        });
        marker.bindTooltip(`${place.label} · ${place.artists.length}`);
        marker.on('click', () => openPlace(place));
        marker.addTo(markerLayer);
    }
    renderCountries();
    const counts = data.counts;
    mapCounts.textContent = Statify.t(
        `${counts.total} artistes · ${counts.city} situés en ville · ${counts.country} au niveau du pays · ${counts.unknown} non placés`,
        `${counts.total} artists · ${counts.city} located in a city · ${counts.country} at country level · ${counts.unknown} not placed`);
    mapStatus.textContent = counts.total === 0 ? Statify.t('Aucun artiste dans cette source.', 'No artists in this source.')
        : counts.pending && data.markers.length ? Statify.t(`${data.markers.length} repères affichés · ${counts.pending} origines en cours de localisation…`, `${data.markers.length} markers shown · ${counts.pending} origins being located…`)
        : counts.pending ? Statify.t(`${counts.pending} origines en cours de localisation…`, `${counts.pending} origins being located…`)
        : data.markers.length ? Statify.t(`${data.markers.length} repères affichés.`, `${data.markers.length} markers shown.`)
        : Statify.t('Aucun lieu à afficher pour le moment. Les correspondances MusicBrainz confirmées apparaîtront ici.', 'No places to show yet. Confirmed MusicBrainz matches will appear here.');
    const requested = new URLSearchParams(location.search).get('artist') || Statify.recall('artist');
    if (requested && !restoredSelection) {
        const place = data.markers.find(item => item.artists.some(artist => artist.id === requested));
        const artist = place?.artists.find(item => item.id === requested);
        if (place && artist) { restoredSelection = true; map.setView([place.latitude, place.longitude], Math.max(map.getZoom(), 4)); openArtist(artist, place); }
        else mapStatus.textContent = Statify.t('Cet artiste n’a pas encore de lieu confirmé sur la carte.', 'This artist has no confirmed place on the map yet.');
    }
}
async function refreshMap() {
    if (!VisualizationSource.ready) return;
    try { renderData(await VisualizationSource.request('/map/data')); }
    catch (error) {
        if (error.name !== 'AbortError') mapStatus.textContent = Statify.t(`Carte indisponible : ${error.message}`, `Map unavailable: ${error.message}`);
    }
}
fetch('/static/world-countries.geojson')
    .then(response => { if (!response.ok) throw new Error(Statify.t('Frontières indisponibles', 'Borders unavailable')); return response.json(); })
    .then(geojson => { countryLayer.addData(geojson); renderCountries(); })
    .catch(error => { mapStatus.textContent = error.message; });
VisualizationSource.init(ready => {
    lastData = '';
    restoredSelection = false;
    markerLayer.clearLayers();
    countryArtists = new Map();
    listMarkers = [];
    document.getElementById('map-place-list').replaceChildren();
    mapCounts.textContent = '';
    mapStatus.textContent = '';
    renderCountries();
    if (ready) refreshMap();
});
const mapPolling = window.setInterval(() => { if (!document.hidden) refreshMap(); }, 12000);
window.addEventListener('pagehide', () => { clearInterval(mapPolling); VisualizationSource.controller.abort(); });
new MutationObserver(renderCountries).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', renderCountries);
