from flask import Blueprint, request, jsonify
from app.db_connection import get_collection

sort_bp = Blueprint('sort', __name__)

@sort_bp.route('/get_sort_options', methods=['GET'])
def get_sort_options():
    try:
        track_template = {
            "album": {
                "album_type": "compilation",
                "total_tracks": 9,
                "release_date": "1981-12",
                "name": "string"
            },
            "artists": [
                {
                    "name": "string"
                }
            ],
            "duration_ms": 0,
            "name": "string",
            "popularity": 0,
            "track_number": 0
        }

        dynamic_sort_options = []

        def process_fields(prefix, obj):
            for key, value in obj.items():
                if isinstance(value, dict):
                    process_fields(f"{prefix}.{key}" if prefix else key, value)
                elif isinstance(value, list):
                    if value and isinstance(value[0], dict):
                        process_fields(f"{prefix}.{key}[0]" if prefix else f"{key}[0]", value[0])
                else:
                    display_name_prefix = prefix.replace(".", " ").replace("_", " ").title() if prefix else ""
                    field_name = f"{display_name_prefix} {key.replace('_', ' ').title()}".strip()

                    dynamic_sort_options.append({
                        'id': f'{prefix}.{key}_asc',
                        'name': f'{field_name} (Ascending)',
                        'field': f'{prefix}.{key}' if prefix else f'{key}',
                        'order': 'asc'
                    })
                    dynamic_sort_options.append({
                        'id': f'{prefix}.{key}_desc',
                        'name': f'{field_name} (Descending)',
                        'field': f'{prefix}.{key}' if prefix else key,
                        'order': 'desc'
                    })

        process_fields("", track_template)

        return jsonify(dynamic_sort_options)
    except Exception as e:
        print(f"Error in get_sort_options: {e}")
        return jsonify({'error': str(e)}), 500


@sort_bp.route('/sort_playlist_tracks', methods=['POST'])
def sort_playlist_tracks():
    try:
        data = request.json
        playlist_id = data['playlist_id']
        sort_option = data.get('sort_option', 'name_asc')

        cached_tracks = get_collection('playlist_tracks').find_one({"_id": playlist_id})
        if not cached_tracks:
            return jsonify({'error': 'Tracks not found in cache for the specified playlist'}), 404

        track_list = cached_tracks['tracks']

        reverse = sort_option.endswith('_desc')
        field_path = sort_option.rsplit('_', 1)[0].lstrip('.')
        field_parts = field_path.split('.')
        print(f"Sorting tracks by field: reverse={reverse}, field_path={field_path}, field_parts={field_parts}")

        def get_nested_field(obj, field_parts):
            try:
                obj = obj.get('track', obj)

                for part in field_parts:
                    if '[' in part and ']' in part:
                        part, index = part.split('[')
                        index = int(index[:-1])
                        obj = obj.get(part, [])[index]
                    else:
                        obj = obj.get(part)
                    if obj is None:
                        return None
                return obj
            except (KeyError, IndexError, TypeError) as e:
                print(f"Error retrieving field '{'.'.join(field_parts)}': {e}")
                return None

        sorted_tracks = sorted(
            track_list,
            key=lambda track: (
                get_nested_field(track, field_parts)
                if isinstance(get_nested_field(track, field_parts), (int, float))
                else str(get_nested_field(track, field_parts) or '')
            ),
            reverse=reverse
        )

        return jsonify({'tracks': sorted_tracks})
    except Exception as e:
        print(f"Error in sort_playlist_tracks: {e}")
        return jsonify({'error': str(e)}), 500