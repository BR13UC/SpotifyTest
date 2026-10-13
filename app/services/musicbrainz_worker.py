"""Run the durable MusicBrainz queue independently of web requests."""

import time

from app import create_app, storage
from app.services.musicbrainz import enqueue_rechecks, process_one
from app.services.geography import process_area_one, recover_areas


def main():
    app = storage.wait_for_database(create_app)
    with app.app_context():
        storage.wait_for_database(enqueue_rechecks)
        storage.wait_for_database(recover_areas)
        while True:
            try:
                area_work = False
                for _ in range(5):
                    if not process_area_one():
                        break
                    area_work = True
                artist_work = process_one()
            except Exception as exc:
                if not storage.is_busy_error(exc):
                    raise
                app.logger.warning('MusicBrainz queue temporarily busy; recovering interrupted work')
                storage.wait_for_database(enqueue_rechecks)
                storage.wait_for_database(recover_areas)
                time.sleep(2)
                continue
            if not artist_work and not area_work:
                time.sleep(2)


if __name__ == "__main__":
    main()
