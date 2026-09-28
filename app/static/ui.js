(() => {
  const storage = (key, value) => {
    try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (_) { return null; }
    return value;
  };
  const locale = () => storage('statify-lang') === 'en' ? 'en' : 'fr';
  const t = (fr, en) => locale() === 'en' ? en : fr;
  const errorCodes = {
    session_expired: 'Your Spotify session expired. Please sign in again.',
    invalid_csrf: 'Invalid security token.',
    not_found: 'Resource not found.',
    playlist_not_found: 'Playlist not found in your library.',
    playlist_required: 'A playlist is required.',
    invalid_sort: 'Unknown sort option.',
    artist_not_found: 'Followed artist not found.',
    match_not_found: 'Match not found.',
    account_changed: 'The Spotify account changed. Please sign in again.',
    artists_not_loaded: 'Load your followed artists first.',
    liked_tracks_denied: 'Access to liked tracks was denied. Sign in again and allow access to your Spotify library.',
    unsupported_format: 'Unsupported format.',
    invalid_offset: 'Invalid offset.',
    invalid_type: 'Unknown type.',
    candidate_not_found: 'Candidate not found.',
    catalog_unavailable: 'Catalogs are temporarily unavailable. Please try again later.',
    invalid_mbid: 'Invalid MBID.',
    invalid_match: 'Invalid match or MBID.',
    playlist_details_required: 'Playlist, sort option and name are required.',
    name_too_long: 'The name is too long.',
    no_exportable_tracks: 'No Spotify track can be added.',
    playlist_partially_created: 'The playlist was created, but adding tracks was interrupted.',
    spotify_forbidden: 'This playlist is not available to your account.',
    spotify_rate_limited: 'Spotify is temporarily limiting requests. Please try again later.',
    spotify_unavailable: 'Spotify is temporarily unavailable.'
  };
  const errorTranslations = {
    'Jeton de sécurité invalide.': 'Invalid security token.',
    'Ressource introuvable.': 'Resource not found.',
    'La playlist a été créée, mais l\'ajout des titres a été interrompu.': 'The playlist was created, but adding tracks was interrupted.',
    'Playlist introuvable dans votre bibliothèque.': 'Playlist not found in your library.',
    'Identifiant de playlist requis.': 'A playlist is required.',
    'Option de tri inconnue.': 'Unknown sort option.',
    'Playlist, tri et nom requis.': 'Playlist, sort option and name are required.',
    'Le nom est trop long.': 'The name is too long.',
    'Aucun titre Spotify ajoutable.': 'No Spotify track can be added.',
    'Type inconnu.': 'Unknown type.',
    'Correspondance introuvable.': 'Match not found.',
    'Candidat introuvable.': 'Candidate not found.',
    'MBID invalide.': 'Invalid MBID.',
    'Correspondance ou MBID invalide.': 'Invalid match or MBID.',
    'Catalogues momentanément indisponibles. Réessayez plus tard.': 'Catalogs are temporarily unavailable. Please try again later.',
    'Décalage invalide.': 'Invalid offset.',
    'Artiste suivi introuvable.': 'Followed artist not found.',
    "Chargez d'abord les artistes suivis.": 'Load your followed artists first.',
    "Accès aux titres likés refusé. Reconnectez-vous et autorisez l'accès à votre bibliothèque Spotify.": 'Access to liked tracks was denied. Sign in again and allow access to your Spotify library.',
    'Session Spotify expirée. Reconnectez-vous.': 'Your Spotify session expired. Please sign in again.',
    "Cette playlist n'est pas accessible avec votre compte.": 'This playlist is not available to your account.',
    'Spotify limite temporairement les requêtes. Réessayez plus tard.': 'Spotify is temporarily limiting requests. Please try again later.',
    'Spotify est momentanément indisponible.': 'Spotify is temporarily unavailable.',
    'Le compte Spotify a changé. Reconnectez-vous.': 'The Spotify account changed. Please sign in again.'
  };
  const translateError = (message, code) => locale() === 'en' ? (errorCodes[code] || errorTranslations[message] || message) : message;
  const translateStatic = () => {
    document.documentElement.lang = locale();
    if (locale() === 'en') {
      const titles = { home: 'Home', sorts: 'Playlists', graphs: 'Graph', map: 'Map', timeline: 'Timeline', progress: 'Progress', settings: 'Settings' };
      document.title = `Statify — ${titles[document.body.id] || 'Music'}`;
    }
    document.querySelectorAll('[data-en]').forEach(el => {
      if (el.dataset.fr === undefined) el.dataset.fr = el.textContent;
      el.textContent = locale() === 'en' ? el.dataset.en : el.dataset.fr;
    });
    for (const [key, attr] of [['enPlaceholder', 'placeholder'], ['enAriaLabel', 'aria-label'], ['enTitle', 'title']]) {
      document.querySelectorAll(`[data-${key.replace(/[A-Z]/g, c => '-' + c.toLowerCase())}]`).forEach(el => {
        const frKey = 'fr' + key.slice(2);
        if (el.dataset[frKey] === undefined) el.dataset[frKey] = el.getAttribute(attr) || '';
        el.setAttribute(attr, locale() === 'en' ? el.dataset[key] : el.dataset[frKey]);
      });
    }
  };
  const panel = () => document.getElementById('detail-panel');
  let previousFocus;
  const closeDrawer = () => {
    const drawer = panel();
    if (!drawer || drawer.hidden) return;
    drawer.hidden = true;
    document.getElementById('detail-backdrop').hidden = true;
    document.body.classList.remove('drawer-open');
    previousFocus?.focus?.();
  };
  const openDrawer = (title, kicker, build) => {
    const drawer = panel();
    if (!drawer) return;
    if (drawer.hidden) previousFocus = document.activeElement;
    document.getElementById('detail-title').textContent = title;
    document.getElementById('detail-kicker').textContent = kicker;
    const content = document.getElementById('detail-content');
    content.replaceChildren();
    if (typeof build === 'function') build(content);
    else if (build instanceof Node) content.appendChild(build);
    else content.textContent = build || '';
    drawer.hidden = false;
    document.getElementById('detail-backdrop').hidden = false;
    document.body.classList.add('drawer-open');
    drawer.focus();
    return content;
  };
  const remember = (key, value) => storage(`statify-context-${key}`, value);
  const recall = key => storage(`statify-context-${key}`);
  const contextualLink = (path, params = {}) => {
    const url = new URL(path, location.origin);
    for (const [key, value] of Object.entries(params)) if (value) url.searchParams.set(key, value);
    return url.pathname + url.search;
  };
  window.Statify = { locale, t, translateError, translateStatic, openDrawer, closeDrawer, remember, recall, contextualLink };
  document.addEventListener('DOMContentLoaded', () => {
    translateStatic();
    const navToggle = document.querySelector('.mobile-nav-toggle');
    navToggle?.addEventListener('click', () => {
      const open = document.getElementById('site-nav').classList.toggle('is-open');
      navToggle.setAttribute('aria-expanded', String(open));
    });
    document.getElementById('detail-close')?.addEventListener('click', closeDrawer);
    document.getElementById('detail-backdrop')?.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', event => {
      const drawer = panel();
      if (!drawer || drawer.hidden) return;
      if (event.key === 'Escape') closeDrawer();
      if (event.key === 'Tab') {
        const focusable = [...drawer.querySelectorAll('a, button, input, select, [tabindex]:not([tabindex="-1"])')].filter(el => !el.disabled && el.getClientRects().length);
        if (!focusable.length) { event.preventDefault(); drawer.focus(); return; }
        const first = focusable[0], last = focusable.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
    document.querySelectorAll('[data-lang-option]').forEach(button => {
      button.addEventListener('click', () => { storage('statify-lang', button.dataset.langOption); location.reload(); });
      button.setAttribute('aria-pressed', String(button.dataset.langOption === locale()));
    });
    const theme = storage('statify-theme') || 'system';
    document.querySelectorAll('[data-theme-option]').forEach(button => {
      button.addEventListener('click', () => { storage('statify-theme', button.dataset.themeOption); document.documentElement.dataset.theme = button.dataset.themeOption; document.querySelectorAll('[data-theme-option]').forEach(item => item.setAttribute('aria-pressed', String(item === button))); });
      button.setAttribute('aria-pressed', String(button.dataset.themeOption === theme));
    });
    if (document.body.id === 'home') {
      fetch('/home/summary').then(response => { if (!response.ok) throw Error(); return response.json(); }).then(data => {
        for (const key of ['playlists', 'artists', 'tracks']) {
          if (data[key] === null) continue;
          document.getElementById(`home-${key}`).textContent = data[key].toLocaleString(locale());
          document.getElementById(`home-${key}-note`).textContent = t('Données locales', 'Local data');
        }
      }).catch(() => { document.querySelectorAll('.summary-card small').forEach(el => { el.textContent = t('Aperçu indisponible', 'Overview unavailable'); }); });
    }
  });
})();
