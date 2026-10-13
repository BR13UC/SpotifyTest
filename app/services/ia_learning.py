"""Frozen evaluation protocols and CPU-only, leakage-safe taste models."""
import hashlib
import itertools
import json
import math
import pickle
import random
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app
from werkzeug.exceptions import BadRequest, NotFound
from app import storage
from app.services import ia_data as data, ia_store as store

FEATURE_VERSION = 1
SEEDS = [17, 41, 73]
DEFAULT_GRID = {
    'models': ['logistic', 'forest'], 'periods': ['all', 'five_years'], 'musicbrainz': [False, True],
    'logistic': {'C': [0.1, 1, 10], 'class_weight': [None, 'balanced']},
    'forest': {'n_estimators': [100, 300], 'max_depth': [10, None], 'min_samples_leaf': [1, 5], 'class_weight': [None, 'balanced']},
    'max_configurations': 100, 'cpu_threads': 1,
}


def config_key(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:24]


def grid(settings):
    merged = {**DEFAULT_GRID, **settings}
    if not isinstance(merged['models'], list) or not merged['models'] or not set(merged['models']) <= {'logistic', 'forest'}:
        raise BadRequest('Famille de modèles inconnue.')
    if not merged['periods'] or not set(merged['periods']) <= {'all', 'five_years'}:
        raise BadRequest('Période inconnue.')
    if not merged['musicbrainz'] or any(type(v) is not bool for v in merged['musicbrainz']):
        raise BadRequest('Variantes MusicBrainz invalides.')
    if type(merged['cpu_threads']) is not int or not 1 <= merged['cpu_threads'] <= 8:
        raise BadRequest('Choisissez entre 1 et 8 threads CPU.')
    if type(merged['max_configurations']) is not int or not 1 <= merged['max_configurations'] <= 10000:
        raise BadRequest('Plafond invalide (1 à 10 000).')
    configs = {}
    for model in set(merged['models']) | {'artist'}:
        params = merged.get(model, {}) if model != 'artist' else {}
        allowed = set(DEFAULT_GRID.get(model, {}))
        if not isinstance(params, dict) or set(params) != allowed:
            raise BadRequest('Paramètres du modèle incomplets ou inconnus.')
        for key, values in params.items():
            if not isinstance(values, list) or not values or len(values) > 30:
                raise BadRequest('Chaque paramètre attend une liste de 1 à 30 valeurs.')
            for value in values:
                if key == 'class_weight':
                    valid = value in (None, 'balanced')
                elif key == 'max_depth':
                    valid = value is None or type(value) is int and 1 <= value <= 100
                elif key == 'C':
                    valid = type(value) in (int, float) and math.isfinite(value) and 0 < value <= 10000
                else:
                    valid = type(value) is int and 1 <= value <= (1000 if key == 'n_estimators' else 100)
                if not valid:
                    raise BadRequest(f'Valeur invalide pour {key}.')
        combinations = list(itertools.product(*params.values()))
        for period, mb, values in itertools.product(merged['periods'], merged['musicbrainz'], combinations):
            config = {'model': model, 'period': period, 'musicbrainz': mb, 'params': dict(zip(params, values)), 'feature_version': FEATURE_VERSION}
            configs[config_key(config)] = config
            if len(configs) > 10000:
                raise BadRequest('La grille dépasse 10 000 configurations.')
    configs = sorted(configs.values(), key=lambda c: json.dumps(c, sort_keys=True))
    learned = sum(c['model'] != 'artist' for c in configs)
    references = len(configs) - learned
    return {'settings': merged, 'configs': configs, 'configurations': learned, 'references': references,
            'trainings': learned * 3, 'reference_runs': references * 3,
            'within_limit': learned <= merged['max_configurations']}


