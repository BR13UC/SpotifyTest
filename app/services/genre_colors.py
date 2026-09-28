"""Local genre families based on Discogs' genre/style grouping.

Discogs is a taxonomy reference, not a runtime dependency. Spotify and
MusicBrainz labels that cannot be mapped confidently stay unclassified.
"""

import colorsys
import hashlib
import re
import unicodedata


# (rainbow hue, recognized genre/style terms)
FAMILIES = {
    "Electronic": (288, ("electronic", "electronica", "house", "techno", "trance", "ambient", "drum and bass", "dnb", "dubstep", "synthwave", "breakbeat", "electro", "edm", "garage", "downtempo", "idm")),
    "Rock": (0, ("rock", "metal", "punk", "grunge", "shoegaze", "hardcore", "post-rock", "emo", "psychobilly")),
    "Pop": (324, ("pop", "synth-pop", "hyperpop", "schlager")),
    "Hip-Hop": (48, ("hip hop", "hip-hop", "rap", "trap", "boom bap", "drill", "grime")),
    "Jazz": (216, ("jazz", "bebop", "bop", "swing", "fusion")),
    "Funk / Soul": (72, ("funk", "soul", "r&b", "rhythm and blues", "disco", "motown", "gospel")),
    "Classical": (264, ("classical", "baroque", "romanticism", "opera", "chamber music", "orchestral")),
    "Blues": (240, ("blues", "delta blues", "chicago blues")),
    "Folk, World, & Country": (120, ("folk", "country", "bluegrass", "americana", "traditional", "world music", "chanson")),
    "Latin": (24, ("latin", "salsa", "bachata", "merengue", "reggaeton", "bossa nova", "samba", "cumbia", "tango")),
    "Reggae": (144, ("reggae", "dub", "ska", "dancehall", "rocksteady")),
    "Brass & Military": (168, ("brass", "marching band", "military")),
    "Stage & Screen": (192, ("soundtrack", "film score", "musical", "stage and screen")),
    "Children's": (96, ("children's music", "kids music", "nursery rhyme")),
    "Non-Music": (348, ("spoken word", "comedy", "audiobook", "field recording")),
}

EXACT = {
    "indie pop": "Pop",
    "pop rock": "Rock",
    "synth pop": "Pop",
    "pop punk": "Rock",
    "jazz fusion": "Jazz",
    "latin jazz": "Jazz",
    "electro swing": "Electronic",
    "folk rock": "Rock",
    "country rock": "Rock",
    "blues rock": "Rock",
    "psychedelic soul": "Funk / Soul",
    "rhythm and blues": "Funk / Soul",
}


def normalize(value):
    folded = unicodedata.normalize("NFKD", value or "")
    ascii_name = "".join(char for char in folded if not unicodedata.combining(char)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", ascii_name).strip()


def classify(name):
    key = normalize(name)
    if not key:
        return None
    if key in EXACT:
        return EXACT[key]
    matches = set()
    for family, (_hue, aliases) in FAMILIES.items():
        for alias in aliases:
            term = normalize(alias)
            if re.search(rf"(^| ){re.escape(term)}($| )", key):
                matches.add(family)
                break
    return next(iter(matches)) if len(matches) == 1 else None


def color_for(name):
    family = classify(name)
    if family is None:
        return None, "#9aa3a9"
    key = normalize(name)
    shade = hashlib.sha256(key.encode("utf-8")).digest()
    hue = ((FAMILIES[family][0] + shade[0] % 9 - 4) % 360) / 360
    saturation = 0.76 + shade[1] / 255 * 0.1
    lightness = 0.54 + shade[2] / 255 * 0.08
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return family, "#{:02x}{:02x}{:02x}".format(round(red * 255), round(green * 255), round(blue * 255))


def border_for(color):
    red, green, blue = (int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    red, green, blue = colorsys.hls_to_rgb(hue, max(0, lightness - 0.18), saturation)
    return "#{:02x}{:02x}{:02x}".format(round(red * 255), round(green * 255), round(blue * 255))
