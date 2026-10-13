"use strict";

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const staticDir = path.join(__dirname, '..', 'app', 'static');
const TimelineCore = require(path.join(staticDir, 'timeline_core.js'));
const timelineScript = fs.readFileSync(path.join(staticDir, 'timeline.js'), 'utf8');

class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.style = {};
    this.listeners = {};
    this.className = '';
    this.textContent = '';
    this.hidden = false;
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren() { this.children = []; }
  addEventListener(event, listener) { this.listeners[event] = listener; }
  setAttribute(name, value) { this[name] = value; }
}

async function renderCase(artists, catalogues) {
  const ids = ['timeline-status', 'timeline-retry', 'timeline-chart', 'timeline-names',
    'timeline-plot', 'timeline-axis', 'timeline-rows'];
  const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
  const opened = [];
  const context = {
    TimelineCore,
    document: {
      getElementById: id => elements[id],
      createElement: tag => new Element(tag),
    },
    window: { addEventListener() {} },
    clearInterval() {},
    Statify: {
      t: fr => fr,
      openDrawer: title => opened.push(title),
    },
    fetch: async url => ({
      ok: true,
      json: async () => url === '/timeline/data' ? { artists }
        : url.startsWith('/timeline/artist/') ? {
          releases: catalogues[decodeURIComponent(url.split('/')[3])] || [], next_offset: null,
        } : {},
    }),
  };
  context.VisualizationSource = {
    ready: true, generation: 1,
    controller: { abort() {} },
    init(callback) { callback(true); },
    request: async url => (await context.fetch(url)).json(),
  };
  vm.runInNewContext(timelineScript, context);
  for (let i = 0; i < 20 && !elements['timeline-status'].textContent.includes(`${artists.length}/${artists.length}`); i++) {
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.match(elements['timeline-status'].textContent, new RegExp(`${artists.length}/${artists.length}`));
  return { elements, opened };
}

function artist(id, name, dates) {
  return { id, name, dates, match_status: 'confirmed' };
}

function release(id, date, category = 'album') {
  return { id, title: id, date, category, url: `https://open.spotify.com/album/${id}` };
}

function rows(elements) {
  return elements['timeline-rows'].children.filter(child => child.className === 'timeline-row');
}

(async () => {
  const mixed = await renderCase([
    artist('unknown-b', 'Zulu', null),
    artist('active', 'Active', { begin: '2000', end: null, ended: false }),
    artist('unknown-a', 'Alpha', { begin: null, end: null, ended: false }),
  ], {
    'unknown-a': [release('early-ep', '1970', 'album')],
    'unknown-b': [release('later-single', '1990', 'single')],
    active: [release('active-album', '2010')],
  });
  assert.equal(mixed.elements['timeline-chart'].hidden, false);
  assert.deepEqual(mixed.elements['timeline-names'].children.map(node => node.textContent),
    ['Active', 'Alpha', 'Zulu']);
  assert.equal(mixed.elements['timeline-axis'].children[0].textContent, '1970');
  assert.equal(rows(mixed.elements).length, 3);
  assert.equal(rows(mixed.elements)[0].children.filter(node => node.className.includes('timeline-life')).length, 1);
  for (const row of rows(mixed.elements).slice(1)) {
    assert.equal(row.children.filter(node => node.className.includes('timeline-life')).length, 0);
    assert.equal(row.children.filter(node => node.className.includes('timeline-marker')).length, 1);
  }
  const earlyMarker = rows(mixed.elements)[1].children[0];
  assert.equal(earlyMarker.style.left, '0px');
  assert.match(earlyMarker.className, /timeline-album/);
  earlyMarker.listeners.click();
  assert.deepEqual(mixed.opened, ['early-ep']);

  const undatedOnly = await renderCase([
    artist('no-releases', 'No releases', null),
    artist('first', 'First', null),
  ], { first: [release('compilation', '1985', 'compilation')] });
  assert.equal(undatedOnly.elements['timeline-chart'].hidden, false);
  assert.deepEqual(undatedOnly.elements['timeline-names'].children.map(node => node.textContent),
    ['First', 'No releases']);
  assert.equal(undatedOnly.elements['timeline-axis'].children[0].textContent, '1985');
  assert.deepEqual(rows(undatedOnly.elements).map(row => row.children.length), [1, 0]);
})().catch(error => { console.error(error); process.exitCode = 1; });