def dataset(user):
    records = {r['uri']: r for r in data.catalog(user)}
    annotations = store.rows('SELECT * FROM ia_annotations WHERE user_id=? AND label != ?', (user, 'unknown'))
    groups = defaultdict(list)
    for annotation in annotations:
        if annotation['uri'] in records:
            row = {**records[annotation['uri']], 'label': annotation['label'], 'reserved': bool(annotation['reserved']), 'selection': json.loads(annotation['selection'])}
            groups[row['equivalence']].append(row)
    partitions = {label: {'development': [], 'reserved': []} for label in store.LABELS}
    for group, rows in groups.items():
        if len({r['label'] for r in rows}) != 1:
            raise BadRequest('Des versions équivalentes ont des avis différents. Corrigez-les avant de figer les données.')
        reserved = any(r['reserved'] for r in rows)
        partitions[rows[0]['label']]['reserved' if reserved else 'development'].append(group)
    dev, test = [], []
    for label, parts in partitions.items():
        development = sorted(parts['development'])
        reserved = sorted(parts['reserved'])
        needed = max(1, math.ceil(len(development) / 4))
        if len(development) < 4 or len(reserved) < needed:
            raise BadRequest(f'{label} : il faut au moins 4 groupes de développement et {needed} groupe(s) du lot indépendant Évaluation (disponibles : {len(development)} / {len(reserved)}).')
        random.Random(13).shuffle(reserved)
        dev.extend(development)
        test.extend(reserved[:needed])
    splits = []
    for seed in SEEDS:
        train, validation = [], []
        for parts in partitions.values():
            keys = sorted(parts['development'])
            random.Random(seed).shuffle(keys)
            count = max(1, math.ceil(len(keys) / 4))
            validation.extend(keys[:count])
            train.extend(keys[count:])
        splits.append({'train': train, 'validation': validation, 'seed': seed})
    chosen = set(dev + test)
    frozen_rows = [r for key in sorted(chosen) for r in groups[key]]
    event_counts = {}
    last = store.rows('SELECT max(ts) AS last FROM ia_events WHERE user_id=?', (user,))[0]['last']
    anchor = datetime.fromisoformat(last) if last else datetime.now(timezone.utc)
    try:
        cutoff = anchor.replace(year=anchor.year - 5).isoformat()
    except ValueError:  # February 29 when the target year is not a leap year.
        cutoff = anchor.replace(year=anchor.year - 5, day=28).isoformat()
    for r in store.rows('SELECT uri,count(*) AS all_count,sum(CASE WHEN ts>=? THEN 1 ELSE 0 END) AS recent FROM ia_events WHERE user_id=? GROUP BY uri', (cutoff, user)):
        event_counts[r['uri']] = {'all': r['all_count'], 'five_years': r['recent']}
    payload = {'rows': frozen_rows, 'development': dev, 'test': test, 'splits': splits,
               'history_counts': {r['uri']: event_counts.get(r['uri'], {'all': 0, 'five_years': 0}) for r in frozen_rows},
               'history_anchor': anchor.isoformat(), 'coverage': data.summary(user)['coverage'],
               'feature_version': FEATURE_VERSION, 'seeds': SEEDS}
    payload['artist_holdouts'] = artist_holdouts(payload)
    def counts(groups):
        return dict(Counter(r['label'] for r in frozen_rows if r['equivalence'] in groups))
    payload['split_counts'] = [{'train': counts(s['train']), 'validation': counts(s['validation'])} for s in splits]
    payload['test_counts'] = counts(test)
    key = data.identifier()
    consulted = set()
    for previous in store.rows('SELECT payload FROM ia_datasets WHERE user_id=? AND test_used=1', (user,)):
        consulted.update(json.loads(previous['payload'])['test'])
    reused_test = bool(consulted & set(test))
    payload['test_previously_accessed'] = reused_test
    store.execute('INSERT INTO ia_datasets(id,user_id,payload,created_at,test_used) VALUES(?,?,?,?,?)', (key, user, json.dumps(payload), storage.now(), int(reused_test)))
    return key


def artist_ids(row):
    return {a['id'] for a in row.get('artists') or [] if a.get('id')}


def artist_holdouts(frozen):
    """Connected artist-credit components prevent collaboration leakage."""
    from sklearn.model_selection import GroupShuffleSplit
    dev = [r for r in frozen['rows'] if r['equivalence'] in frozen['development']]
    if any(not artist_ids(r) for r in dev):
        return []
    parent = {}
    def root(a):
        parent.setdefault(a, a)
        if parent[a] != a:
            parent[a] = root(parent[a])
        return parent[a]
    equivalent_artists = defaultdict(set)
    for row in dev:
        equivalent_artists[row['equivalence']].update(artist_ids(row))
    for artists in equivalent_artists.values():
        ids = sorted(artists)
        for artist in ids[1:]:
            parent[root(artist)] = root(ids[0])
    grouped = [root(sorted(artist_ids(r))[0]) for r in dev]
    if len(set(grouped)) < 2:
        return []
    result = []
    for seed in SEEDS:
        for train, val in GroupShuffleSplit(n_splits=40, test_size=0.25, random_state=seed).split(dev, groups=grouped):
            if set(dev[i]['label'] for i in train) == set(store.LABELS) and set(dev[i]['label'] for i in val) == set(store.LABELS):
                result.append({'train': sorted({dev[i]['equivalence'] for i in train}),
                               'validation': sorted({dev[i]['equivalence'] for i in val}), 'seed': seed})
                break
    return result if len(result) == 3 else []


