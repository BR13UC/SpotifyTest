"""Run the durable MusicBrainz queue independently of web requests."""

import time

from app import create_app
from app.services.musicbrainz import enqueue_rechecks, process_one
from app.services.geography import process_area_one, recover_areas


def main():
    app = create_app()
    with app.app_context():
        enqueue_rechecks()
        recover_areas()
        while True:
            area_work = False
            for _ in range(5):
                if not process_area_one():
                    break
                area_work = True
            artist_work = process_one()
            if not artist_work and not area_work:
                time.sleep(2)


if __name__ == "__main__":
    main()
