/* Sequential ratings: the queue, player and saved opinions have separate lifecycles. */
(() => {
  const tastes = () => ({dislike:T('Je n’aime pas','Dislike'),neutral:T('Neutre','Neutral'),like:T('J’aime','Like'),love:T('J’adore','Love')});
  const arrows = {ArrowLeft:'dislike',ArrowUp:'like',ArrowDown:'neutral',ArrowRight:'love'};
  const glyphs = {dislike:'←',like:'↑',neutral:'↓',love:'→'};
  const element = (parent,tag,value='',className='') => {
    const node=document.createElement(tag);node.textContent=value;node.className=className;parent.appendChild(node);return node;
  };
  let apiPromise;
  function spotifyAPI() {
    if(!apiPromise)apiPromise=new Promise((resolve,reject)=>{
      window.onSpotifyIframeApiReady=resolve;
      const script=document.createElement('script');script.src='https://open.spotify.com/embed/iframe-api/v1';script.async=true;
      script.onerror=()=>reject(Error('Spotify API unavailable'));document.head.appendChild(script);
    });
    return apiPromise;
  }

  class RatingPlayer {
    constructor(host,hint) {
      this.host=host;this.hint=hint;this.controller=null;this.ready=false;this.track=null;this.wantPlay=false;this.initializing=false;this.loadedUri=null;
    }
    fallback() {
      const frame=document.createElement('iframe');frame.width='100%';frame.height='80';
      frame.src='https://open.spotify.com/embed/track/'+encodeURIComponent(this.track.spotify_id);
      frame.title=T('Lecteur Spotify : ','Spotify player: ')+displayTrackName(this.track.name);
      frame.allow='autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture';frame.allowFullscreen=true;
      this.host.replaceChildren(frame);
    }
    setTitle() {
      const frame=this.host.querySelector('iframe');
      if(frame)frame.title=T('Lecteur Spotify : ','Spotify player: ')+displayTrackName(this.track.name);
    }
    load(track,autoplay=false) {
      this.pause();this.track=track;this.wantPlay=autoplay;
      this.hint.textContent=autoplay?T('Lecture automatique demandée. Si elle ne démarre pas, utilisez Espace ou ▶.','Automatic playback requested. If it does not start, use Space or ▶.'):
        T('Espace ou clic sur la carte : lecture / pause.','Space or click the card: play / pause.');
      if(this.controller) {
        try {if(this.ready)this.syncTrack();this.setTitle();if(autoplay)this.play();}
        catch(error){this.unavailable(true);}
        return;
      }
      if(this.initializing){this.fallback();return;}
      this.initializing=true;
      spotifyAPI().then(api=>{
        const placeholder=document.createElement('div');this.host.replaceChildren(placeholder);
        this.loadedUri=this.track.uri;
        api.createController(placeholder,{uri:this.loadedUri,width:'100%',height:80},controller=>{
          this.controller=controller;this.setTitle();
          controller.addListener('ready',()=>{
            if(this.ready)return;
            this.ready=true;
            try {
              this.syncTrack();this.setTitle();
              if(this.wantPlay)this.play();else controller.pause();
            } catch(error) {this.unavailable(true);}
          });
          controller.addListener('playback_update',event=>{
            if(event.data?.playingURI&&event.data.playingURI!==this.track.uri)return;
            if(event.data?.isPaused)this.hint.textContent=T('En pause · Espace pour reprendre la lecture.','Paused · Space to resume playback.');
          });
          controller.addListener('playback_started',event=>{
            if(event.data?.playingURI&&event.data.playingURI!==this.track.uri)return;
            this.hint.textContent=T('Lecture en cours · Espace pour mettre en pause.','Playing · Space to pause.');
          });
        });
      }).catch(()=>this.unavailable());
    }
    syncTrack() {
      if(this.loadedUri===this.track.uri)return;
      // Record before loading: Spotify may emit ready again for the new entity.
      this.loadedUri=this.track.uri;
      this.controller.loadEntity(this.loadedUri);
    }
    manualPlayback() {
      this.hint.textContent=T('La lecture n’a pas démarré. Essayez Espace ou ▶ dans le lecteur ; vous pouvez continuer à noter.','Playback did not start. Try Space or ▶ in the player; you can keep rating.');
    }
    unavailable(replace=false) {
      this.ready=false;
      if(replace&&this.controller) {
        try {this.controller.destroy();}catch(error) {/* Removing the iframe also stops it. */}
        this.controller=null;this.host.replaceChildren();
      }
      if(!this.host.querySelector('iframe'))this.fallback();
      this.hint.textContent=T('Les commandes Spotify sont indisponibles. Utilisez ▶ dans le lecteur ; vous pouvez continuer à noter.','Spotify controls are unavailable. Use ▶ in the player; you can keep rating.');
    }
    play() {
      this.wantPlay=true;
      if(!this.ready)return;
      try {
        const playing=this.controller.play();
        if(playing?.catch)playing.catch(()=>this.manualPlayback());
      } catch(error) {this.manualPlayback();}
    }
    pause() {
      this.wantPlay=false;
      try {
        if(this.controller)this.controller.pause();
        else {const frame=this.host.querySelector('iframe');if(frame)frame.src=frame.src;}
      } catch(error) {this.unavailable();}
    }
    toggle() {
      if(!this.ready) {
        if(!this.controller){this.hint.textContent=T('Utilisez ▶ dans le lecteur Spotify pendant son chargement.','Use ▶ in the Spotify player while it loads.');return;}
        this.wantPlay=!this.wantPlay;return;
      }
      try {this.controller.togglePlay();}catch(error){this.manualPlayback();}
    }
  }

  class RatingSession {
    constructor({root,form,filtersPanel,fetchBatch,saveAnnotation,onSaved,renderDetails,onBatch=()=>{}}) {
      Object.assign(this,{root,form,filtersPanel,fetchBatch,saveAnnotation,onSaved,renderDetails,onBatch});
      this.queue=[];this.seen=new Set();this.processed=new Set();this.index=0;this.batchNumber=0;this.busy=false;this.ended=false;this.started=false;
      this.build();this.bindKeyboard();
    }
    active() {return this.root.isConnected&&!this.root.hidden&&!this.root.closest('[data-ia-panel]')?.hidden;}
    current() {return this.queue[this.index];}
    build() {
      this.root.replaceChildren();
      const nav=element(this.root,'div','','ia-session-navigation');
      this.previous=element(nav,'button',T('Précédent','Previous'),'button button-secondary');this.previous.type='button';
      this.progress=element(nav,'p','','ia-session-progress');this.progress.setAttribute('aria-live','polite');
      this.resume=element(nav,'button',T('Reprendre','Resume'),'button button-secondary');this.resume.type='button';
      this.stage=element(this.root,'article','','surface ia-rating-card ia-single-rating');this.stage.tabIndex=0;
      this.stage.setAttribute('aria-label',T('Titre à évaluer','Track to rate'));this.stage.setAttribute('aria-keyshortcuts','ArrowLeft ArrowUp ArrowDown ArrowRight Space Enter');
      this.details=element(this.stage,'div','','ia-session-details');
      this.playerHost=element(this.stage,'div','','ia-embedded-player');
      this.playerHint=element(this.stage,'p','','ia-player-hint');
      this.player=new RatingPlayer(this.playerHost,this.playerHint);
      const actions=element(this.stage,'div','','ia-rating-actions ia-rating-cross');this.choices={};
      Object.entries(tastes()).forEach(([label,title])=>{
        const button=element(actions,'button','','button button-secondary ia-choice-'+label);button.type='button';button.dataset.rating=label;
        element(button,'kbd',glyphs[label]);element(button,'span',title);button.setAttribute('aria-pressed','false');
        button.addEventListener('click',()=>this.rate(label));this.choices[label]=button;
      });
      this.notice=element(this.root,'p','','ia-session-notice');this.notice.setAttribute('role','status');this.notice.setAttribute('aria-live','polite');
      this.retry=element(this.root,'button',T('Charger la suite','Load next batch'),'button button-secondary');this.retry.type='button';this.retry.hidden=true;
      this.retry.addEventListener('click',()=>this.resumePending());
      this.previous.addEventListener('click',()=>{if(!this.busy&&this.index>0){this.index--;this.show(false);}});
      this.resume.addEventListener('click',()=>this.resumePending());
      this.stage.addEventListener('click',event=>{
        if(event.target.closest('a,button,input,select,textarea,details,.ia-embedded-player')||window.getSelection()?.toString())return;
        this.stage.focus({preventScroll:true});if(!this.busy)this.player.toggle();
      });
      this.stage.hidden=true;nav.hidden=true;
      this.notice.textContent=T('Choisissez vos filtres et proposez un premier lot pour commencer.','Choose your filters and suggest a first batch to begin.');
    }
    bindKeyboard() {
      this.heldKeys=new Set();
      document.addEventListener('keyup',event=>this.heldKeys.delete(event.key));
      window.addEventListener('blur',()=>this.heldKeys.clear());
      document.addEventListener('keydown',event=>{
        if(!this.active()||!this.current()||document.activeElement!==this.stage||event.defaultPrevented||event.isComposing||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey||event.getModifierState('Fn'))return;
        if(event.target.closest('input,textarea,select,button,a,summary,form,[contenteditable],[role="dialog"],[role="menu"]'))return;
        if(document.querySelector('[aria-modal="true"]:not([hidden])')||document.querySelector('.ia-playlist-picker[open]'))return;
        const label=arrows[event.key],play=event.key===' '||event.key==='Enter';if(!label&&!play)return;
        event.preventDefault();
        if(this.busy||event.repeat||this.heldKeys.has(event.key))return;
        this.heldKeys.add(event.key);
        if(label)this.rate(label);else this.player.toggle();
      });
    }
    setBusy(value) {
      this.busy=value;this.stage.setAttribute('aria-busy',String(value));
      Object.values(this.choices).forEach(button=>button.disabled=value);
      this.previous.disabled=value||this.index===0;
      const pending=this.queue.findIndex(entry=>!this.processed.has(entry.track.uri));
      this.resume.disabled=value||(pending<0&&this.ended)||pending===this.index;
      this.retry.disabled=value;
      this.form.querySelector('button').disabled=value;
    }
    async start(filters) {
      if(this.busy)return;
      this.player.pause();this.queue=[];this.seen.clear();this.processed.clear();this.index=0;this.batchNumber=0;this.ended=false;this.started=true;
      this.filters=JSON.parse(JSON.stringify(filters));this.setBusy(true);this.stage.hidden=true;
      this.notice.textContent=T('Chargement du lot…','Loading batch…');this.retry.hidden=true;
      try {await this.appendBatch();this.filtersPanel.open=false;this.show(false);this.root.scrollIntoView({block:'start'});}
      catch(error){this.notice.textContent=error.message;this.retry.hidden=false;}
      finally {this.setBusy(false);}
    }
    async appendBatch() {
      const result=await this.fetchBatch({...this.filters,exclude_uris:[...this.seen]});
      const tracks=result.tracks.filter(track=>!this.seen.has(track.uri));
      this.batch=tracks;this.batchNumber++;
      tracks.forEach((track,index)=>{
        this.seen.add(track.uri);this.queue.push({track,batchNumber:this.batchNumber,position:index+1,size:tracks.length});
      });
      if(!tracks.length)this.ended=true;
      else Promise.resolve().then(()=>this.onBatch(tracks)).catch(error=>console.warn('Batch enrichment unavailable',error));
    }
    show(autoplay) {
      const entry=this.current();this.retry.hidden=true;this.notice.textContent='';
      this.previous.parentElement.hidden=!this.started;
      if(!entry) {
        this.player.pause();this.stage.hidden=true;
        this.progress.textContent=`${this.processed.size} ${T('titres traités','tracks rated')}`;
        this.notice.textContent=this.ended?T('Session terminée : aucun autre titre pour ces filtres.','Session complete: no more tracks for these filters.'):T('Chargement de la suite…','Loading next batch…');
        this.setBusy(this.busy);return;
      }
      this.stage.hidden=false;this.renderDetails(this.details,entry.track);
      this.progress.textContent=`${T('Lot','Batch')} ${entry.batchNumber} · ${entry.position} / ${entry.size} · ${this.processed.size} ${T('titres traités','tracks rated')}`;
      this.updateChoices(entry.track.annotation?.label);
      this.player.load(entry.track,autoplay&&this.active());
      this.setBusy(this.busy);
      if(this.active())this.stage.focus({preventScroll:true});
    }
    updateChoices(label) {
      Object.entries(this.choices).forEach(([key,button])=>button.setAttribute('aria-pressed',String(key===label)));
    }
    async rate(label) {
      const entry=this.current();if(this.busy||!entry||!Object.hasOwn(this.choices,label))return;
      this.setBusy(true);this.player.pause();this.updateChoices(label);this.notice.textContent=T('Enregistrement…','Saving…');
      try {
        await this.saveAnnotation({uri:entry.track.uri,label});
      } catch(error) {
        this.updateChoices(entry.track.annotation?.label);this.notice.textContent=error.message;this.setBusy(false);return;
      }
      entry.track.annotation={...(entry.track.annotation||{}),label};this.processed.add(entry.track.uri);this.index++;
      Promise.resolve().then(()=>this.onSaved()).catch(error=>console.warn('Rating counters refresh unavailable',error));
      try {if(this.index===this.queue.length&&!this.ended)await this.appendBatch();this.show(true);}
      catch(error){this.show(false);this.notice.textContent=error.message;this.retry.hidden=false;}
      finally {this.setBusy(false);}
    }
    async resumePending() {
      if(this.busy||!this.started)return;
      const pending=this.queue.findIndex(entry=>!this.processed.has(entry.track.uri));
      if(pending>=0){this.index=pending;this.show(false);return;}
      this.index=this.queue.length;
      if(this.ended){this.show(false);return;}
      this.setBusy(true);
      try {await this.appendBatch();this.show(false);}
      catch(error){this.notice.textContent=error.message;this.retry.hidden=false;}
      finally {this.setBusy(false);}
    }
    updateMetadata(tracks) {
      const before=JSON.stringify(this.current()?.track);
      const latest=new Map(tracks.map(track=>[track.uri,track]));
      this.queue.forEach(entry=>{
        const enriched=latest.get(entry.track.uri);
        if(enriched)entry.track={...entry.track,...enriched,annotation:entry.track.annotation};
      });
      // Metadata updates must never reload the player or move keyboard focus.
      const entry=this.current();if(entry&&!this.stage.hidden&&before!==JSON.stringify(entry.track))this.renderDetails(this.details,entry.track);
    }
    suspend() {this.player.pause();this.heldKeys.clear();}
    currentBatch() {return this.queue.filter(entry=>entry.batchNumber===this.current()?.batchNumber).map(entry=>entry.track);}
  }
  window.StatifyRatingSession=RatingSession;
})();