def load_dataset(user, key):
    rows = store.rows('SELECT * FROM ia_datasets WHERE id=? AND user_id=?', (key, user))
    if not rows:
        raise NotFound('Version de données introuvable.')
    return json.loads(rows[0]['payload'])


def launch(user, settings):
    preview = grid(settings)
    if not preview['within_limit']:
        raise BadRequest('La grille dépasse le plafond. Réduisez-la ou augmentez le plafond.')
    dataset_id = settings.get('dataset_id') or dataset(user)
    load_dataset(user, dataset_id)
    key = data.identifier()
    with storage.connect() as db:
        db.execute('INSERT INTO ia_campaigns VALUES(?,?,?,?,?,?)', (key, user, dataset_id, 'running', json.dumps(preview['settings']), storage.now()))
        for config in preview['configs']:
            for split in range(3):
                db.execute('INSERT INTO ia_trials(id,campaign_id,config_key,config,split) VALUES(?,?,?,?,?)',
                           (data.identifier(), key, config_key(config), json.dumps(config), split))
    return key


class TasteModel:
    """A model owns its training-only profile and fitted encoders."""
    def __init__(self, config, seed=17, threads=1):
        self.config, self.seed, self.threads = config, seed, threads

    def genres(self, row):
        return {g['name'].casefold() for g in row.get('genres', []) if self.config['musicbrainz'] or g['source'] != 'musicbrainz'}

    def profile(self, rows):
        self.training = rows
        self.artist_counts = defaultdict(Counter)
        self.genre_counts = defaultdict(Counter)
        self.artist_history = Counter()
        self.prior = Counter(r['label'] for r in rows)
        for row in rows:
            for artist in artist_ids(row):
                self.artist_counts[artist][row['label']] += 1
                self.artist_history[artist] += math.log1p(row.get('history_count', 0))
            for genre in self.genres(row):
                self.genre_counts[genre][row['label']] += 1

    def affinities(self, row, training=False):
        artists, genres = artist_ids(row), self.genres(row)
        ac, gc, history = Counter(), Counter(), 0.0
        for artist in artists:
            ac.update(self.artist_counts[artist])
            history += self.artist_history[artist]
        for genre in genres:
            gc.update(self.genre_counts[genre])
        if training:
            # Remove every equivalent version of the target from all profile features.
            for other in self.training:
                if other['equivalence'] != row['equivalence']:
                    continue
                for artist in artists & artist_ids(other):
                    ac[other['label']] -= 1
                    history -= math.log1p(other.get('history_count', 0))
                for genre in genres & self.genres(other):
                    gc[other['label']] -= 1
        def normalized(counts):
            total = sum(counts.values())
            return [counts[label] / total if total else 0.25 for label in store.LABELS]
        return normalized(ac), normalized(gc), max(0, history), sum(ac.values()) > 0

    def features(self, row, training=False):
        ac, gc, history, known = self.affinities(row, training)
        features = {'bias': 1., 'missing_artist': float(not artist_ids(row)), 'missing_genre': float(not self.genres(row)),
                    'missing_duration': float(not row.get('duration_ms')), 'artist_known': float(known), 'history_artist': math.log1p(history)}
        for index, label in enumerate(store.LABELS):
            features['artist_affinity:' + label] = ac[index]
            features['genre_affinity:' + label] = gc[index]
        for artist in artist_ids(row):
            features['artist:' + artist] = 1.
        for genre in self.genres(row):
            features['genre:' + genre] = 1.
        features['duration_minutes'] = (row.get('duration_ms') or 0) / 60000
        date = row.get('release_date') or (row.get('mb_release_date') if self.config['musicbrainz'] else None)
        try:
            features['release_year'] = (int(date[:4]) - 2000) / 25
            features['missing_date'] = 0.
        except (TypeError, ValueError):
            features['release_year'], features['missing_date'] = 0., 1.
        return features

    def fit(self, rows):
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.preprocessing import StandardScaler
        from sklearn.linear_model import LogisticRegression
        from sklearn.ensemble import RandomForestClassifier
        self.profile(rows)
        if self.config['model'] == 'artist':
            return self
        self.vectorizer = DictVectorizer(sparse=True)
        features = self.vectorizer.fit_transform([self.features(r, True) for r in rows])
        self.scaler = StandardScaler(with_mean=False)
        features = self.scaler.fit_transform(features)
        if self.config['model'] == 'logistic':
            self.estimator = LogisticRegression(**self.config['params'], random_state=self.seed, max_iter=2000)
        else:
            self.estimator = RandomForestClassifier(**self.config['params'], random_state=self.seed, n_jobs=self.threads)
        self.estimator.fit(features, [r['label'] for r in rows])
        return self

    def predict(self, rows):
        if self.config['model'] == 'artist':
            scores = []
            for row in rows:
                ac, _, _, known = self.affinities(row)
                scores.append(ac if known else [self.prior[label] / sum(self.prior.values()) for label in store.LABELS])
        else:
            matrix = self.scaler.transform(self.vectorizer.transform([self.features(r) for r in rows]))
            probs = self.estimator.predict_proba(matrix)
            scores = [[float(p[list(self.estimator.classes_).index(label)]) for label in store.LABELS] for p in probs]
        return [{'label': store.LABELS[max(range(4), key=lambda i: score[i])], 'score': max(score), 'scores': score} for score in scores]

    def explain(self, row, label):
        features = self.features(row)
        if self.config['model'] == 'artist':
            ac, _, _, known = self.affinities(row)
            return [{'feature': 'artist_affinity:' + label if known else 'class_prior', 'value': ac[store.LABELS.index(label)]}]
        names = self.vectorizer.get_feature_names_out()
        if self.config['model'] == 'logistic':
            coefficients = self.estimator.coef_[list(self.estimator.classes_).index(label)]
            vector = self.scaler.transform(self.vectorizer.transform([features])).toarray()[0]
            values = coefficients * vector
        else:
            # Global importance for present features, explicitly not a causal/local attribution.
            values = [value if features.get(name) else 0 for name, value in zip(names, self.estimator.feature_importances_)]
        order = sorted(range(len(names)), key=lambda i: abs(values[i]), reverse=True)[:5]
        return [{'feature': str(names[i]), 'value': float(values[i]), 'kind': 'global_importance' if self.config['model'] == 'forest' else 'linear_contribution'} for i in order]


