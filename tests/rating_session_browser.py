"""Sequential rating browser validation, synthetic isolated data + mocked Spotify."""
import os
import sys
import threading
import tempfile
from pathlib import Path
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from flask import redirect, session
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
(ROOT/'work').mkdir(exist_ok=True)
from app import create_app, spotify_auth, storage
from app.services import ia_store
app=create_app({'TESTING':True,'DATA_DIR':tempfile.mkdtemp(prefix='rating-session-',dir=ROOT/'work'),'SECRET_KEY':'synthetic'})
spotify_auth.is_valid_token=lambda:False
spotify_auth.get_spotify=lambda:(_ for _ in ()).throw(RuntimeError('Unexpected Spotify request'))
with app.app_context(),storage.connect() as db:
    for user in ['frdark','frlight','enlight','endark']:
        for i in range(25):
            ia_store.upsert_track(db,user,{'uri':f'spotify:track:sample{i}','spotify_id':f'sample{i}','name':f'Music {i}','artist_names':[f'Artist {i}']})
@app.get('/__login/<user>')
def login(user):
    session['spotify_user_id']=user
    session['csrf_token']='test'
    return redirect('/ia#ratings')
server=make_server('127.0.0.1',5099,app,threaded=True)
threading.Thread(target=server.serve_forever,daemon=True).start()
stub='''window.mockPlayers=[];window.onSpotifyIframeApiReady({createController(el,opts,cb){
const frame=document.createElement('iframe');frame.src='https://open.spotify.com/embed/track/'+opts.uri.split(':').pop();frame.width='100%';frame.height=opts.height;el.replaceWith(frame);
const listeners={};const p={uri:opts.uri,isPaused:true,playCalls:0,pauseCalls:0,toggleCalls:0,loads:[opts.uri],
loadEntity(uri){this.uri=uri;this.loads.push(uri);frame.dataset.uri=uri;listeners.ready?.();},
play(){this.playCalls++;if(window.blockPlayback)throw Error('Blocked');this.isPaused=false;listeners.playback_started?.({data:{playingURI:this.uri}});},
pause(){this.pauseCalls++;this.isPaused=true;},togglePlay(){this.toggleCalls++;this.isPaused=!this.isPaused;},
addListener(event,handler){listeners[event]=handler;if(event==='ready')setTimeout(handler,0);}};window.mockPlayers.push(p);cb(p);}});'''
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,**({'executable_path':os.environ['STATIFY_TEST_CHROMIUM']} if os.environ.get('STATIFY_TEST_CHROMIUM') else {}))
        for lang,theme,width in [('fr','dark',1440),('fr','light',390),('en','light',1440),('en','dark',390)]:
            context=browser.new_context(viewport={'width':width,'height':1100})
            page=context.new_page()
            errors=[]
            posts=[]
            batches=[]
            enrichments=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda req: posts.append(req.post_data_json) if req.url.endswith('/annotations') else batches.append(req.post_data_json) if req.url.endswith('/candidates') else enrichments.append(req.post_data_json) if req.url.endswith('/enrich') else None)
            page.route('https://open.spotify.com/embed/**',lambda r:r.fulfill(content_type='text/html',body='<html><body>Spotify player fixture</body></html>'))
            page.route('https://open.spotify.com/embed/iframe-api/v1',lambda r:r.fulfill(content_type='application/javascript',body=stub))
            page.goto(f'http://127.0.0.1:5099/__login/{lang}{theme}')
            page.evaluate('(x)=>{localStorage.setItem("statify-lang",x[0]);localStorage.setItem("statify-theme",x[1]);}',[lang,theme])
            page.reload()
            panel=page.locator('[data-ia-panel=ratings]')
            card=page.locator('.ia-single-rating')
            assert page.locator('#ia-rating-filters').evaluate('(e)=>e.open')
            page.wait_for_function('document.querySelector("#ia-rating-counts").children.length===4')
            assert panel.locator('.ia-badge').count()==4
            page.locator('#ia-candidates-form button').click()
            page.wait_for_function('window.mockPlayers?.length===1 && window.mockPlayers[0].loads.length>0')
            page.wait_for_function('document.querySelector("#ia-status").textContent.length>0')
            assert len(enrichments)==1 and enrichments[0]['automatic'] and enrichments[0]['spotify']
            assert len(enrichments[0]['uris'])==12
            assert not page.locator('#ia-rating-filters').evaluate('(e)=>e.open')
            assert card.locator('button[data-rating]').count()==4
            assert card.locator('iframe').evaluate('(e)=>e.getBoundingClientRect().height')==80
            assert page.evaluate('window.mockPlayers[0].playCalls')==0
            assert page.evaluate('window.mockPlayers[0].loads.length')==1
            page.evaluate('window.originalFrame=document.querySelector(".ia-embedded-player iframe")')
            # Media/volume keys and modified arrows retain native behavior.
            card.focus()
            assert page.evaluate("""()=>['AudioVolumeUp','AudioVolumeDown','AudioVolumeMute','MediaPlayPause'].every(key=>{
                const e=new KeyboardEvent('keydown',{key,bubbles:true,cancelable:true});document.activeElement.dispatchEvent(e);return !e.defaultPrevented;
            })""")
            page.keyboard.press('Shift+ArrowUp')
            page.keyboard.press('Control+ArrowDown')
            assert not posts
            assert page.evaluate("""()=>{const e=new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true,cancelable:true});
                Object.defineProperty(e,'getModifierState',{value:key=>key==='Fn'});document.activeElement.dispatchEvent(e);return !e.defaultPrevented;}""")
            page.evaluate('document.body.tabIndex=-1;document.body.focus()')
            page.keyboard.press('ArrowUp')
            assert not posts
            # A custom volume slider outside the card also keeps its arrow commands.
            page.evaluate('const slider=document.createElement("div");slider.id="volume-fixture";slider.tabIndex=0;slider.setAttribute("role","slider");document.body.append(slider);slider.focus()')
            assert page.evaluate("""()=>{const e=new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true,cancelable:true});document.activeElement.dispatchEvent(e);return !e.defaultPrevented;}""")
            assert not posts
            page.evaluate('document.querySelector("#volume-fixture").remove()')
            card.focus()
            page.keyboard.press('Space')
            assert page.evaluate('window.mockPlayers[0].toggleCalls')==1
            card.locator('.ia-player-hint').click()
            assert page.evaluate('window.mockPlayers[0].toggleCalls')==2
            card.locator('h3 a').evaluate('(e)=>e.addEventListener("click",event=>event.preventDefault())')
            card.locator('h3 a').click()
            assert page.evaluate('window.mockPlayers[0].toggleCalls')==2
            page.locator('#ia-rating-filters > summary').click()
            page.locator('#ia-artist').focus()
            page.keyboard.press('ArrowLeft')
            assert not posts
            page.locator('#ia-rating-filters > summary').click()
            card.focus()
            # Network error must leave the original title and old opinion intact.
            title=card.locator('h3').inner_text()
            page.route('**/ia/api/annotations',lambda r:r.fulfill(status=500,json={'error':'Synthetic save failure'}),times=1)
            page.keyboard.press('ArrowLeft')
            page.wait_for_function('document.querySelector(".ia-session-notice").textContent.includes("Synthetic")')
            assert card.locator('h3').inner_text()==title and card.locator('[aria-pressed=true]').count()==0
            def wait_count(n):
                page.wait_for_function('(n)=>document.querySelector(".ia-session-progress").textContent.endsWith(n+" "+(localStorage.getItem("statify-lang")==="en"?"tracks rated":"titres traités"))',arg=n)
            # A held key and duplicate synchronous clicks must produce one save only.
            card.focus()
            page.keyboard.down('ArrowLeft')
            wait_count(1)
            page.evaluate('document.querySelector(".ia-single-rating").dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowLeft",repeat:true,bubbles:true}))')
            page.evaluate('document.querySelector(".ia-single-rating").dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowLeft",bubbles:true}))')
            page.wait_for_timeout(100)
            assert len(posts)==2
            page.keyboard.up('ArrowLeft')
            assert page.evaluate('window.mockPlayers[0].playCalls')>=1
            page.evaluate('window.blockPlayback=true')
            page.keyboard.press('ArrowUp')
            wait_count(2)
            assert ('did not start' if lang=='en' else 'n’a pas démarré') in card.locator('.ia-player-hint').inner_text()
            page.evaluate('window.blockPlayback=false')
            toggles=page.evaluate('window.mockPlayers[0].toggleCalls')
            page.keyboard.press('Space')
            assert page.evaluate('window.mockPlayers[0].toggleCalls')==toggles+1
            for key,n in [('ArrowDown',3),('ArrowRight',4)]:
                page.keyboard.press(key)
                wait_count(n)
            assert [p['label'] for p in posts[1:5]]==['dislike','like','neutral','love']
            pending_title=card.locator('h3').inner_text()
            page.locator('.ia-session-navigation button').first.click()
            assert card.locator('[data-rating=love]').get_attribute('aria-pressed')=='true'
            with page.expect_response('**/ia/api/annotations'):
                card.locator('[data-rating=neutral]').click()
            page.wait_for_function('(s)=>document.querySelector(".ia-session-details h3").textContent===s',arg=pending_title)
            assert len(posts)==6
            wait_count(4)
            page.locator('.ia-session-navigation button').first.click()
            page.locator('.ia-session-navigation button').last.click()
            assert card.locator('h3').inner_text()==pending_title
            # A failed correction preserves the known rating too.
            page.locator('.ia-session-navigation button').first.click()
            page.route('**/ia/api/annotations',lambda r:r.fulfill(status=500,json={'error':'Synthetic correction failure'}),times=1)
            card.locator('[data-rating=love]').click()
            page.wait_for_function('document.querySelector(".ia-session-notice").textContent.includes("Synthetic correction")')
            assert card.locator('[data-rating=neutral]').get_attribute('aria-pressed')=='true'
            page.locator('.ia-session-navigation button').last.click()
            count=len(posts)
            card.locator('[data-rating=like]').evaluate('(e)=>{e.click();e.click();}')
            wait_count(5)
            assert len(posts)==count+1
            # Background shortcut must stop working on other tabs.
            page.locator('[data-tab=data]').click()
            page.locator('[data-ia-panel=data] h2').first.click()
            count=len(posts)
            page.keyboard.press('ArrowRight')
            assert len(posts)==count
            assert page.evaluate('window.mockPlayers[0].isPaused')
            page.locator('[data-tab=ratings]').click()
            card.focus()
            # Dialogs and playlist menus suspend shortcuts, including outside focus.
            page.evaluate('document.querySelector("#detail-panel").hidden=false')
            page.keyboard.press('ArrowLeft')
            assert len(posts)==count
            page.evaluate('document.querySelector("#detail-panel").hidden=true')
            page.locator('#ia-rating-filters > summary').click()
            page.locator('.ia-playlist-picker summary').click()
            card.focus()
            page.keyboard.press('ArrowLeft')
            assert len(posts)==count
            page.evaluate('document.querySelector(".ia-playlist-picker").open=false')
            page.locator('#ia-rating-filters > summary').click()
            card.focus()
            batch_failure=lang=='fr' and theme=='dark'
            for n in range(6,26):
                if n==12 and batch_failure:
                    page.route('**/ia/api/candidates',lambda r:r.fulfill(status=503,json={'error':'Synthetic batch failure'}),times=1)
                page.keyboard.press('ArrowRight')
                wait_count(n)
                if n==12:
                    if batch_failure:
                        assert card.is_hidden()
                        page.locator('.ia-rating-session > button').click()
                    page.wait_for_function('document.querySelector(".ia-session-progress").textContent.includes("2 · 1 / 12")')
                    page.locator('.ia-session-navigation button').first.click()
                    assert '1 · 12 / 12' in page.locator('.ia-session-progress').inner_text()
                    page.locator('.ia-session-navigation button').last.click()
                    card.focus()
            page.wait_for_function('document.querySelector(".ia-session-notice").textContent.includes(localStorage.getItem("statify-lang")==="en"?"Session complete":"Session terminée")')
            assert len(batches)==4+int(batch_failure) and [len(b['exclude_uris']) for b in batches]==([0,12,12,24,25] if batch_failure else [0,12,24,25])
            assert card.is_hidden() and page.locator('.ia-embedded-player iframe').count()==1
            assert page.evaluate('window.originalFrame===document.querySelector(".ia-embedded-player iframe") && window.mockPlayers.length===1')
            assert len(enrichments)==3
            assert page.evaluate('window.mockPlayers[0].loads.every((uri,i,loads)=>i===0||uri!==loads[i-1])')
            # Cross-batch Previous and correction preserve the completed session.
            page.locator('.ia-session-navigation button').first.click()
            assert card.is_visible()
            card.locator('[data-rating=dislike]').click()
            page.wait_for_function('document.querySelector(".ia-single-rating").hidden')
            assert len(batches)==4+int(batch_failure)
            # Review includes all rated tracks but must exhaust too.
            page.locator('#ia-rating-filters > summary').click()
            page.locator('#ia-mode').select_option('review')
            page.locator('#ia-candidates-form button').click()
            page.wait_for_function('!document.querySelector(".ia-single-rating").hidden')
            page.wait_for_function('document.querySelector(".ia-session-progress").textContent.includes("0 ")')
            page.wait_for_timeout(50)
            card.focus()
            for n in range(1,26):
                page.keyboard.press('ArrowDown')
                wait_count(n)
            page.wait_for_function('document.querySelector(".ia-single-rating").hidden')
            assert len(batches)==8+int(batch_failure)
            # Start fresh review: same controller, starts paused, selected color and responsive layout.
            page.locator('#ia-rating-filters > summary').click()
            page.locator('#ia-candidates-form button').click()
            page.wait_for_function('!document.querySelector(".ia-single-rating").hidden')
            assert page.evaluate('window.mockPlayers[0].isPaused')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert card.locator('[data-rating=neutral]').get_attribute('aria-pressed')=='true'
            page.locator('[data-ia-panel=ratings] h2').click()
            assert card.locator('[data-rating=neutral]').evaluate('(e)=>getComputedStyle(e).outlineStyle')=='none'
            page.screenshot(path=str(ROOT/f'work/ia-session-page-{lang}-{theme}-{width}.png'),full_page=True)
            card.screenshot(path=str(ROOT/f'work/ia-sequential-{lang}-{theme}-{width}.png'))
            assert not errors,errors
            print(lang,theme,width,'OK',flush=True)
            context.close()
        browser.close()
finally:
    server.shutdown()
