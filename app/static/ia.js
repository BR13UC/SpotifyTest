/* Local AI laboratory. All external/user text is rendered with textContent. */
(() => {
  const labels = () => ({dislike:T("Je n’aime pas",'Dislike'),neutral:T('Neutre','Neutral'),like:T("J’aime",'Like'),love:T("J’adore",'Love'),unknown:T('Je ne sais pas','Unknown')});
  const $ = id => document.getElementById(id);
  const text = (parent, tag, value, cls) => {const e=document.createElement(tag); e.textContent=value; if(cls)e.className=cls; parent.appendChild(e); return e;};
  const button = (parent, title, action) => {const b=text(parent,'button',title,'button button-secondary'); b.type='button'; b.addEventListener('click',()=>guard(action)); return b;};
  const spotifyLink = (parent, title, path) => {const link=text(parent,'a',title);link.href='https://open.spotify.com/'+path;link.target='_blank';link.rel='noopener';return link;};
  const table = (parent, headers, rows) => {const t=document.createElement('table'); t.className='ia-table'; const h=t.createTHead().insertRow(); headers.forEach(x=>text(h,'th',x)); const body=t.createTBody(); rows.forEach(values=>{const row=body.insertRow();values.forEach(value=>{const cell=row.insertCell();if(value instanceof Node)cell.appendChild(value);else cell.textContent=value??'—';});});parent.appendChild(t);};
  const state = key => ({queued:T('En attente','Queued'),running:T('En cours','Running'),completed:T('Terminé','Completed'),failed:T('Échec','Failed'),paused:T('En pause','Paused'),stopped:T('Arrêté','Stopped'),incomplete:T('Incomplet','Incomplete'),cancelled:T('Annulé','Cancelled')})[key]||key;
  let ratingSession=null, overview={}, report={results:[],campaigns:[],models:[],datasets:[]};
  async function guard(fn) {try {await fn();}catch(error){$('ia-status')?$('ia-status').textContent=error.message:alert(error.message);}}
  const message = value => {$('ia-status').textContent=value;};
  const send = async (url,payload={}) => postJson('/ia/api/'+url,payload);
  function chooseTab(name) {if(name!=='ratings')ratingSession?.suspend();document.querySelectorAll('[data-ia-panel]').forEach(e=>e.hidden=e.dataset.iaPanel!==name);document.querySelectorAll('[data-tab]').forEach(b=>{b.setAttribute('aria-pressed',String(b.dataset.tab===name));});}
  const option = (select,label,value) => select.add(new Option(label,value));
  function refreshSelect(select, entries, emptyLabel) {const value=select.value;select.replaceChildren();if(emptyLabel)option(select,emptyLabel,'');entries.forEach(([name,id])=>option(select,name,id));if([...select.options].some(o=>o.value===value))select.value=value;}
  const favoritePlaylists = () => [...$('ia-favorite-playlists').querySelectorAll('input:checked')].map(input=>input.value);
  function favoritePlaylistCaption() {
    const selected=favoritePlaylists();
    $('ia-playlist-caption').textContent=selected.includes('all')?T('Toutes les playlists','All playlists'):`${selected.length} ${selected.length===1?'playlist':'playlists'}`;
  }
  function renderFavoritePlaylists() {
    const box=$('ia-favorite-playlists'), entries=[[T('Toutes les playlists','All playlists'),'all']], seen=new Set();
    overview.snapshots.forEach(r=>{if(r.collection_id==='library'||seen.has(r.collection_id))return;seen.add(r.collection_id);entries.push([r.name,r.collection_id]);});
    const signature=JSON.stringify(entries);
    if(box.dataset.options===signature)return;
    const selected=favoritePlaylists();box.replaceChildren();box.dataset.options=signature;
    entries.forEach(([name,id])=>{
      const label=text(box,'label','','ia-playlist-option'), input=document.createElement('input');
      input.type='checkbox';input.value=id;input.checked=selected.includes(id);label.appendChild(input);text(label,'span',name);
    });
    if(!favoritePlaylists().length)box.querySelector('input').checked=true;
    favoritePlaylistCaption();
  }
  async function refresh() {
    [overview,report]=await Promise.all([fetchJson('/ia/api/summary'),fetchJson('/ia/api/results')]);
    if(!$('ia-coverage'))return;
    await refreshRatingMetadata();
    const c=overview.coverage,h=overview.history,l=labels();$('ia-coverage').replaceChildren();
    text($('ia-coverage'),'p',`${h.events.toLocaleString()} ${T('écoutes musicales','music plays')} · ${h.tracks.toLocaleString()} ${T('titres écoutés','played tracks')}`);
    text($('ia-coverage'),'p',h.first?`${h.first.slice(0,10)} → ${h.last.slice(0,10)}`:T('Aucun historique importé.','No history imported.'));
    [['artists',T('Identifiants artistes','Artist IDs')],['duration',T('Durée du morceau','Track duration')],['release_date',T('Date de sortie','Release date')],['genres',T('Genres connus','Known genres')]].forEach(([key,title])=>text($('ia-coverage'),'p',`${title} : ${c[key]} / ${c.total}`));
    text($('ia-coverage'),'p',`${T('Artistes distincts identifiés','Distinct identified artists')} : ${c.distinct_artists}`);
    text($('ia-coverage'),'p',`${T('Genres inconnus','Unknown genres')} : ${c.total-c.genres} / ${c.total}`);
    if(overview.matches.length){
      text($('ia-coverage'),'h3',T('Rapprochements MusicBrainz','MusicBrainz matches'));
      const matches=text($('ia-coverage'),'ul','','ia-match-summary');
      const kinds={artist:T('Artistes','Artists'),recording:T('Enregistrements','Recordings')};
      const statuses={ambiguous:T('À vérifier','Needs review'),confirmed:T('Confirmés','Confirmed'),not_found:T('Introuvables','Not found')};
      overview.matches.forEach(r=>{
        const row=text(matches,'li','');
        text(row,'span',`${kinds[r.kind]||r.kind} · ${statuses[r.status]||r.status}`);
        text(row,'strong',r.count.toLocaleString());
      });
    }
    $('ia-rating-counts').replaceChildren();Object.entries(l).filter(([key])=>key!=='unknown').forEach(([key,title])=>text($('ia-rating-counts'),'span',`${title} : ${overview.annotations[key]||0} / 30`,'ia-badge'));
    $('ia-years').replaceChildren();table($('ia-years'),[T('Année','Year'),T('Écoutes','Plays'),T('Titres distincts','Distinct tracks')],overview.years.map(r=>[r.year,r.events,r.tracks]));
    $('ia-imports').replaceChildren();if(!overview.imports.length)text($('ia-imports'),'p',T('Importez votre premier ZIP pour commencer.','Import your first ZIP to begin.'));
    overview.imports.forEach(r=>{const box=text($('ia-imports'),'div','');text(box,'p',`${r.filename} · ${state(r.state)} · ${r.progress}/${r.total} ${T('fichiers','files')}`);const counts=JSON.parse(r.summary);text(box,'p',`${T('Ajoutées / déjà présentes / invalides / sans URI / hors musique','Added / already present / invalid / missing URI / non-music')} : ${[counts.added||0,counts.already_present||0,counts.invalid||0,counts.missing_uri||0,counts.non_music||0].join(' / ')}`);if(r.error){text(box,'p',r.error);button(box,T('Réessayer','Retry'),async()=>{await send(`imports/${r.id}/retry`);await refresh();});}});
    $('ia-snapshots').replaceChildren();overview.snapshots.slice(0,20).forEach(r=>text($('ia-snapshots'),'p',`${r.name} · ${r.source} · ${r.captured_at.slice(0,10)}`));
    renderFavoritePlaylists();
    $('ia-jobs').replaceChildren();overview.jobs.forEach(r=>text($('ia-jobs'),'p',`${r.kind} · ${state(r.state)}${r.error?' · '+r.error:''}`));
    refreshSelect($('ia-dataset'),report.datasets.map(d=>[`${d.created_at.slice(0,19)} · ${d.id.slice(0,8)}`,d.id]),T('Créer au lancement','Create at launch'));
    refreshSelect($('ia-result-dataset'),report.datasets.map(d=>[`${d.id.slice(0,8)}${d.test_used?T(' · test consulté',' · test accessed'):''}`,d.id]));
    $('ia-campaigns').replaceChildren();report.campaigns.forEach(r=>{const box=text($('ia-campaigns'),'section','','surface ia-section');text(box,'h3',`${r.id.slice(0,8)} · ${state(r.state)}`);text(box,'p',`${T('Données','Dataset')} : ${r.dataset_id.slice(0,8)} · ${Object.entries(r.counts).map(([k,v])=>`${state(k)} : ${v}`).join(' · ')}`);const actions=text(box,'div','','ia-actions');const controls=r.state==='running'?['pause','stop']:r.state==='paused'?['resume','stop']:r.state==='incomplete'?['retry']:[];controls.forEach(action=>button(actions,({pause:T('Pause','Pause'),resume:T('Reprendre','Resume'),stop:T('Arrêter','Stop'),retry:T('Relancer les échecs','Retry failures')})[action],async()=>{await send(`campaigns/${r.id}/${action}`);await refresh();}));});
    renderResults();renderModels();renderDataset();
  }
  function renderDataset() {
    const box=$('ia-dataset-coverage');box.replaceChildren();
    const frozen=report.datasets.find(d=>d.id===$('ia-dataset').value);if(!frozen)return;
    text(box,'p',`${T('Couverture figée','Frozen coverage')} : ${frozen.coverage.genres}/${frozen.coverage.total} ${T('titres avec genre','tracks with genres')}`);
    if(frozen.test_used)text(box,'p',T('Ce test a déjà été consulté ; les nouveaux réglages ne constituent plus une évaluation indépendante.','This test has already been accessed; further tuning is no longer independently evaluated.'));
    const l=labels(),keys=Object.keys(l).slice(0,4);const rows=frozen.split_counts.map((s,i)=>[`${T('Découpage','Split')} ${i+1}`,...keys.map(k=>`${s.train[k]||0} / ${s.validation[k]||0} / ${frozen.test_counts[k]||0}`)]);
    table(box,[T('Entraînement / validation / test','Training / validation / test'),...keys.map(k=>l[k])],rows);
  }
  function renderRatingDetails(card,r) {
    const infoOpen=card.querySelector(".ia-track-info")?.open||false;
    card.replaceChildren();
    spotifyLink(text(card,'h3',''),displayTrackName(r.name),'track/'+encodeURIComponent(r.spotify_id));
    const artistLine=text(card,'p','','ia-card-artists');
    (r.artist_names||[]).forEach((name,index)=>{
      if(index)artistLine.appendChild(document.createTextNode(', '));
      const artist=(r.artists||[]).find(a=>a.name===name);
      spotifyLink(artistLine,name,artist?.id?'artist/'+encodeURIComponent(artist.id):'search/'+encodeURIComponent(`artist:"${name}"`));
    });
    if(!r.artist_names?.length)artistLine.textContent=T('Artiste inconnu','Unknown artist');
    const info=text(card,'details','','ia-track-info');info.open=infoOpen;text(info,'summary',T('Informations sur le titre','Track information'));
    text(info,'p',r.genres.length?r.genres.map(g=>`${g.name} (${g.source}, ${g.level})`).join(', '):T('Genre inconnu','Unknown genre'));
    const membership=text(info,'p',`${T('Présent dans','Found in')} ${r.playlist_count} ${r.playlist_count===1?'playlist':'playlists'}${r.liked?' · '+T('Liké','Liked'):''}${r.favorite_playlist?' · Que des bons sons':''}`,'ia-card-membership');
    membership.title=T('Compte les playlists locales sélectionnées contenant ce titre, une fois par playlist, même si le titre y apparaît plusieurs fois.','Counts selected local playlists containing this track, once per playlist, even if the track appears several times.');
    if(r.selection==='evaluation'||r.annotation?.reserved)text(card,'p',T('Réservé à l’évaluation indépendante','Reserved for independent evaluation'));
    if(r.annotation?.label==='unknown')text(card,'p',T('Ancien avis : abstention. Choisissez maintenant un des quatre goûts.','Previous rating: abstention. Now choose one of the four tastes.'));
  }
  function settings() {return {models:[$('ia-logistic').checked?'logistic':null,$('ia-forest').checked?'forest':null].filter(Boolean),periods:[$('ia-period-all').checked?'all':null,$('ia-period-five').checked?'five_years':null].filter(Boolean),musicbrainz:[$('ia-with-mb').checked?true:null,$('ia-without-mb').checked?false:null].filter(v=>v!==null),logistic:JSON.parse($('ia-logistic-params').value),forest:JSON.parse($('ia-forest-params').value),max_configurations:Number($('ia-max').value),cpu_threads:Number($('ia-cpu').value),dataset_id:$('ia-dataset').value||undefined};}
  async function preview() {const r=await send('grid',settings());$('ia-grid-preview').textContent=`${r.configurations} ${T('configurations','configurations')} × 3 = ${r.trainings} ${T('entraînements','trainings')} + ${r.reference_runs} ${T('essais de référence','reference runs')}${r.artist_holdout_runs!==null?` + ${r.artist_holdout_runs} ${T('essais avec artistes exclus','artist holdout runs')}`:' · '+T('Protocole artistes exclus à déterminer après gel des données','Artist holdout protocol determined after freezing data')}${!r.within_limit?' · '+T('PLAFOND DÉPASSÉ','LIMIT EXCEEDED'):''}`;return r;}
  const pct = value => value===null||value===undefined?'—':value.toFixed(3);
  const dimension = (row,key) => key.startsWith('params.')?JSON.stringify(row.config.params[key.slice(7)]??null):String(row.config[key]);
  function filtered() {return report.results.filter(r=>r.dataset_id===$('ia-result-dataset').value&&(!$('ia-result-model').value||r.config.model===$('ia-result-model').value)&&(!$('ia-result-period').value||r.config.period===$('ia-result-period').value)&&(!$('ia-result-mb').value||String(r.config.musicbrainz)===$('ia-result-mb').value)&&JSON.stringify(r.config.params).toLowerCase().includes($('ia-result-params').value.toLowerCase()));}
  const selectedComparisons=new Set();
  function renderResults() {
    const rows=filtered();$('ia-results-table').replaceChildren();
    table($('ia-results-table'),[T('Comparer','Compare'),T('Modèle','Model'),T('Période','Period'),'MusicBrainz',T('Paramètres','Parameters'),'F1 macro',T('Dispersion','Dispersion'),T('Durée / taille','Duration / size'),T('Finaliste','Finalist')],rows.map(r=>{const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=selectedComparisons.has(r.config_key);checkbox.setAttribute('aria-label',T('Comparer cette configuration','Compare this configuration'));checkbox.addEventListener('change',()=>{checkbox.checked?selectedComparisons.add(r.config_key):selectedComparisons.delete(r.config_key);renderComparison();});const actions=document.createElement('div');if(r.complete)button(actions,T('Évaluer sur le test final','Evaluate on final test'),async()=>{await send('finalize',{campaign_id:r.campaign_id,config_key:r.config_key});message(T('Finaliste en file : entraînement et évaluation finale.','Finalist queued: training and final evaluation.'));await refresh();});else text(actions,'small',T('Trois essais complets requis','Three complete runs required'));return [checkbox,r.config.model,r.config.period,r.config.musicbrainz?T('Avec','With'):T('Sans','Without'),JSON.stringify(r.config.params),pct(r.f1_macro),pct(r.dispersion),`${r.duration.toFixed(1)} s / ${(r.bytes/1024).toFixed(1)} Ko`,actions];}));
    renderPivot(rows);renderComparison();
  }
  function renderPivot(rows) {const x=$('ia-pivot-row').value,y=$('ia-pivot-col').value;if(!x||!y)return;const complete=rows.filter(r=>r.complete);const xs=[...new Set(complete.map(r=>dimension(r,x)))],ys=[...new Set(complete.map(r=>dimension(r,y)))];$('ia-pivot').replaceChildren();table($('ia-pivot'),[`${x} / ${y}`,...ys],xs.map(a=>[a,...ys.map(b=>{const group=complete.filter(r=>dimension(r,x)===a&&dimension(r,y)===b);return group.length?`${pct(group.reduce((s,r)=>s+r.f1_macro,0)/group.length)} (n=${group.length})`:'—';})]));}
  function renderComparison() {$('ia-comparison').replaceChildren();filtered().filter(r=>selectedComparisons.has(r.config_key)).forEach(r=>{const box=text($('ia-comparison'),'details','');text(box,'summary',`${r.config_key} · ${r.config.model} · F1 ${pct(r.f1_macro)}`);r.trials.forEach(t=>{text(box,'h4',`${T('Découpage','Split')} ${t.split+1} · ${state(t.state)}`);if(t.error)text(box,'p',t.error);if(!t.metrics)return;text(box,'p',`${T('Échantillon','Sample')} : ${t.metrics.n} · ${T('Erreurs éloignées','Distant errors')} : ${t.metrics.distant_errors}`);text(box,'p',`${T('Artistes connus','Known artists')} : ${pct(t.metrics.known_artists?.f1_macro)} (n=${t.metrics.known_artists?.n||0}) · ${T('Artistes exclus de l’entraînement','Artists held out from training')} : ${t.metrics.unknown_artists?`${pct(t.metrics.unknown_artists.f1_macro)} (n=${t.metrics.unknown_artists.n})`:T('non mesurable','not measurable')}`);text(box,'p',T('Matrice : avis réel en ligne, prédiction en colonne.','Matrix: actual rating in rows, prediction in columns.'));const l=labels();table(box,['',...Object.values(l).slice(0,4)],t.metrics.confusion.map((v,i)=>[Object.values(l)[i],...v]));table(box,[T('Goût','Taste'),'Precision','Recall','F1','n'],Object.keys(l).slice(0,4).map(label=>{const v=t.metrics.report[label];return [l[label],pct(v.precision),pct(v.recall),pct(v['f1-score']),v.support];}));});});}
  function renderModels() {$('ia-models').replaceChildren();if(!report.models.length)text($('ia-models'),'p',T('Aucun modèle finalisé. Comparez les validations puis évaluez un finaliste.','No finalized model. Compare validation results, then evaluate a finalist.'));report.models.forEach(r=>{const box=text($('ia-models'),'div','','ia-model');text(box,'strong',`${r.id.slice(0,8)} · ${r.config.model}${r.exploratory?' · '+T('Exploratoire','Exploratory'):''}${report.active_model===r.id?' · '+T('Actif','Active'):''}`);text(box,'p',r.exploratory?T('Sans évaluation indépendante','Without independent evaluation'):`F1 ${pct(r.metrics.f1_macro)} · n=${r.metrics.n} · ${T('Test final','Final test')}`);if(!r.exploratory)button(box,T('Activer cette version','Activate this version'),async()=>{await send('activate',{model_id:r.id});await refresh();});});}
  async function refreshRatingMetadata() {
    const batch=ratingSession?.currentBatch()||[];if(!batch.length)return;
    const params=new URLSearchParams();batch.forEach(track=>params.append('uri',track.uri));
    const result=await fetchJson('/ia/api/metadata?'+params);
    ratingSession.updateMetadata(result.tracks);
  }
  let enrichmentWarning="";
  async function enrichRatingBatch(tracks) {
    try {
      const result=await send('enrich',{uris:tracks.map(track=>track.uri),spotify:true,automatic:true});
      const warning=result.spotify_status==='expired'?T('MusicBrainz en arrière-plan. Reconnectez Spotify pour compléter les métadonnées.','MusicBrainz in the background. Reconnect Spotify to complete metadata.'):
        result.spotify_status==='unavailable'?T('Spotify indisponible. MusicBrainz continue ; la notation reste disponible.','Spotify unavailable. MusicBrainz continues; rating remains available.'):'';
      if(warning)message(warning);else if($('ia-status').textContent===enrichmentWarning)message('');
      enrichmentWarning=warning;
      await refreshRatingMetadata();
    } catch(error) {message(T('Enrichissement indisponible : ','Enrichment unavailable: ')+error.message);}
  }
  async function initLaboratory() {
    ratingSession=new StatifyRatingSession({root:$('ia-candidates'),form:$('ia-candidates-form'),filtersPanel:$('ia-rating-filters'),
      fetchBatch:filters=>send('candidates',filters),saveAnnotation:payload=>send('annotations',payload),onSaved:refresh,renderDetails:renderRatingDetails,onBatch:enrichRatingBatch});
    document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>{location.hash=b.dataset.tab;chooseTab(b.dataset.tab);}));window.addEventListener('hashchange',()=>chooseTab(['data','ratings','campaigns','results'].includes(location.hash.slice(1))?location.hash.slice(1):'data'));chooseTab(['data','ratings','campaigns','results'].includes(location.hash.slice(1))?location.hash.slice(1):'data');
    $('ia-import-form').addEventListener('submit',e=>{e.preventDefault();guard(async()=>{const form=new FormData();form.append('archive',$('ia-archive').files[0]);await fetchJson('/ia/api/imports',{method:'POST',headers:{'X-CSRF-Token':csrfToken},body:form});message(T('Import en attente du worker IA.','Import waiting for the AI worker.'));await refresh();});});
    $('ia-sync').addEventListener('click',()=>guard(async()=>{await send('sync-local');await refresh();message(T('Collections locales enregistrées.','Local collections saved.'));}));
    const playlistPicker=document.querySelector('.ia-playlist-picker');
    document.addEventListener('pointerdown',e=>{if(!playlistPicker.contains(e.target))playlistPicker.open=false;});
    playlistPicker.addEventListener('keydown',e=>{if(e.key==='Escape'){playlistPicker.open=false;$('ia-playlist-caption').focus();}});
    $('ia-favorite-playlists').addEventListener('change',e=>{
      const input=e.target;if(input.type!=='checkbox')return;
      const all=$('ia-favorite-playlists').querySelector('input[value="all"]');
      if(input===all&&all.checked)$('ia-favorite-playlists').querySelectorAll('input').forEach(other=>{if(other!==all)other.checked=false;});
      else if(input!==all&&input.checked)all.checked=false;
      if(!favoritePlaylists().length)all.checked=true;
      favoritePlaylistCaption();
    });
    $('ia-candidates-form').addEventListener('submit',e=>{e.preventDefault();guard(async()=>{const filters={mode:$('ia-mode').value,artist:$('ia-artist').value,year:$('ia-year').value,genre:$('ia-genre').value,threshold:$('ia-threshold').value};const selected=favoritePlaylists();if(!selected.includes('all'))filters.playlists=selected;await ratingSession.start(filters);});});
    const enrich=spotify=>guard(async()=>{const batch=ratingSession.currentBatch();if(!batch.length)throw Error(T('Proposez un lot de titres d’abord.','Suggest a batch first.'));await send('enrich',{uris:batch.map(r=>r.uri),spotify});message(T('Enrichissement demandé. Rechargez le lot après traitement.','Enrichment requested. Reload the batch after processing.'));});$('ia-enrich').addEventListener('click',()=>enrich(false));$('ia-enrich-spotify').addEventListener('click',()=>enrich(true));
    $('ia-exploratory').addEventListener('click',()=>guard(async()=>{await send('exploratory');message(T('Modèle exploratoire en file.','Exploratory model queued.'));}));
    $('ia-freeze').addEventListener('click',()=>guard(async()=>{const r=await send('datasets');await refresh();$('ia-dataset').value=r.id;renderDataset();message(T('Version figée.','Dataset frozen.'));}));
    $('ia-dataset').addEventListener('change',()=>guard(async()=>{renderDataset();await preview();}));
    $('ia-preview').addEventListener('click',()=>guard(preview));$('ia-campaign-form').addEventListener('submit',e=>{e.preventDefault();guard(async()=>{if(!$('ia-dataset').value){const frozen=await send('datasets');await refresh();$('ia-dataset').value=frozen.id;renderDataset();}await preview();await send('campaigns',settings());message(T('Campagne en file du worker IA.','Campaign queued for the AI worker.'));await refresh();});});
    const dimensions=['model','period','musicbrainz','params.C','params.class_weight','params.n_estimators','params.max_depth','params.min_samples_leaf'];dimensions.forEach(d=>{option($('ia-pivot-row'),d,d);option($('ia-pivot-col'),d,d);});$('ia-pivot-col').value='musicbrainz';
    ['ia-result-dataset','ia-result-model','ia-result-period','ia-result-mb','ia-result-params','ia-pivot-row','ia-pivot-col'].forEach(id=>$(id).addEventListener('input',renderResults));await refresh();await preview();setInterval(()=>{if(!document.hidden)guard(refresh);},5000);
  }
  async function initPredictions() {
    const select=$('ia-collection'),container=$('ia-predictions');
    const reload=async()=>{const r=await fetchJson('/ia/api/collections');refreshSelect(select,r.collections.map(c=>[`${c.name} · ${c.captured_at.slice(0,10)}`,c.id]));};
    const render=async()=>{if(!select.value)throw Error(T('Aucune collection locale : enregistrez les collections chargées depuis IA → Données.','No local collection: save loaded collections in AI → Data.'));const r=await send('predict',{snapshot_id:select.value});container.replaceChildren();const names=labels();delete names.unknown;names.unassigned=T('Non attribué','Unassigned');const columns={};Object.entries(names).forEach(([key,title])=>{const box=text(container,'section','','surface');text(box,'h3',title);columns[key]=box;});if(!r.model_id)text($('ia-prediction-status'),'p',T('Aucun modèle actif. Seuls les avis connus sont répartis.','No active model. Only known ratings are assigned.'));else $('ia-prediction-status').textContent=`${T('Modèle','Model')} : ${r.model_id.slice(0,8)} · ${T('Scores non calibrés','Uncalibrated scores')}`;r.tracks.forEach(track=>{const known=track.annotation&&track.annotation!=='unknown';const key=known?track.annotation:(track.prediction||'unassigned');const card=text(columns[key],'article','','ia-track');text(card,'strong',track.name);text(card,'p',(track.artist_names||[]).join(', '));text(card,'small',known?T('Avis connu','Known rating'):`${T('Prédiction','Prediction')} · ${pct(track.score)}`);text(card,'small',`${T('Couverture artiste / genre / durée','Artist / genre / duration coverage')} : ${Object.values(track.coverage).map(v=>v?'✓':'—').join(' / ')}`);if(track.factors.length){const details=text(card,'details','');text(details,'summary',T('Indices utilisés','Factors used'));track.factors.forEach(f=>text(details,'p',`${f.feature} : ${f.value.toFixed(3)}${f.kind==='global_importance'?' · '+T('importance globale','global importance'):''}`));}const actions=text(card,'div','','ia-rating-actions');Object.entries(labels()).forEach(([label,title])=>button(actions,title,async()=>{await send('annotations',{uri:track.uri,label});await render();}));});};
    const enrichCollection=spotify=>guard(async()=>{
      if(!select.value)throw Error(T('Sélectionnez une collection locale.','Select a local collection.'));
      const r=await fetchJson('/ia/api/collections/'+encodeURIComponent(select.value));
      if(!r.uris.length)throw Error(T('Aucun titre musical dans cette collection.','No musical tracks in this collection.'));
      for(let i=0;i<r.uris.length;i+=50){await send('enrich',{uris:r.uris.slice(i,i+50),spotify});$('ia-prediction-status').textContent=`${T('Enrichissement demandé','Enrichment requested')} : ${Math.min(i+50,r.uris.length)}/${r.uris.length}`;}
    });
    $('ia-collection-enrich').addEventListener('click',()=>enrichCollection(false));
    $('ia-collection-spotify').addEventListener('click',()=>enrichCollection(true));
    $('ia-predict').addEventListener('click',()=>guard(async()=>{$('ia-prediction-status').replaceChildren();await render();}));await reload();
  }
  document.addEventListener('DOMContentLoaded',()=>{if($('ia-status'))guard(initLaboratory);if($('ia-collection'))guard(initPredictions);});
})();