def prepared_rows(frozen, groups, config):
    result = []
    for row in frozen['rows']:
        if row['equivalence'] in groups:
            result.append({**row, 'history_count': frozen['history_counts'].get(row['uri'], {}).get(config['period'], 0)})
    return result


def metrics(model, rows):
    from sklearn.metrics import classification_report, confusion_matrix, f1_score
    predictions = model.predict(rows)
    actual = [r['label'] for r in rows]
    guessed = [p['label'] for p in predictions]
    return {'n': len(rows), 'f1_macro': float(f1_score(actual, guessed, labels=store.LABELS, average='macro', zero_division=0)),
            'report': classification_report(actual, guessed, labels=store.LABELS, output_dict=True, zero_division=0),
            'confusion': confusion_matrix(actual, guessed, labels=store.LABELS).tolist(),
            'distant_errors': sum(abs(store.LABELS.index(a) - store.LABELS.index(b)) >= 2 for a, b in zip(actual, guessed)),
            'metadata_coverage': {'artists': sum(bool(artist_ids(r)) for r in rows), 'genres': sum(bool(model.genres(r)) for r in rows)},
            'abstentions': 0}


def save_artifact(key, model):
    folder = Path(current_app.config['DATA_DIR']) / 'ia' / 'models'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / (key + '.pickle')
    temporary = path.with_suffix('.tmp')
    with temporary.open('wb') as file:
        pickle.dump(model, file, protocol=pickle.HIGHEST_PROTOCOL)
        file.flush()
        import os
        os.fsync(file.fileno())
    temporary.replace(path)
    return str(path), path.stat().st_size


def read_artifact(path):
    # Artifacts are generated internally; users cannot upload or choose pickle paths.
    with Path(path).open('rb') as file:
        return pickle.load(file)


