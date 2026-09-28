const progressKinds = ['recording', 'artist'];
let activeProgressKind = 'recording';
let progressRequest = 0;
const reviewRevisions = {};

function selectProgressKind(kind, focus = false) {
    activeProgressKind = kind;
    for (const item of progressKinds) {
        const tab = document.getElementById(`tab-${item}`);
        tab.setAttribute('aria-selected', String(item === kind));
        tab.tabIndex = item === kind ? 0 : -1;
        document.getElementById(`panel-${item}`).hidden = item !== kind;
    }
    if (focus) document.getElementById(`tab-${kind}`).focus();
    refreshProgress();
}

function showProgress(kind, data) {
    const total = Number(data.total) || 0;
    const completed = Math.min(total, Number(data.completed) || 0);
    const noun = kind === 'artist' ? 'artiste' : 'titre';
    const bar = document.getElementById(`${kind}-progress`);
    bar.max = Math.max(total, 1);
    bar.value = completed;
    document.getElementById(`progress-percent-${kind}`).textContent = total ? `${Math.floor(completed * 100 / total)} %` : '—';
    document.getElementById(`progress-count-${kind}`).textContent = total
        ? Statify.t(`${completed} ${noun}${completed > 1 ? 's' : ''} traité${completed > 1 ? 's' : ''} sur ${total}`, `${completed} of ${total} ${kind === 'artist' ? 'artists' : 'tracks'} processed`)
        : Statify.t(`Aucun ${noun} chargé pour le moment.`, `No ${kind === 'artist' ? 'artists' : 'tracks'} loaded yet.`);
    for (const field of ['confirmed', 'reviews', 'not_found', 'queued']) {
        document.getElementById(`progress-${field}-${kind}`).textContent = data[field];
    }
    const state = document.getElementById(`progress-state-${kind}`);
    if (!total) {
        state.textContent = kind === 'artist'
            ? Statify.t('Ouvrez le graphe pour charger les artistes suivis et lancer leur analyse.', 'Open the graph to load followed artists and start analysis.')
            : Statify.t('Ouvrez une playlist pour charger ses titres et lancer leur analyse.', 'Open a playlist to load its tracks and start analysis.');
    } else if (completed === total) {
        state.textContent = data.reviews
            ? Statify.t(`Analyse terminée. ${data.reviews} ${noun}${data.reviews > 1 ? 's' : ''} reste${data.reviews > 1 ? 'nt' : ''} à vérifier manuellement.`, `Analysis complete. ${data.reviews} ${kind === 'artist' ? 'artists' : 'tracks'} still need manual review.`)
            : Statify.t('Analyse terminée. Aucune vérification manuelle nécessaire.', 'Analysis complete. No manual review needed.');
    } else if (data.queued) {
        state.textContent = Statify.t(`${total - completed} ${noun}${total - completed > 1 ? 's' : ''} à traiter ; ${data.queued} tâche${data.queued > 1 ? 's' : ''} dans la file.`, `${total - completed} ${kind === 'artist' ? 'artists' : 'tracks'} to process; ${data.queued} tasks queued.`);
    } else {
        state.textContent = Statify.t('Analyse en attente. Le traitement reprendra quand le processus MusicBrainz sera actif.', 'Analysis is waiting. Processing will resume when the MusicBrainz worker is active.');
    }
}

async function showReviews(kind, total, revision, requestId) {
    const list = document.getElementById(`reviews-list-${kind}`);
    const status = document.getElementById(`reviews-status-${kind}`);
    const page = await fetchJson(`/musicbrainz/reviews?kind=${kind}`);
    if (kind !== activeProgressKind || requestId !== progressRequest) return;
    reviewRevisions[kind] = revision;
    list.replaceChildren();
    status.textContent = total
        ? Statify.t(`${total} correspondance${total > 1 ? 's' : ''} à valider.`, `${total} ${total > 1 ? 'matches' : 'match'} to review.`)
        : Statify.t('Aucune correspondance à valider.', 'No matches to review.');
    for (const match of page.reviews) renderMatch(list, match, match.spotify.name || match.spotify_id);
    let shown = page.reviews.length;
    if (shown >= total) return;
    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'button button-secondary';
    more.textContent = Statify.t('Afficher 20 correspondances supplémentaires', 'Show 20 more matches');
    more.addEventListener('click', async () => {
        more.disabled = true;
        try {
            const next = await fetchJson(`/musicbrainz/reviews?kind=${kind}&offset=${shown}`);
            if (kind !== activeProgressKind || reviewRevisions[kind] !== revision) return;
            more.remove();
            for (const match of next.reviews) renderMatch(list, match, match.spotify.name || match.spotify_id);
            shown += next.reviews.length;
            if (shown < total && next.reviews.length) list.appendChild(more);
        } catch (error) {
            status.textContent = error.message;
        } finally {
            more.disabled = false;
        }
    });
    list.appendChild(more);
}

async function refreshProgress(forceReviews = false) {
    const kind = activeProgressKind;
    const requestId = ++progressRequest;
    try {
        const [progress, status] = await Promise.all([
            fetchJson(`/musicbrainz/progress?kind=${kind}`),
            fetchJson('/musicbrainz/status'),
        ]);
        if (kind !== activeProgressKind || requestId !== progressRequest) return;
        showProgress(kind, progress);
        if (kind === 'artist') {
            const count = status.counts.artists_with_origin;
            const total = status.counts.artists_total;
            const percent = total ? Math.round(100 * count / total) : 0;
            document.getElementById('artist-origin-coverage').textContent = Statify.t(
                `Lieu d’origine connu : ${count}/${total} artistes chargés (${percent} %).`,
                `Known origin: ${count}/${total} loaded artists (${percent}%).`);
        }
        const revision = status.counts.revision;
        if (forceReviews || reviewRevisions[kind] !== revision) {
            await showReviews(kind, status.counts[kind === 'artist' ? 'artists' : 'recordings'].ambiguous, revision, requestId);
        }
    } catch (error) {
        if (kind === activeProgressKind && requestId === progressRequest) document.getElementById(`progress-state-${kind}`).textContent = error.message;
    }
}

for (const kind of progressKinds) {
    const tab = document.getElementById(`tab-${kind}`);
    tab.addEventListener('click', () => selectProgressKind(kind));
    tab.addEventListener('keydown', event => {
        const current = progressKinds.indexOf(kind);
        const next = event.key === 'ArrowRight' ? (current + 1) % progressKinds.length
            : event.key === 'ArrowLeft' ? (current - 1 + progressKinds.length) % progressKinds.length
                : event.key === 'Home' ? 0 : event.key === 'End' ? progressKinds.length - 1 : null;
        if (next === null) return;
        event.preventDefault();
        selectProgressKind(progressKinds[next], true);
    });
}
document.addEventListener('musicbrainz-match-saved', () => refreshProgress(true));
refreshProgress();
window.setInterval(() => refreshProgress(), 8000);
