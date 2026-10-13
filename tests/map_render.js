'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
class Element {
    constructor(tag = '') { this.tag = tag; this.textContent = ''; this.children = []; this.dataset = {}; this.listeners = {}; this.open = false; }
    appendChild(child) { this.children.push(child); }
    replaceChildren() { this.children = []; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    querySelectorAll() {
        return this.children.flatMap(child => [...(child.tag === 'details' && child.open ? [child] : []), ...child.querySelectorAll()]);
    }
}
const elements = Object.fromEntries([...input.html.matchAll(/\bid="([^"]+)"/g)].map(([, id]) => [id, new Element()]));
elements['map-list-sort'].value = 'alphabetical';
let language = 'fr';
let sourceChanged;
let drawer;
const leafletLayer = () => ({ addTo() { return this; }, clearLayers() {}, eachLayer() {}, addData() {} });
const context = vm.createContext({
    document: { getElementById: id => elements[id], documentElement: { dataset: {} }, hidden: false },
    L: { map: () => ({ setView() {}, getZoom: () => 2 }), geoJSON: leafletLayer, layerGroup: leafletLayer,
        circleMarker: () => ({ bindTooltip() {}, on() {}, addTo() {} }) },
    Statify: { t: (fr, en) => language === 'en' ? en : fr, locale: () => language,
        recall() {}, remember() {}, contextualLink: () => '/graphs',
        openDrawer: (title, kicker, build) => { drawer = { title, content: new Element() }; build(drawer.content); } },
    appendText: (parent, tag, text) => { const child = new Element(tag); child.textContent = text; parent.appendChild(child); return child; },
    VisualizationSource: { init: callback => { sourceChanged = callback; }, controller: { abort() {} } },
    fetch: () => new Promise(() => {}),
    window: { setInterval() {}, addEventListener() {} },
    MutationObserver: class { observe() {} }, matchMedia: () => ({ addEventListener() {} }),
    Intl, URLSearchParams, location: { search: '' }, clearInterval() {},
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/static/map.js'), 'utf8'), context);
const place = (id, label, code, continent, names, precision = 'city') => ({
    id, label, country_code: code, country: code ? code : null, continent, precision, latitude: 1, longitude: 2,
    artists: names.map((name, i) => ({ id: `${id}-${i}`, name, precision })),
});
const markers = [
    place('paris', 'Paris', 'FR', 'Europe', ['Zulu', 'Émile', 'alice']),
    place('evry', 'Évry', 'FR', 'Europe', ['B']),
    place('country:FR', 'France', 'FR', 'Europe', ['C'], 'country'),
    place('zurich', 'Zurich', 'CH', 'Europe', ['D', 'E']),
    place('africa', 'Cape Town', 'ZA', 'Africa', ['F']),
    place('unknown', 'Mystery', null, null, ['G']),
    place('oslo', 'Oslo', 'NO', 'Europe', ['H', 'I']),
];
const data = { markers, counts: { total: 12, city: 10, country: 1, unknown: 1, pending: 0 } };
function render(value) { context.data = value; vm.runInContext('renderData(data)', context); }
const root = elements['map-place-list'];
const summary = item => item.children[0].children[0].textContent;
const names = items => items.map(summary);
const europe = () => root.children.find(column => column.children[0].textContent === 'Europe');
const countries = () => europe().children.slice(1);
const france = () => countries().find(country => summary(country) === 'France');
const cities = () => france().children.slice(1);
render(data);
assert.deepEqual(root.children.map(column => column.children[0].textContent), ['Afrique', 'Europe', 'Continent inconnu']);
assert.equal(europe().children[0].children[0].textContent, '9 artistes');
assert.deepEqual(names(countries()), ['France', 'Norvège', 'Suisse']);
assert.deepEqual(names(cities()), ['Évry', 'Paris', 'Ville inconnue']);
assert.equal(france().children[0].children[1].textContent, '5 artistes');
assert.equal(root.children.at(-1).children[1].children[0].children[0].textContent, 'Pays inconnu');
const paris = cities().find(city => summary(city) === 'Paris');
assert.deepEqual(paris.children[1].children.map(item => item.children[0].textContent), ['alice', 'Émile', 'Zulu']);
paris.children[1].children[1].children[0].listeners.click();
assert.equal(drawer.title, 'Émile');
france().open = true;
paris.open = true;
elements['map-list-sort'].value = 'count';
elements['map-list-sort'].listeners.change();
assert.deepEqual(names(countries()), ['France', 'Norvège', 'Suisse']);
assert.deepEqual(names(cities()), ['Paris', 'Évry', 'Ville inconnue']);
assert.equal(france().open, true);
assert.equal(cities()[0].open, true);
// Polling adds artists and changes totals without collapsing previously opened groups.
render({ ...data, markers: [...markers, place('geneva', 'Genève', 'CH', 'Europe', ['J', 'K', 'L', 'M'])] });
assert.deepEqual(names(countries()), ['Suisse', 'France', 'Norvège']);
assert.equal(france().open, true);
assert.equal(cities()[0].open, true);
assert.equal(europe().children[0].children[0].textContent, '13 artistes');
// An unchanged response preserves the actual elements, including their open state.
vm.runInContext('renderData(data)', context);
const unchanged = root.children[0];
vm.runInContext('renderData(data)', context);
assert.equal(root.children[0], unchanged);
sourceChanged(false);
render(data);
assert.equal(elements['map-list-sort'].value, 'count');
assert.equal(france().open, false);
assert.equal(cities()[0].open, false);
language = 'en';
elements['map-list-sort'].listeners.change();
assert.equal(root.children.at(-1).children[0].textContent, 'Unknown continent');
assert.equal(cities().at(-1).children[0].children[0].textContent, 'Unknown city');
render({ markers: [], counts: { total: 0, city: 0, country: 0, unknown: 0, pending: 0 } });
assert.equal(root.children.length, 1);
assert.equal(root.children[0].textContent, 'No confirmed places to show.');
assert.equal(elements['map-status'].textContent, 'No artists in this source.');
