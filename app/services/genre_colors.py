"""Local genre taxonomy used for graph colors and compact clusters.

The taxonomy is intentionally local: it gives common Spotify and MusicBrainz
labels a predictable parent family and subfamily without a runtime dependency.
Labels that remain ambiguous are resolved by graph co-occurrence instead.
"""

import colorsys
import hashlib
import re
import unicodedata


FAMILIES = {
    "Electronic": 288, "Rock": 0, "Pop": 324, "Hip-Hop": 48,
    "Jazz": 216, "Funk / Soul": 72, "Classical": 264, "Blues": 240,
    "Folk, World, & Country": 120, "Latin": 24, "Reggae": 144,
    "African": 156, "East Asian Pop": 306, "Experimental": 180,
    "Brass & Military": 168, "Stage & Screen": 192, "Children's": 96,
    "Non-Music": 348,
}

# (parent family, subfamily, aliases). Specific entries deliberately precede
# broader matches so that "indie pop" does not collapse into generic Pop.
TAXONOMY = (
    ("Rock", "Metal", ("metal", "death metal", "black metal", "doom metal", "thrash metal", "metalcore", "nu metal")),
    ("Rock", "Punk & Hardcore", ("punk", "hardcore", "post hardcore", "pop punk", "psychobilly")),
    ("Rock", "Indie & Alternative", ("indie", "alternative", "grunge", "shoegaze", "post rock", "emo", "britpop", "dream pop")),
    ("Rock", "Rock", ("rock", "classic rock", "progressive rock", "psychedelic rock")),
    ("Electronic", "Dance & Club", ("electronic", "electronica", "house", "techno", "trance", "edm", "garage", "electro", "hardstyle", "eurodance")),
    ("Electronic", "Bass & Breaks", ("drum and bass", "dnb", "dubstep", "breakbeat", "jungle", "uk bass")),
    ("Electronic", "Ambient & Downtempo", ("ambient", "downtempo", "idm", "chillout", "drone", "synthwave")),
    ("Pop", "Indie Pop", ("indie pop", "bedroom pop", "dream pop")),
    ("Pop", "Pop", ("pop", "synth pop", "hyperpop", "schlager")),
    ("Hip-Hop", "Rap & Trap", ("hip hop", "hip-hop", "rap", "trap", "boom bap", "drill", "grime")),
    ("Jazz", "Jazz", ("jazz", "bebop", "bop", "swing", "fusion")),
    ("Funk / Soul", "Funk & Soul", ("funk", "soul", "r&b", "rhythm and blues", "disco", "motown", "gospel")),
    ("Classical", "Classical", ("classical", "baroque", "romanticism", "opera", "chamber music", "orchestral")),
    ("Blues", "Blues", ("blues", "delta blues", "chicago blues")),
    ("Folk, World, & Country", "Country & Americana", ("country", "bluegrass", "americana")),
    ("Folk, World, & Country", "Folk & World", ("folk", "traditional", "world music", "chanson")),
    ("Latin", "Latin", ("latin", "salsa", "bachata", "merengue", "reggaeton", "bossa nova", "samba", "cumbia", "tango")),
    ("Reggae", "Reggae & Dub", ("reggae", "dub", "ska", "dancehall", "rocksteady")),
    ("African", "Afrobeats", ("afrobeats", "afrobeat", "amapiano", "highlife", "bongo flava")),
    ("East Asian Pop", "K-pop", ("k pop", "k-pop", "korean pop")),
    ("East Asian Pop", "J-pop", ("j pop", "j-pop", "japanese pop", "city pop")),
    ("Experimental", "Experimental & Noise", ("experimental", "noise", "avant garde", "industrial", "glitch")),
    ("Brass & Military", "Brass & Military", ("brass", "marching band", "military")),
    ("Stage & Screen", "Stage & Screen", ("soundtrack", "film score", "musical", "stage and screen")),
    ("Children's", "Children's", ("children's music", "kids music", "nursery rhyme")),
    ("Non-Music", "Non-Music", ("spoken word", "comedy", "audiobook", "field recording")),
)

EXACT = {
    "indie pop": ("Pop", "Indie Pop"), "indie rock": ("Rock", "Indie & Alternative"),
    "dream pop": ("Pop", "Indie Pop"), "pop rock": ("Rock", "Rock"),
    "synth pop": ("Pop", "Pop"), "pop punk": ("Rock", "Punk & Hardcore"),
    "jazz fusion": ("Jazz", "Jazz"), "latin jazz": ("Jazz", "Jazz"),
    "electro swing": ("Electronic", "Dance & Club"), "folk rock": ("Rock", "Rock"),
    "country rock": ("Rock", "Rock"), "blues rock": ("Rock", "Rock"),
    "psychedelic soul": ("Funk / Soul", "Funk & Soul"),
    "rhythm and blues": ("Funk / Soul", "Funk & Soul"),
}


def normalize(value):
    folded = unicodedata.normalize("NFKD", value or "")
    ascii_name = "".join(char for char in folded if not unicodedata.combining(char)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", ascii_name).strip()


def classify_details(name):
    key = normalize(name)
    if not key:
        return None, None
    if key in EXACT:
        return EXACT[key]
    matches = []
    for family, subfamily, aliases in TAXONOMY:
        for alias in aliases:
            term = normalize(alias)
            if re.search(rf"(^| ){re.escape(term)}($| )", key):
                matches.append((len(term.split()), family, subfamily))
    if not matches:
        return None, None
    longest = max(match[0] for match in matches)
    candidates = {(family, subfamily) for length, family, subfamily in matches if length == longest}
    return next(iter(candidates)) if len(candidates) == 1 else (None, None)


def classify(name):
    return classify_details(name)[0]


def color_for_family(name, family):
    if family is None:
        return "#9aa3a9"
    shade = hashlib.sha256(normalize(name).encode("utf-8")).digest()
    hue = ((FAMILIES[family] + shade[0] % 9 - 4) % 360) / 360
    saturation = 0.76 + shade[1] / 255 * 0.1
    lightness = 0.54 + shade[2] / 255 * 0.08
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return "#{:02x}{:02x}{:02x}".format(round(red * 255), round(green * 255), round(blue * 255))


def color_for(name):
    family = classify(name)
    return family, color_for_family(name, family)


def border_for(color):
    red, green, blue = (int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    red, green, blue = colorsys.hls_to_rgb(hue, max(0, lightness - 0.18), saturation)
    return "#{:02x}{:02x}{:02x}".format(round(red * 255), round(green * 255), round(blue * 255))