def run_trial(trial, campaign):
    from threadpoolctl import threadpool_limits
    frozen = load_dataset(campaign['user_id'], campaign['dataset_id'])
    config = json.loads(trial['config'])
    settings = json.loads(campaign['settings'])
    split = frozen['splits'][trial['split']]
    start = time.monotonic()
    with threadpool_limits(limits=settings['cpu_threads']):
        train = prepared_rows(frozen, split['train'], config)
        validation = prepared_rows(frozen, split['validation'], config)
        model = TasteModel(config, split['seed'], settings['cpu_threads']).fit(train)
        results = metrics(model, validation)
        import sklearn
        results['library_version'] = sklearn.__version__
        results['feature_version'] = FEATURE_VERSION
        known = [r for r in validation if artist_ids(r) and artist_ids(r) <= set(model.artist_counts)]
        results['known_artists'] = metrics(model, known) if known else None
        results['unknown_artists'] = None
        if frozen['artist_holdouts']:
            artist_split = frozen['artist_holdouts'][trial['split']]
            held = TasteModel(config, artist_split['seed'], settings['cpu_threads']).fit(prepared_rows(frozen, artist_split['train'], config))
            results['unknown_artists'] = metrics(held, prepared_rows(frozen, artist_split['validation'], config))
        path, size = save_artifact(trial['id'], model)
    store.execute("UPDATE ia_trials SET state='completed',metrics=?,duration=?,artifact=?,bytes=?,error=NULL WHERE id=?",
                  (json.dumps(results), time.monotonic() - start, path, size, trial['id']))


def results(user):
    campaigns = store.rows('SELECT * FROM ia_campaigns WHERE user_id=? ORDER BY created_at DESC', (user,))
    grouped = []
    for campaign in campaigns:
        trials = store.rows('SELECT * FROM ia_trials WHERE campaign_id=? ORDER BY config_key,split', (campaign['id'],))
        campaign['settings'] = json.loads(campaign['settings'])
        campaign['counts'] = dict(Counter(t['state'] for t in trials))
        configs = defaultdict(list)
        for trial in trials:
            trial['config'] = json.loads(trial['config'])
            trial['metrics'] = json.loads(trial['metrics']) if trial['metrics'] else None
            configs[trial['config_key']].append(trial)
        for key, entries in configs.items():
            scores = [t['metrics']['f1_macro'] for t in entries if t['state'] == 'completed']
            complete = len(scores) == 3
            grouped.append({'campaign_id': campaign['id'], 'dataset_id': campaign['dataset_id'], 'config_key': key,
                            'config': entries[0]['config'], 'complete': complete,
                            'f1_macro': statistics.mean(scores) if complete else None,
                            'dispersion': statistics.pstdev(scores) if complete else None,
                            'duration': sum(t['duration'] or 0 for t in entries), 'bytes': sum(t['bytes'] or 0 for t in entries), 'trials': entries})
    grouped.sort(key=lambda r: (r['f1_macro'] is not None, r['f1_macro'] or 0), reverse=True)
    models = store.rows('SELECT id,dataset_id,campaign_id,config_key,config,metrics,created_at,exploratory FROM ia_models WHERE user_id=? ORDER BY created_at DESC', (user,))
    for model in models:
        model['config'], model['metrics'] = json.loads(model['config']), json.loads(model['metrics'])
    return {'campaigns': campaigns, 'results': grouped, 'models': models, 'active_model': store.setting(user, 'active_model'),
            'datasets': [{'id': r['id'], 'created_at': r['created_at'], 'test_used': bool(r['test_used']),
                          'counts': dict(Counter(i['label'] for i in json.loads(r['payload'])['rows'])),
                          'coverage': json.loads(r['payload'])['coverage'],
                          'split_counts': json.loads(r['payload']).get('split_counts', []),
                          'test_counts': json.loads(r['payload']).get('test_counts', {})}
                         for r in store.rows('SELECT * FROM ia_datasets WHERE user_id=? ORDER BY created_at DESC', (user,))]}


def finalize(user, campaign_id, key):
    matches = [r for r in results(user)['results'] if r['campaign_id'] == campaign_id and r['config_key'] == key]
    if not matches or not matches[0]['complete']:
        raise BadRequest('Choisissez une configuration dont les trois essais sont terminés.')
    existing = store.rows('SELECT id FROM ia_models WHERE user_id=? AND campaign_id=? AND config_key=?', (user, campaign_id, key))
    if existing:
        return existing[0]['id']
    pending = store.rows("SELECT id FROM ia_jobs WHERE user_id=? AND kind='finalize' AND state IN ('queued','running') AND payload=?", (user, json.dumps({'campaign_id': campaign_id, 'config_key': key})))
    if pending:
        return pending[0]['id']
    return data.job(user, 'finalize', {'campaign_id': campaign_id, 'config_key': key})


