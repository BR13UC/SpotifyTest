'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
    constructor() { this.value = ''; this.options = []; this.listeners = {}; }
    add(option) { this.options.push(option); }
    replaceChildren() { this.options = []; }
    addEventListener(event, callback) { this.listeners[event] = callback; }
}

async function main() {
    const elements = Object.fromEntries(['artist-source', 'artist-source-playlist',
        'artist-source-playlist-controls', 'artist-source-status'].map(id => [id, new Element()]));
    const memory = new Map([['source', 'liked'], ['source-playlist', 'alice-playlist']]);
    const events = [];
    const links = ['/graphs', '/map', '/timeline'].map(href => ({ href }));
    let resolveOld;
    let oldSignal;
    const context = {
        URL, URLSearchParams, AbortController, DOMException,
        Option: function(text, value) { this.textContent = text; this.value = value; },
        location: { href: 'http://localhost/map?source=followed&artist=one', origin: 'http://localhost', search: '?source=followed&artist=one' },
        document: { getElementById: id => elements[id], querySelectorAll: () => links },
        Statify: {
            t: fr => fr, closeDrawer() {},
            recall: key => memory.get(key), remember: (key, value) => memory.set(key, value),
            contextualLink: (pathname, params) => {
                const url = new URL(pathname, 'http://localhost');
                for (const [key, value] of Object.entries(params)) if (value) url.searchParams.set(key, value);
                return url.pathname + url.search;
            },
        },
        fetchJson: async (url, options) => {
            const parsed = new URL(url, 'http://localhost');
            if (parsed.pathname === '/playlists/get_playlists') return { playlists: [{ id: 'alice-playlist', name: 'Alice' }] };
            if (parsed.searchParams.get('source') === 'liked') {
                oldSignal = options.signal;
                return new Promise(resolve => { resolveOld = resolve; });
            }
            if (parsed.searchParams.get('playlist_id') === 'missing') throw new Error('Playlist introuvable');
            return { artists: [] };
        },
    };
    context.window = context;
    context.history = { replaceState: (_, __, url) => {
        context.location.href = url.href;
        context.location.search = url.search;
    } };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../app/static/artist_source.js'), 'utf8'), context);
    const source = context.VisualizationSource;
    await source.init(ready => events.push([source.source, ready]));
    assert.equal(source.source, 'followed'); // URL overrides remembered choice.
    assert.equal(source.ready, true);
    assert.equal(elements['artist-source-playlist-controls'].hidden, true);
    assert.equal(new URL(context.location.href).searchParams.get('artist'), 'one');

    source.source = 'liked';
    const stale = source.change();
    assert.equal(source.ready, false);
    source.source = 'playlist';
    await source.change();
    assert.equal(source.ready, true);
    assert.equal(source.playlistId, 'alice-playlist');
    assert.equal(elements['artist-source-playlist-controls'].hidden, false);
    assert.equal(oldSignal.aborted, true);
    resolveOld({ artists: [{ id: 'stale' }] });
    await stale;
    assert.equal(events.filter(([name, ready]) => name === 'liked' && ready).length, 0);
    assert.equal(memory.get('source'), 'playlist');
    assert.equal(memory.get('artist'), '');
    for (const link of links) {
        const url = new URL(link.href, 'http://localhost');
        assert.equal(url.searchParams.get('source'), 'playlist');
        assert.equal(url.searchParams.get('playlist_id'), 'alice-playlist');
    }

    // An unavailable explicit playlist reports an error without choosing another.
    source.playlistId = 'missing';
    await source.change();
    assert.equal(source.ready, false);
    assert.equal(source.playlistId, 'missing');
    assert.equal(elements['artist-source-playlist'].disabled, false);
    assert.equal(elements['artist-source-status'].textContent, 'Playlist introuvable');
    assert.equal(memory.get('source'), 'playlist');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
