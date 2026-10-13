"""Browser smoke test for the IA UI with synthetic account data only."""
import json
import os
import sys
import threading
from pathlib import Path
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from app import create_app, spotify_auth, storage
from app.services import ia_data, ia_learning, ia_store, ia_worker
from test_ia import seed, small_grid
from flask import redirect, session

app = create_app({'TESTING': True, 'DATA_DIR': str(ROOT/'work/ia-ui-data'), 'SECRET_KEY': 'synthetic-ui'})
spotify_auth.is_valid_token = lambda: False
spotify_auth.get_spotify = lambda: (_ for _ in ()).throw(RuntimeError('No Spotify network in UI smoke test'))
with app.app_context():
    already = ia_store.tracks('alice')
if not already:
    seed(app)
    with app.app_context(), storage.connect() as db:
        for i in range(15):
            ia_store.upsert_track(db, 'alice', {'uri':f'spotify:track:unrated{i}', 'spotify_id':f'unrated{i}', 'name':f'Une découverte {i}', 'artist_names':[f'Artiste {i}']})
    with app.app_context():
        storage.set_cache('alice','profile',{'id':'alice','display_name':'Compte local'})
        items=ia_store.tracks('alice')[:12]
        storage.set_cache('alice','playlists',[{'id':'p','name':'Ma playlist'}])
        storage.replace_playlist_items('alice','p',items+items[:2])
        ia_data.sync_local('alice')
        key=ia_learning.launch('alice',small_grid())
        while ia_worker.process_one():
            pass
        chosen=ia_learning.results('alice')['results'][0]
        ia_learning.finalize('alice',chosen['campaign_id'],chosen['config_key'])
        ia_worker.process_one()
        ia_learning.activate('alice',ia_learning.results('alice')['models'][0]['id'])

@app.get('/__test_login')
def login():
    session['spotify_user_id']='alice'
    session['csrf_token']='test'
    return redirect('/ia')

server=make_server('127.0.0.1',5098,app,threaded=True)
threading.Thread(target=server.serve_forever,daemon=True).start()
results=[]
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,**({'executable_path':os.environ['STATIFY_TEST_CHROMIUM']} if os.environ.get('STATIFY_TEST_CHROMIUM') else {}))
        for language,theme,width in [('fr','light',1440),('fr','dark',390),('en','dark',1440),('en','light',390)]:
            context=browser.new_context(viewport={'width':width,'height':1100})
            page=context.new_page()
            errors=[]
            page.on('pageerror',lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:5098/__test_login')
            page.evaluate('(x)=>{localStorage.setItem("statify-lang",x[0]);localStorage.setItem("statify-theme",x[1]);}',[language,theme])
            page.reload()
            page.wait_for_function('document.getElementById("ia-grid-preview").textContent.includes("88")')
            for tab in ['data','ratings','campaigns','results']:
                page.locator(f'[data-tab="{tab}"]').click()
                if tab=='ratings':
                    page.locator('#ia-mode').select_option('evaluation')
                    page.locator('#ia-candidates-form button').click()
                    page.wait_for_function('document.querySelectorAll("#ia-candidates article").length > 0')
                    first=page.locator('#ia-candidates article').first
                    first.locator('.ia-rating-actions button').nth(2).click()
                    page.wait_for_function('document.querySelector("#ia-candidates article .ia-rating-actions button:nth-child(3)").getAttribute("aria-pressed") === "true"')
                if tab=='results':
                    page.wait_for_selector('#ia-results-table tbody tr')
                    page.locator('#ia-results-table input[type=checkbox]').first.check()
                    assert page.locator('#ia-comparison details').count()>0
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),(language,width,tab)
                page.screenshot(path=str(ROOT/f'work/ia-{tab}-{language}-{width}.png'),full_page=True)
            page.goto('http://127.0.0.1:5098/sorts')
            page.wait_for_selector('#ia-collection option',state='attached')
            page.locator('#ia-predict').click()
            page.wait_for_selector('#ia-predictions article')
            assert page.locator('#ia-predictions section').count()==5
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(ROOT/f'work/ia-playlists-{language}-{width}.png'),full_page=True)
            assert not errors,errors
            results.append({'language':language,'theme':theme,'width':width,'errors':errors,'responsive':True})
            context.close()
        browser.close()
finally:
    server.shutdown()
(ROOT/'work/ia-ui-validation.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results))
