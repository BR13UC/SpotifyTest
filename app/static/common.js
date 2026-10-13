const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
const T = (fr, en) => Statify.t(fr, en);
const displayTrackName = name => name === 'Titre indisponible' ? T('Titre indisponible', 'Unavailable track') : name;

async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        const error = new Error((Statify.locale() === 'en' && data.error_en) || Statify.translateError(data.error, data.error_code) || `HTTP ${response.status}`);
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
        ep: T('titre sur un EP', 'track on an EP'), contribution: T('participation créditée', 'credited contribution'), score: T('force des indices', 'evidence strength'),
        disambiguation: T('candidats départagés par les indices disponibles', 'candidates distinguished using available evidence'),
        first_candidate: T('premier candidat compatible, indices à égalité', 'first compatible candidate, tied evidence') }[match.match_method];
    if (method) appendText(box, 'p', T(`Correspondance : ${method}.`, `Match: ${method}.`));
    if (match.match_evidence?.common_albums?.length) appendText(box, 'p', T(`Albums communs : ${match.match_evidence.common_albums.join(', ')}`, `Albums in common: ${match.match_evidence.common_albums.join(', ')}`));
    if (match.match_evidence?.release) appendText(box, 'p', T(`Sortie concordante : ${match.match_evidence.release}.`, `Matching release: ${match.match_evidence.release}.`));
    if (match.match_evidence?.spotify_url) appendText(box, 'p', T(`Lien confirmé : ${match.match_evidence.spotify_url}`, `Confirmed link: ${match.match_evidence.spotify_url}`));
    if (match.match_evidence?.ranked?.length) {
        const ranked = match.match_evidence.ranked;
        appendText(box, 'p', T(`Score : ${ranked[0].score}/100${ranked.length > 1 ? ` · deuxième : ${ranked[1].score}/100` : ''}.`, `Score: ${ranked[0].score}/100${ranked.length > 1 ? ` · second: ${ranked[1].score}/100` : ''}.`));
    }
    if (match.kind === 'artist' && match.match_evidence?.ranked) appendText(box, 'p', T('Le score mesure la force des indices, pas une probabilité. Confirmation : 85/100, 15 points d’avance et deux enregistrements distincts, ou identité directe unique ; analyse complète requise.', 'The score measures evidence strength, not probability. Confirmation: 85/100, a 15-point lead and two distinct recordings, or a unique direct identity; complete analysis required.'));
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
        if (match.match_evidence?.ranked) {
            const positions = new Map(match.match_evidence.ranked.map((item, index) => [item.id, index]));
            candidates.sort((a, b) => (positions.get(a.id) ?? Infinity) - (positions.get(b.id) ?? Infinity));
        }
        const exact = match.kind === 'artist' && match.match_evidence?.ranked ? candidates : candidates.filter(candidate => sameArtistName(candidate.name, match.spotify.name));
        const others = match.kind === 'artist' && match.match_evidence?.ranked ? [] : candidates.filter(candidate => !sameArtistName(candidate.name, match.spotify.name));
        const message = appendText(box, 'p', '');
        const renderCandidate = (target, candidate) => {
            const card = document.createElement('div');
            card.className = 'mb-candidate';
            appendText(card, 'strong', candidate.name);
            const context = [candidate.disambiguation, candidate.type, candidate.area].filter(Boolean).join(' · ');
            if (context) appendText(card, 'p', context);
            if (match.kind === 'artist') {
                const assessment = match.match_evidence?.ranked?.find(item => item.id === candidate.id);
                if (assessment) {
                    const complete = assessment.complete && match.match_evidence.complete !== false;
                    appendText(card, 'p', T(`Force des indices : ${assessment.score}/100${complete ? '' : ' · provisoire'}`, `Evidence strength: ${assessment.score}/100${complete ? '' : ' · provisional'}`));
                    const families = { recordings: T('Titres et crédits', 'Tracks and credits'), discography: T('Discographie', 'Discography'), name: T('Nom et alias', 'Name and aliases'), context: T('Contexte', 'Context') };
                    if (assessment.direct) appendText(card, 'p', T('Identité directe vérifiée : 100 points', 'Verified direct identity: 100 points'));
                    else appendText(card, 'p', Object.entries(assessment.families || {}).map(([key, value]) => `${families[key] || key} : ${value}`).join(' · '));
                    const kinds = { identity: T('Identité', 'Identity'), name: T('Nom ou alias', 'Name or alias'), isrc: 'ISRC', recording: T('Titre et crédits', 'Track and credits'), release: T('Sortie', 'Release'), collaboration: T('Collaboration', 'Collaboration'), date: T('Date', 'Date') };
                    const proofs = document.createElement('details');
                    appendText(proofs, 'summary', T(`Preuves (${assessment.evidence.length})`, `Evidence (${assessment.evidence.length})`));
                    for (const proof of assessment.evidence) {
                        const line = appendText(proofs, 'p', `${kinds[proof.kind] || proof.kind} · ${proof.label || ''} · ${proof.points} pts `);
                        const url = new URL(proof.url);
                        if (['http:', 'https:'].includes(url.protocol)) {
                            const link = appendText(line, 'a', T('Source', 'Source'));
                            link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
                        }
                    }
                    card.appendChild(proofs);
                    const flags = { isrc_title: T('ISRC commun mais titres différents : à examiner', 'Shared ISRC but different titles: review required'), other_spotify: T('Autre profil Spotify associé : à examiner', 'Another associated Spotify profile: review required'), site_unavailable: T('Site officiel indisponible', 'Official website unavailable'), official_other_identity: T('Le site officiel renvoie vers une autre identité Spotify', 'Official website links to a different Spotify identity') };
                    for (const flag of [...(assessment.conflicts || []), ...(assessment.warnings || [])]) {
                        const line = appendText(card, 'p', flags[flag.kind] || flag.kind);
                        if (flag.url && /^https?:\/\//.test(flag.url)) {
                            const link = appendText(line, 'a', T(' · Source', ' · Source'));
                            link.href = flag.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
                        }
                    }
                    const missing = { tracks: T('titres de tes collections', 'collection tracks'), spotify_catalogue: T('catalogue Spotify', 'Spotify catalogue'), aliases: T('pseudonymes documentés', 'documented aliases'), incomplete: T('analyse complète', 'complete analysis') };
                    if (assessment.missing?.length) appendText(card, 'p', T('Données manquantes : ', 'Missing data: ') + [...new Set(assessment.missing)].map(key => missing[key] || key).join(', '));
                }
            }
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
                albums.textContent = T('Comparer titres et sorties', 'Compare tracks and releases');
                const result = appendText(card, 'div', '');
                albums.addEventListener('click', async () => {
                    albums.disabled = true;
                    result.textContent = T('Chargement de la comparaison…', 'Loading comparison…');
                    try {
                        const data = await fetchJson(`/musicbrainz/comparison/artist/${encodeURIComponent(match.spotify_id)}/${encodeURIComponent(candidate.id)}`);
                        result.replaceChildren();
                        appendText(result, 'p', T(`Sorties communes : ${data.common_albums.join(', ') || 'aucun'}`, `Releases in common: ${data.common_albums.join(', ') || 'none'}`));
                        if (data.matched_tracks?.length) appendText(result, 'p', T(`Titres concordants : ${data.matched_tracks.join(', ')}`, `Matching tracks: ${data.matched_tracks.join(', ')}`));
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
        if (window.VisualizationSource && !VisualizationSource.ready) return;
        try {
            const data = window.VisualizationSource
                ? await VisualizationSource.request('/musicbrainz/status')
                : await fetchJson('/musicbrainz/status');
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
        const profile = await fetchJson('/ia/api/profile');
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

document.addEventListener('DOMContentLoaded', () => {
    loadProfile();
});
