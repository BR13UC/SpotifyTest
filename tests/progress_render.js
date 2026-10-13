'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
class Element {
    constructor() { this.textContent = ''; this.children = []; this.listeners = {}; }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    replaceChildren() { this.children = []; }
    appendChild(child) { this.children.push(child); }
    focus() {}
}
const elements = Object.fromEntries([...input.html.matchAll(/\bid="([^"]+)"/g)]
    .map(([, id]) => [id, new Element()]));
const progress = { total: 4, completed: 3, confirmed: 1, reviews: 1, not_found: 1, queued: 1, deferred: 2,
    analysis: { name: 'Artist in progress', phase: 'candidates', done: 3, total: 10 },
    errors: { attempts: 4, failed: 2, percent: 50, since: '2026-10-10T08:00:00+00:00' } };
const context = vm.createContext({
    document: {
        getElementById: id => elements[id] || null,
        createElement: () => new Element(),
        addEventListener() {},
    },
    window: { setInterval() {} },
    Statify: { t: fr => fr, locale: () => 'fr' },
    fetchJson: async url => url.startsWith('/musicbrainz/progress') ? progress
        : url === '/musicbrainz/status' ? { counts: { revision: 'one', artists_total: 4, artists_with_origin: 1,
            artists: { ambiguous: 1 }, recordings: { ambiguous: 1 } } }
        : { reviews: [{ spotify: { name: 'Review' } }] },
    renderMatch: (parent, _, name) => { parent.appendChild({ textContent: name }); },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/static/progress.js'), 'utf8'), context);
(async () => {
    await vm.runInContext('refreshProgress()', context);
    for (const kind of ['recording', 'artist']) {
        await vm.runInContext(`activeProgressKind = '${kind}'; refreshProgress()`, context);
        assert.equal(elements[`progress-percent-${kind}`].textContent, '75 %');
        assert.equal(elements[`progress-not-found-${kind}`].textContent, 1);
        assert.equal(elements[`progress-queued-${kind}`].textContent, 1);
        assert.equal(elements[`progress-deferred-${kind}`].textContent, 2);
        assert.match(elements[`progress-state-${kind}`].textContent, /1 tâche dans la file\. 2 reportés de 24 h\./);
        assert.match(elements[`progress-errors-${kind}`].textContent, /50 %/);
        assert.match(elements[`progress-errors-${kind}`].textContent, /2\/4/);
        assert.doesNotMatch(elements[`progress-state-${kind}`].textContent, /null|textContent/);
        if (kind === 'artist') assert.match(elements[`progress-state-${kind}`].textContent, /Artist in progress.*3\/10/);
        assert.equal(elements[`reviews-list-${kind}`].children[0].textContent, 'Review');
        vm.runInContext(`showProgress('${kind}', { total:0, completed:0, errors:{attempts:0,failed:0,percent:null} })`, context);
        assert.match(elements[`progress-errors-${kind}`].textContent, /aucune tentative/);
        vm.runInContext(`showProgress('${kind}', { total:1, completed:1, reviews:0, errors:{attempts:1,failed:0,percent:0} })`, context);
        assert.match(elements[`progress-errors-${kind}`].textContent, /0 %/);
    }
    assert.match(elements['artist-origin-coverage'].textContent, /1\/4/);
})().catch(error => { console.error(error); process.exitCode = 1; });