def train_final(job):
    from threadpoolctl import threadpool_limits
    payload = json.loads(job['payload'])
    campaign = store.rows('SELECT * FROM ia_campaigns WHERE id=? AND user_id=?', (payload['campaign_id'], job['user_id']))[0]
    trial = store.rows('SELECT config FROM ia_trials WHERE campaign_id=? AND config_key=? LIMIT 1', (campaign['id'], payload['config_key']))[0]
    config = json.loads(trial['config'])
    frozen = load_dataset(job['user_id'], campaign['dataset_id'])
    threads = json.loads(campaign['settings'])['cpu_threads']
    with threadpool_limits(limits=threads):
        model = TasteModel(config, 17, threads).fit(prepared_rows(frozen, frozen['development'], config))
        score = metrics(model, prepared_rows(frozen, frozen['test'], config))
        path, _ = save_artifact(job['id'], model)
    with storage.connect() as db:
        db.execute('INSERT OR IGNORE INTO ia_models VALUES(?,?,?,?,?,?,?,?,?,0)',
                   (job['id'], job['user_id'], campaign['dataset_id'], campaign['id'], payload['config_key'], json.dumps(config), path, json.dumps(score), storage.now()))
        db.execute('UPDATE ia_datasets SET test_used=1 WHERE id=?', (campaign['dataset_id'],))


def train_exploratory(job):
    from threadpoolctl import threadpool_limits
    rows = {r['uri']: r for r in data.catalog(job['user_id'])}
    labeled = [{**rows[r['uri']], 'label': r['label']} for r in store.rows("SELECT uri,label FROM ia_annotations WHERE user_id=? AND reserved=0 AND label!='unknown'", (job['user_id'],)) if r['uri'] in rows]
    if set(r['label'] for r in labeled) != set(store.LABELS):
        raise ValueError('Le modèle exploratoire nécessite des avis de développement dans les quatre catégories.')
    config = {'model': 'logistic', 'params': {'C': 1, 'class_weight': 'balanced'}, 'period': 'all', 'musicbrainz': True, 'feature_version': FEATURE_VERSION}
    with threadpool_limits(limits=1):
        model = TasteModel(config).fit(labeled)
        path, _ = save_artifact(job['id'], model)
    store.execute('INSERT OR IGNORE INTO ia_models VALUES(?,?,NULL,NULL,NULL,?,?,?,?,1)',
                  (job['id'], job['user_id'], json.dumps(config), path, json.dumps({'note': 'Exploratoire, sans mesure indépendante'}), storage.now()))
    store.set_setting(job['user_id'], 'exploratory_model', job['id'])


def activate(user, key):
    rows = store.rows('SELECT artifact FROM ia_models WHERE id=? AND user_id=? AND exploratory=0', (key, user))
    if not rows:
        raise NotFound('Modèle finalisé introuvable.')
    if not Path(rows[0]['artifact']).is_file():
        raise BadRequest('Artefact du modèle introuvable. Le modèle actif reste inchangé.')
    store.set_setting(user, 'active_model', key)


def predictions(user, rows, exploratory=False, explanations=True):
    key = store.setting(user, 'exploratory_model' if exploratory else 'active_model')
    versions = store.rows('SELECT * FROM ia_models WHERE id=? AND user_id=?', (key, user)) if key else []
    annotations = {r['uri']: r['label'] for r in store.rows('SELECT uri,label FROM ia_annotations WHERE user_id=?', (user,))}
    model = read_artifact(versions[0]['artifact']) if versions else None
    outputs = model.predict(rows) if model and rows else [{} for _ in rows]
    result = []
    for row, prediction in zip(rows, outputs):
        known = annotations.get(row['uri'])
        supported = bool(artist_ids(row) or (model and model.genres(row)) or row.get('duration_ms') or row.get('release_date'))
        entry = {'uri': row['uri'], 'annotation': known, 'prediction': prediction.get('label') if supported else None,
                 'score': prediction.get('score') if supported else None, 'model_id': key,
                 'coverage': {'artists': bool(artist_ids(row)), 'genres': bool(row.get('genres')), 'duration': bool(row.get('duration_ms'))},
                 'factors': model.explain(row, prediction['label']) if explanations and model and supported else []}
        result.append(entry)
    return result
