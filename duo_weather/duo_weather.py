import base64
import logging
import os
import random
import re
import unicodedata
from io import BytesIO

import pytz
import requests
import datetime
from PIL import Image, ImageDraw, ImageFilter

from plugins.weather.weather import UNITS, Weather

logger = logging.getLogger(__name__)

REVERSE_GEOCODE_URL = (
    "https://nominatim.openstreetmap.org/reverse"
    "?lat={lat}&lon={long}&format=jsonv2&addressdetails=1&zoom=10"
)

# Simple in-memory cache for reverse-geocoded titles to avoid hitting Nominatim
# on every refresh. Keys are rounded coordinate pairs to tolerate tiny changes.
REVERSE_GEOCODE_CACHE = {}
# TTL for successful reverse geocode results (seconds)
REVERSE_GEOCODE_SUCCESS_TTL = 7 * 24 * 60 * 60  # 7 days
# TTL for failed attempts (seconds) to avoid tight retry loops
REVERSE_GEOCODE_FAIL_TTL = 60 * 60  # 1 hour
REVERSE_GEOCODE_ROUND_DECIMALS = 4

HOURLY_POINT_COUNT = 6
HOURLY_STEP_HOURS = 2

QUICK_LOCATION_LABELS = {
    "52.3676,4.9041": "Amsterdam",
    "52.5200,13.4050": "Berlin",
    "-34.6037,-58.3816": "Buenos Aires",
    "-6.2088,106.8456": "Jakarta",
    "51.5074,-0.1278": "London",
    "40.4168,-3.7038": "Madrid",
    "40.7128,-74.0060": "New York",
    "48.8566,2.3522": "Paris",
    "-22.9068,-43.1729": "Rio de Janeiro",
    "41.9028,12.4964": "Rome",
    "-23.5505,-46.6333": "São Paulo",
    "35.6762,139.6503": "Tokyo",
}

QUICK_LOCATION_COORDS = {
    city: tuple(map(float, coords.split(",")))
    for coords, city in QUICK_LOCATION_LABELS.items()
}

LANGUAGE_LABELS = {
    "de": {
        "now": "JETZT",
        "high": "H",
        "low": "T",
        "days": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
        "conditions": {
            "clear": "Klar",
            "mostly_sunny": "Meist sonnig",
            "mostly_clear": "Meist klar",
            "partly_cloudy": "Teilweise bewölkt",
            "cloudy": "Bewölkt",
            "overcast": "Bedeckt",
            "fog": "Nebel",
            "icy_fog": "Eisnebel",
            "drizzle": "Nieselregen",
            "rain": "Regen",
            "heavy_rain": "Starker Regen",
            "freezing_rain": "Gefrierender Regen",
            "snow": "Schnee",
            "thunderstorm": "Gewitter",
        },
    },
    "en": {
        "now": "NOW",
        "high": "H",
        "low": "L",
        "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "conditions": {
            "clear": "Clear",
            "mostly_sunny": "Mostly Sunny",
            "mostly_clear": "Mostly Clear",
            "partly_cloudy": "Partly Cloudy",
            "cloudy": "Cloudy",
            "overcast": "Overcast",
            "fog": "Fog",
            "icy_fog": "Icy Fog",
            "drizzle": "Drizzle",
            "rain": "Rain",
            "heavy_rain": "Heavy Rain",
            "freezing_rain": "Freezing Rain",
            "snow": "Snow",
            "thunderstorm": "Thunderstorm",
        },
    },
    "es": {
        "now": "AHORA",
        "high": "M",
        "low": "m",
        "days": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
        "conditions": {
            "clear": "Despejado",
            "mostly_sunny": "Mayormente soleado",
            "mostly_clear": "Mayormente despejado",
            "partly_cloudy": "Parcialmente nublado",
            "cloudy": "Nublado",
            "overcast": "Cubierto",
            "fog": "Niebla",
            "icy_fog": "Niebla helada",
            "drizzle": "Llovizna",
            "rain": "Lluvia",
            "heavy_rain": "Lluvia intensa",
            "freezing_rain": "Lluvia helada",
            "snow": "Nieve",
            "thunderstorm": "Tormenta",
        },
    },
    "fr": {
        "now": "MAINT",
        "high": "M",
        "low": "m",
        "days": ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"],
        "conditions": {
            "clear": "Clair",
            "mostly_sunny": "Plutôt ensoleillé",
            "mostly_clear": "Plutôt dégagé",
            "partly_cloudy": "Partiellement nuageux",
            "cloudy": "Nuageux",
            "overcast": "Couvert",
            "fog": "Brouillard",
            "icy_fog": "Brouillard givrant",
            "drizzle": "Bruine",
            "rain": "Pluie",
            "heavy_rain": "Forte pluie",
            "freezing_rain": "Pluie verglaçante",
            "snow": "Neige",
            "thunderstorm": "Orage",
        },
    },
    "id": {
        "now": "SEK",
        "high": "T",
        "low": "R",
        "days": ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"],
        "conditions": {
            "clear": "Cerah",
            "mostly_sunny": "Cerah berawan",
            "mostly_clear": "Cerah",
            "partly_cloudy": "Berawan sebagian",
            "cloudy": "Berawan",
            "overcast": "Mendung",
            "fog": "Kabut",
            "icy_fog": "Kabut es",
            "drizzle": "Gerimis",
            "rain": "Hujan",
            "heavy_rain": "Hujan lebat",
            "freezing_rain": "Hujan beku",
            "snow": "Salju",
            "thunderstorm": "Badai petir",
        },
    },
    "it": {
        "now": "ORA",
        "high": "M",
        "low": "m",
        "days": ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"],
        "conditions": {
            "clear": "Sereno",
            "mostly_sunny": "Prevalentemente soleggiato",
            "mostly_clear": "Prevalentemente sereno",
            "partly_cloudy": "Parzialmente nuvoloso",
            "cloudy": "Nuvoloso",
            "overcast": "Coperto",
            "fog": "Nebbia",
            "icy_fog": "Nebbia gelata",
            "drizzle": "Pioggerella",
            "rain": "Pioggia",
            "heavy_rain": "Pioggia intensa",
            "freezing_rain": "Pioggia gelata",
            "snow": "Neve",
            "thunderstorm": "Temporale",
        },
    },
    "nl": {
        "now": "NU",
        "high": "H",
        "low": "L",
        "days": ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"],
        "conditions": {
            "clear": "Helder",
            "mostly_sunny": "Overwegend zonnig",
            "mostly_clear": "Overwegend helder",
            "partly_cloudy": "Gedeeltelijk bewolkt",
            "cloudy": "Bewolkt",
            "overcast": "Zwaar bewolkt",
            "fog": "Mist",
            "icy_fog": "IJsmist",
            "drizzle": "Motregen",
            "rain": "Regen",
            "heavy_rain": "Zware regen",
            "freezing_rain": "IJzel",
            "snow": "Sneeuw",
            "thunderstorm": "Onweer",
        },
    },
    "pt": {
        "now": "AGORA",
        "high": "M",
        "low": "m",
        "days": ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"],
        "conditions": {
            "clear": "Limpo",
            "mostly_sunny": "Predominantemente ensolarado",
            "mostly_clear": "Predominantemente limpo",
            "partly_cloudy": "Parcialmente nublado",
            "cloudy": "Nublado",
            "overcast": "Encoberto",
            "fog": "Neblina",
            "icy_fog": "Névoa gelada",
            "drizzle": "Garoa",
            "rain": "Chuva",
            "heavy_rain": "Chuva forte",
            "freezing_rain": "Chuva congelante",
            "snow": "Neve",
            "thunderstorm": "Tempestade",
        },
    },
}

# month names for a handful of supported languages; keep capitalized first letter
MONTH_NAMES = {
    "en": [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
    "pt": [
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ],
    "es": [
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ],
    "fr": [
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ],
    "de": [
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ],
    "it": [
        "gennaio",
        "febbraio",
        "marzo",
        "aprile",
        "maggio",
        "giugno",
        "luglio",
        "agosto",
        "settembre",
        "ottobre",
        "novembre",
        "dicembre",
    ],
    "nl": [
        "januari",
        "februari",
        "maart",
        "april",
        "mei",
        "juni",
        "juli",
        "augustus",
        "september",
        "oktober",
        "november",
        "december",
    ],
    "id": [
        "Januari",
        "Februari",
        "Maret",
        "April",
        "Mei",
        "Juni",
        "Juli",
        "Agustus",
        "September",
        "Oktober",
        "November",
        "Desember",
    ],
}

ICON_CONDITION_KEYS = {
    "01d": "clear",
    "01n": "clear",
    "022d": "mostly_sunny",
    "022n": "mostly_clear",
    "02d": "partly_cloudy",
    "02n": "partly_cloudy",
    "03d": "cloudy",
    "03n": "cloudy",
    "04d": "overcast",
    "04n": "overcast",
    "09d": "heavy_rain",
    "09n": "heavy_rain",
    "10d": "rain",
    "10n": "rain",
    "11d": "thunderstorm",
    "11n": "thunderstorm",
    "13d": "snow",
    "13n": "snow",
    "48d": "icy_fog",
    "48n": "icy_fog",
    "50d": "fog",
    "50n": "fog",
    "51d": "drizzle",
    "51n": "drizzle",
    "53d": "rain",
    "53n": "rain",
    "56d": "freezing_rain",
    "56n": "freezing_rain",
    "57d": "freezing_rain",
    "57n": "freezing_rain",
    "71d": "snow",
    "71n": "snow",
    "73d": "snow",
    "73n": "snow",
    "77d": "snow",
    "77n": "snow",
}

SKY_BY_CONDITION = {
    "clear": "clear",
    "mostly_sunny": "mostly-sunny",
    "mostly_clear": "mostly-clear",
    "partly_cloudy": "partly-cloudy",
    "cloudy": "cloudy",
    "overcast": "overcast",
    "fog": "fog",
    "icy_fog": "fog",
    "drizzle": "drizzle",
    "rain": "rain",
    "heavy_rain": "heavy-rain",
    "freezing_rain": "freezing-rain",
    "snow": "snow",
    "thunderstorm": "thunderstorm",
}

CLOUDY_CLOUD_MASSES = [
    {
        "x": -0.06,
        "y": 0.28,
        "scale": 0.24,
        "stretch": 1.55,
        "height": 0.34,
        "puffs": [
            {"offset": -0.16, "width": 0.32, "height": 0.90, "rise": 0.13},
            {"offset": 0.18, "width": 0.27, "height": 1.12, "rise": 0.20},
        ],
    },
    {
        "x": 0.84,
        "y": 0.23,
        "scale": 0.27,
        "stretch": 1.45,
        "height": 0.36,
        "puffs": [
            {"offset": -0.28, "width": 0.25, "height": 0.84, "rise": 0.10},
            {"offset": 0.02, "width": 0.38, "height": 1.08, "rise": 0.22},
            {"offset": 0.34, "width": 0.22, "height": 0.72, "rise": 0.07},
        ],
    },
    {
        "x": 0.17,
        "y": 0.72,
        "scale": 0.27,
        "stretch": 1.70,
        "height": 0.28,
        "puffs": [
            {"offset": -0.28, "width": 0.23, "height": 0.70, "rise": 0.08},
            {"offset": 0.13, "width": 0.34, "height": 0.92, "rise": 0.16},
        ],
    },
    {
        "x": 1.06,
        "y": 0.68,
        "scale": 0.25,
        "stretch": 1.60,
        "height": 0.30,
        "puffs": [
            {"offset": -0.18, "width": 0.35, "height": 0.86, "rise": 0.14},
            {"offset": 0.25, "width": 0.23, "height": 0.68, "rise": 0.06},
        ],
    },
]

OVERCAST_CLOUD_MASSES = [
    {
        "x": -0.08,
        "y": 0.24,
        "scale": 0.29,
        "stretch": 1.70,
        "height": 0.34,
        "puffs": [
            {"offset": -0.16, "width": 0.32, "height": 0.82, "rise": 0.11},
            {"offset": 0.18, "width": 0.28, "height": 0.96, "rise": 0.17},
        ],
    },
    {
        "x": 0.28,
        "y": 0.36,
        "scale": 0.25,
        "stretch": 1.80,
        "height": 0.30,
        "puffs": [
            {"offset": -0.25, "width": 0.26, "height": 0.72, "rise": 0.08},
            {"offset": 0.08, "width": 0.36, "height": 0.84, "rise": 0.14},
            {"offset": 0.38, "width": 0.22, "height": 0.62, "rise": 0.04},
        ],
    },
    {
        "x": 0.64,
        "y": 0.22,
        "scale": 0.30,
        "stretch": 1.65,
        "height": 0.32,
        "puffs": [
            {"offset": -0.30, "width": 0.24, "height": 0.72, "rise": 0.06},
            {"offset": 0.00, "width": 0.34, "height": 0.92, "rise": 0.18},
            {"offset": 0.30, "width": 0.28, "height": 0.76, "rise": 0.10},
        ],
    },
    {
        "x": 1.08,
        "y": 0.37,
        "scale": 0.30,
        "stretch": 1.75,
        "height": 0.32,
        "puffs": [
            {"offset": -0.22, "width": 0.35, "height": 0.88, "rise": 0.14},
            {"offset": 0.20, "width": 0.25, "height": 0.68, "rise": 0.06},
        ],
    },
    {
        "x": 0.18,
        "y": 0.73,
        "scale": 0.31,
        "stretch": 1.90,
        "height": 0.26,
        "puffs": [
            {"offset": -0.20, "width": 0.30, "height": 0.64, "rise": 0.06},
            {"offset": 0.18, "width": 0.38, "height": 0.72, "rise": 0.12},
        ],
    },
    {
        "x": 0.83,
        "y": 0.75,
        "scale": 0.33,
        "stretch": 1.85,
        "height": 0.25,
        "puffs": [
            {"offset": -0.28, "width": 0.24, "height": 0.58, "rise": 0.04},
            {"offset": 0.12, "width": 0.36, "height": 0.76, "rise": 0.10},
            {"offset": 0.42, "width": 0.20, "height": 0.52, "rise": 0.02},
        ],
    },
]

def _background_preset(
    gradient,
    glow=None,
    clouds=None,
    haze=None,
    precipitation=None,
    effects=None,
    text_shadow=0.4,
    text_opacity=1.0,
):
    return {
        "gradient": gradient,
        "glow": glow,
        "clouds": clouds,
        "haze": haze,
        "precipitation": precipitation,
        "effects": effects,
        "text_shadow": text_shadow,
        "text_opacity": text_opacity,
    }


BACKGROUND_PRESETS = {
    "clear": _background_preset(
        [(0.0, (24, 104, 204)), (0.52, (61, 154, 222)), (1.0, (166, 218, 237))],
        glow={"kind": "sun", "x": 0.78, "y": 0.18, "radius": 0.36, "color": (255, 220, 112), "alpha": 110},
        effects={"stars": 0},
        text_shadow=0.32,
    ),
    "clear-night": _background_preset(
        [(0.0, (7, 17, 52)), (0.52, (18, 39, 87)), (1.0, (65, 84, 130))],
        glow={"kind": "moon", "x": 0.78, "y": 0.18, "radius": 0.29, "color": (184, 218, 255), "alpha": 92},
        effects={"stars": 15, "seed": 11},
        text_shadow=0.48,
    ),
    "mostly-sunny": _background_preset(
        [(0.0, (43, 125, 208)), (0.52, (103, 180, 226)), (1.0, (194, 224, 237))],
        glow={"kind": "sun", "x": 0.78, "y": 0.19, "radius": 0.34, "color": (255, 218, 108), "alpha": 105},
        clouds={"count": 1, "y": (0.40, 0.60), "scale": 0.28, "color": (231, 242, 246), "alpha": 52, "blur": 0.025, "seed": 21},
        text_shadow=0.34,
    ),
    "mostly-clear-night": _background_preset(
        [(0.0, (9, 26, 66)), (0.52, (24, 52, 101)), (1.0, (84, 105, 143))],
        glow={"kind": "moon", "x": 0.77, "y": 0.19, "radius": 0.27, "color": (187, 220, 255), "alpha": 88},
        clouds={"count": 1, "y": (0.45, 0.63), "scale": 0.24, "color": (161, 183, 204), "alpha": 36, "blur": 0.03, "seed": 22},
        effects={"stars": 10, "seed": 12},
        text_shadow=0.5,
    ),
    "partly-cloudy": _background_preset(
        [(0.0, (65, 133, 193)), (0.52, (122, 180, 212)), (1.0, (191, 216, 227))],
        glow={"kind": "sun", "x": 0.75, "y": 0.24, "radius": 0.25, "color": (255, 218, 125), "alpha": 70},
        clouds={"count": 2, "y": (0.25, 0.62), "scale": 0.31, "color": (224, 237, 243), "alpha": 76, "blur": 0.02, "seed": 31},
        text_shadow=0.38,
    ),
    "partly-cloudy-night": _background_preset(
        [(0.0, (16, 38, 74)), (0.52, (37, 68, 103)), (1.0, (98, 119, 145))],
        glow={"kind": "moon", "x": 0.76, "y": 0.25, "radius": 0.23, "color": (182, 215, 248), "alpha": 52},
        clouds={"count": 2, "y": (0.26, 0.64), "scale": 0.29, "color": (135, 161, 183), "alpha": 58, "blur": 0.025, "seed": 32},
        effects={"stars": 5, "seed": 13},
        text_shadow=0.5,
    ),
    "cloudy": _background_preset(
        [(0.0, (91, 117, 143)), (0.52, (136, 157, 173)), (1.0, (191, 202, 209))],
        glow={"kind": "sun", "x": 0.72, "y": 0.22, "radius": 0.22, "color": (226, 225, 194), "alpha": 28},
        clouds={"masses": CLOUDY_CLOUD_MASSES, "color": (175, 191, 199), "alpha": 76, "blur": 0.028, "seed": 41},
        text_shadow=0.42,
    ),
    "cloudy-night": _background_preset(
        [(0.0, (21, 35, 57)), (0.52, (43, 64, 86)), (1.0, (87, 104, 119))],
        clouds={"masses": CLOUDY_CLOUD_MASSES, "color": (57, 75, 91), "alpha": 92, "blur": 0.028, "seed": 42},
        effects={"stars": 2, "seed": 14},
        text_shadow=0.56,
    ),
    "overcast": _background_preset(
        [(0.0, (106, 128, 143)), (0.52, (149, 164, 173)), (1.0, (194, 201, 204))],
        clouds={"masses": OVERCAST_CLOUD_MASSES, "color": (131, 149, 157), "alpha": 68, "blur": 0.04, "seed": 51},
        haze=[{"y": 0.57, "height": 0.28, "color": (217, 224, 225), "alpha": 38, "blur": 0.04}],
        text_shadow=0.43,
        text_opacity=0.98,
    ),
    "overcast-night": _background_preset(
        [(0.0, (35, 45, 57)), (0.52, (56, 68, 80)), (1.0, (94, 102, 109))],
        clouds={"masses": OVERCAST_CLOUD_MASSES, "color": (37, 50, 63), "alpha": 92, "blur": 0.04, "seed": 52},
        haze=[{"y": 0.63, "height": 0.26, "color": (117, 127, 132), "alpha": 32, "blur": 0.04}],
        text_shadow=0.58,
    ),
    "drizzle": _background_preset(
        [(0.0, (68, 103, 131)), (0.52, (105, 138, 157)), (1.0, (168, 188, 198))],
        clouds={"count": 3, "y": (0.20, 0.58), "scale": 0.32, "color": (128, 151, 163), "alpha": 96, "blur": 0.03, "seed": 61},
        haze=[{"y": 0.62, "height": 0.21, "color": (201, 215, 218), "alpha": 28, "blur": 0.035}],
        precipitation={"kind": "rain", "count": 34, "length": (0.025, 0.055), "alpha": 62, "color": (221, 239, 246), "seed": 61},
        text_shadow=0.46,
    ),
    "drizzle-night": _background_preset(
        [(0.0, (19, 43, 64)), (0.52, (37, 66, 82)), (1.0, (82, 103, 116))],
        clouds={"count": 3, "y": (0.18, 0.62), "scale": 0.33, "color": (48, 71, 84), "alpha": 116, "blur": 0.03, "seed": 62},
        haze=[{"y": 0.64, "height": 0.22, "color": (113, 133, 141), "alpha": 30, "blur": 0.04}],
        precipitation={"kind": "rain", "count": 38, "length": (0.025, 0.06), "alpha": 75, "color": (187, 220, 235), "seed": 62},
        text_shadow=0.57,
    ),
    "rain": _background_preset(
        [(0.0, (42, 61, 85)), (0.52, (76, 99, 119)), (1.0, (132, 151, 164))],
        clouds={"count": 4, "y": (0.14, 0.62), "scale": 0.36, "color": (72, 91, 106), "alpha": 118, "blur": 0.025, "seed": 71},
        precipitation={"kind": "rain", "count": 68, "length": (0.045, 0.11), "alpha": 74, "color": (204, 229, 240), "seed": 71},
        text_shadow=0.5,
    ),
    "rain-night": _background_preset(
        [(0.0, (10, 25, 43)), (0.52, (25, 48, 67)), (1.0, (62, 83, 100))],
        clouds={"count": 4, "y": (0.14, 0.64), "scale": 0.36, "color": (28, 47, 61), "alpha": 136, "blur": 0.025, "seed": 72},
        precipitation={"kind": "rain", "count": 58, "length": (0.035, 0.09), "alpha": 66, "color": (147, 192, 214), "seed": 72},
        text_shadow=0.6,
    ),
    "heavy-rain": _background_preset(
        [(0.0, (28, 47, 73)), (0.52, (52, 77, 101)), (1.0, (104, 130, 148))],
        clouds={"count": 5, "y": (0.10, 0.64), "scale": 0.39, "color": (45, 64, 84), "alpha": 145, "blur": 0.02, "seed": 81},
        precipitation={"kind": "rain", "count": 104, "length": (0.07, 0.16), "alpha": 88, "color": (190, 221, 237), "seed": 81},
        text_shadow=0.57,
    ),
    "heavy-rain-night": _background_preset(
        [(0.0, (7, 16, 30)), (0.52, (17, 35, 53)), (1.0, (52, 73, 91))],
        clouds={"count": 5, "y": (0.10, 0.66), "scale": 0.39, "color": (20, 35, 49), "alpha": 158, "blur": 0.02, "seed": 82},
        precipitation={"kind": "rain", "count": 96, "length": (0.06, 0.14), "alpha": 82, "color": (126, 175, 201), "seed": 82},
        text_shadow=0.64,
    ),
    "freezing-rain": _background_preset(
        [(0.0, (116, 157, 184)), (0.52, (157, 193, 209)), (1.0, (211, 229, 235))],
        glow={"kind": "ice", "x": 0.76, "y": 0.22, "radius": 0.28, "color": (213, 246, 255), "alpha": 54},
        clouds={"count": 3, "y": (0.18, 0.62), "scale": 0.33, "color": (163, 190, 204), "alpha": 88, "blur": 0.03, "seed": 91},
        precipitation={"kind": "sleet", "count": 58, "length": (0.035, 0.08), "alpha": 86, "color": (225, 249, 255), "seed": 91},
        text_shadow=0.43,
    ),
    "freezing-rain-night": _background_preset(
        [(0.0, (23, 52, 77)), (0.52, (47, 82, 103)), (1.0, (105, 135, 150))],
        glow={"kind": "ice", "x": 0.76, "y": 0.23, "radius": 0.25, "color": (175, 230, 248), "alpha": 42},
        clouds={"count": 3, "y": (0.18, 0.64), "scale": 0.34, "color": (48, 73, 90), "alpha": 116, "blur": 0.03, "seed": 92},
        precipitation={"kind": "sleet", "count": 54, "length": (0.03, 0.07), "alpha": 78, "color": (174, 224, 241), "seed": 92},
        text_shadow=0.58,
    ),
    "fog": _background_preset(
        [(0.0, (140, 163, 175)), (0.52, (180, 195, 200)), (1.0, (218, 224, 224))],
        clouds={"count": 3, "y": (0.20, 0.66), "scale": 0.42, "color": (218, 225, 225), "alpha": 78, "blur": 0.06, "seed": 101},
        haze=[
            {"y": 0.28, "height": 0.20, "color": (235, 240, 239), "alpha": 56, "blur": 0.06},
            {"y": 0.68, "height": 0.25, "color": (239, 242, 239), "alpha": 68, "blur": 0.07},
        ],
        text_shadow=0.52,
        text_opacity=0.98,
    ),
    "fog-night": _background_preset(
        [(0.0, (40, 57, 69)), (0.52, (67, 85, 95)), (1.0, (119, 132, 137))],
        clouds={"count": 3, "y": (0.18, 0.68), "scale": 0.42, "color": (129, 143, 148), "alpha": 80, "blur": 0.07, "seed": 102},
        haze=[
            {"y": 0.30, "height": 0.22, "color": (168, 179, 180), "alpha": 38, "blur": 0.07},
            {"y": 0.70, "height": 0.28, "color": (176, 185, 184), "alpha": 48, "blur": 0.08},
        ],
        text_shadow=0.62,
    ),
    "snow": _background_preset(
        [(0.0, (142, 181, 205)), (0.52, (190, 216, 228)), (1.0, (235, 242, 241))],
        glow={"kind": "ice", "x": 0.76, "y": 0.20, "radius": 0.27, "color": (237, 251, 255), "alpha": 66},
        clouds={"count": 2, "y": (0.25, 0.60), "scale": 0.30, "color": (218, 234, 240), "alpha": 58, "blur": 0.035, "seed": 111},
        precipitation={"kind": "snow", "count": 48, "alpha": 105, "color": (250, 255, 255), "seed": 111},
        text_shadow=0.48,
        text_opacity=0.98,
    ),
    "snow-night": _background_preset(
        [(0.0, (32, 61, 87)), (0.52, (59, 95, 119)), (1.0, (130, 153, 165))],
        glow={"kind": "ice", "x": 0.76, "y": 0.21, "radius": 0.23, "color": (191, 231, 248), "alpha": 45},
        clouds={"count": 2, "y": (0.24, 0.62), "scale": 0.31, "color": (101, 130, 148), "alpha": 72, "blur": 0.04, "seed": 112},
        precipitation={"kind": "snow", "count": 42, "alpha": 88, "color": (218, 243, 250), "seed": 112},
        text_shadow=0.59,
    ),
    "thunderstorm": _background_preset(
        [(0.0, (17, 27, 48)), (0.52, (32, 47, 67)), (1.0, (72, 89, 106))],
        clouds={"count": 5, "y": (0.08, 0.68), "scale": 0.40, "color": (36, 49, 67), "alpha": 154, "blur": 0.018, "seed": 121},
        precipitation={"kind": "rain", "count": 84, "length": (0.055, 0.13), "alpha": 76, "color": (158, 195, 216), "seed": 121},
        effects={"lightning": True, "seed": 121},
        text_shadow=0.61,
    ),
    "thunderstorm-night": _background_preset(
        [(0.0, (4, 8, 20)), (0.52, (12, 22, 39)), (1.0, (43, 59, 75))],
        clouds={"count": 5, "y": (0.08, 0.70), "scale": 0.40, "color": (14, 24, 39), "alpha": 170, "blur": 0.018, "seed": 122},
        precipitation={"kind": "rain", "count": 112, "length": (0.06, 0.15), "alpha": 90, "color": (118, 165, 193), "seed": 122},
        effects={"lightning": True, "seed": 122},
        text_shadow=0.7,
    ),
    "default": _background_preset(
        [(0.0, (52, 103, 157)), (0.52, (103, 154, 192)), (1.0, (177, 209, 221))],
        glow={"kind": "sun", "x": 0.77, "y": 0.23, "radius": 0.25, "color": (224, 230, 194), "alpha": 42},
        clouds={"count": 2, "y": (0.30, 0.64), "scale": 0.30, "color": (204, 224, 233), "alpha": 46, "blur": 0.035, "seed": 131},
        text_shadow=0.42,
    ),
}

SKY_GRADIENTS = {name: preset["gradient"] for name, preset in BACKGROUND_PRESETS.items()}


def format_localized_date(language, dt):
    """Return a short localized date string for the given language and datetime.

    Examples:
      en -> "March 25, 2026"
      pt -> "25 de março de 2026"
      fr/de/it/nl/es/id -> "25 mars 2026"
    """
    lang = (language or "").lower()
    # Support full locale codes like en-US or de-DE by normalizing to the short prefix
    short = lang.split("-")[0].split("_")[0]
    months = MONTH_NAMES.get(short, MONTH_NAMES.get("en"))
    raw_month = months[dt.month - 1]

    day = dt.day
    year = dt.year

    # Capitalization rules
    # - English: capitalize month (e.g., March)
    # - French: lowercase month (e.g., mars)
    # - Other languages: use the form provided in MONTH_NAMES
    if short == "en":
        month = raw_month[0].upper() + raw_month[1:]
    elif short == "fr":
        month = raw_month.lower()
    else:
        month = raw_month

    # Formatting rules per language
    if short == "en":
        # Month Day, Year -> March 25, 2026
        return f"{month} {day}, {year}"

    if short in ("fr", "de", "it", "nl", "es", "id"):
        # Day Month Year -> 25 mars 2026 (no commas/connectors)
        return f"{day} {month} {year}"

    if short == "pt":
        # Portuguese: Day de month de Year -> 25 de março de 2026
        return f"{day} de {month} de {year}"

    # Fallback: use English-style month-first formatting
    return f"{month} {day}, {year}"


def get_language_labels(language):
    lang = (language or "").lower()
    # exact key
    if lang in LANGUAGE_LABELS:
        return LANGUAGE_LABELS[lang]
    # try prefix like en-US -> en
    short = lang.split("-")[0].split("_")[0]
    if short in LANGUAGE_LABELS:
        return LANGUAGE_LABELS[short]
    # fallback to English
    return LANGUAGE_LABELS["en"]


def is_valid_title(value):
    if value is None:
        return False

    title = str(value).strip()
    if len(title) < 2:
        return False

    # Require at least one letter/number to avoid titles like "," or "'".
    return bool(re.search(r"\w", title, flags=re.UNICODE))


def is_supported_title(value):
    if not is_valid_title(value):
        return False

    title = str(value).strip()
    has_letter = False

    for char in title:
        if not char.isalpha():
            continue

        has_letter = True
        if "LATIN" not in unicodedata.name(char, ""):
            return False

    return has_letter


class DuoWeather(Weather):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "OpenWeatherMap",
            "expected_key": "OPEN_WEATHER_MAP_SECRET"
        }
        template_params['style_settings'] = False
        return template_params

    def generate_image(self, settings, device_config):
        lat_value = settings.get("latitude")
        long_value = settings.get("longitude")
        if lat_value in (None, "") or long_value in (None, ""):
            raise RuntimeError("Latitude and Longitude are required.")

        # Validate and parse numeric coordinates with clear error messages.
        try:
            lat = float(str(lat_value).strip())
            long = float(str(long_value).strip())
        except (ValueError, TypeError):
            raise RuntimeError("Latitude and Longitude must be valid numeric values.")

        # Range checks: latitude [-90, 90], longitude [-180, 180]
        if not (-90.0 <= lat <= 90.0):
            raise RuntimeError("Latitude must be between -90 and 90.")
        if not (-180.0 <= long <= 180.0):
            raise RuntimeError("Longitude must be between -180 and 180.")

        units = settings.get("units")
        if units not in UNITS:
            raise RuntimeError("Units are required.")

        language = str(settings.get("language", "en")).strip() or "en"
        weather_provider = settings.get("weatherProvider", "OpenMeteo")
        timezone_name = device_config.get_config("timezone", default="America/New_York")
        time_format = device_config.get_config("time_format", default="12h")
        local_tz = pytz.timezone(timezone_name)

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        try:
            template_params, provider_tz, api_key = self._get_template_params(
                weather_provider,
                settings,
                units,
                lat,
                long,
                local_tz,
                time_format,
                device_config,
            )
        except Exception as exc:
            logger.error("%s request failed: %s", weather_provider, exc)
            raise RuntimeError(f"{weather_provider} request failure, please check logs.") from exc

        title = self._resolve_title_with_fallback(settings, weather_provider, lat, long, api_key)

        forecast = template_params.get("forecast", [])
        if not forecast:
            raise RuntimeError("Forecast data unavailable.")

        current_day = forecast[0]
        labels = get_language_labels(language)

        # Use the provider timezone that was returned from _get_template_params.
        # This matches the timezone used to parse the forecast and respects the
        # user's `weatherTimeZone` selection (locationTimeZone vs device timezone).
        now = datetime.datetime.now(provider_tz)
        localized_date = format_localized_date(language, now)

        current_icon = template_params.get("current_day_icon", "")
        condition_key, icon_is_night = self._condition_from_icon(current_icon)
        is_night = template_params.get("current_is_night", icon_is_night)
        if condition_key == "mostly_sunny" and is_night:
            condition_key = "mostly_clear"
        condition_label = labels.get("conditions", {}).get(condition_key, labels.get("conditions", {}).get("cloudy", "Cloudy"))
        sky_theme = self._sky_theme(condition_key, is_night)
        background_preset = BACKGROUND_PRESETS.get(sky_theme, BACKGROUND_PRESETS["default"])
        hourly_points = self._select_hourly_points(
            template_params.get("hourly_forecast", []),
            now,
            time_format=time_format,
            count=HOURLY_POINT_COUNT,
        )

        template_params.update(
            {
                "title": title,
                "current_label": labels["now"],
                "date": localized_date,
                "current_time": self._format_clock(now, time_format),
                "current_condition": condition_label,
                "current_high": current_day["high"],
                "current_low": current_day["low"],
                "high_label": labels.get("high", "H"),
                "low_label": labels.get("low", "L"),
                "hourly_points": hourly_points,
                "sky_theme": sky_theme,
                "sky_background": self._sky_background_data_uri(dimensions, sky_theme),
                "background_text_shadow": background_preset["text_shadow"],
                "background_text_opacity": background_preset["text_opacity"],
                "is_night": is_night,
                "provider_timezone": provider_tz.zone,
                "plugin_settings": settings,
                "show_icons": settings.get("showIcons", "true") not in ("false", False),
                "color_icons": settings.get("colorIcons", "true") in ("true", True),
            }
        )

        image = self.render_image(dimensions, "duo_weather.html", "duo_weather.css", template_params)
        if not image:
            raise RuntimeError("Failed to take screenshot, please check logs.")
        return image

    def _get_template_params(
        self,
        weather_provider,
        settings,
        units,
        lat,
        long,
        local_tz,
        time_format,
        device_config,
    ):
        timezone_selection = settings.get("weatherTimeZone", "locationTimeZone")
        api_key = None

        if weather_provider == "OpenWeatherMap":
            api_key = device_config.load_env_key("OPEN_WEATHER_MAP_SECRET")
            if not api_key:
                raise RuntimeError("Open Weather Map API Key not configured.")

            weather_data = self.get_weather_data(api_key, units, lat, long)
            aqi_data = self.get_air_quality(api_key, lat, long)
            tz = self.parse_timezone(weather_data) if timezone_selection == "locationTimeZone" else local_tz
            template_params = self.parse_weather_data(weather_data, aqi_data, tz, units, time_format, lat)
            current_weather = weather_data.get("current", {}).get("weather", [])
            current_icon = current_weather[0].get("icon", "") if current_weather else ""
            template_params["current_is_night"] = current_icon.endswith("n")
            return template_params, tz, api_key

        if weather_provider == "OpenMeteo":
            weather_data = self.get_open_meteo_data(lat, long, units, 5)
            aqi_data = self.get_open_meteo_air_quality(lat, long)
            tz = self.parse_open_meteo_timezone(weather_data) if timezone_selection == "locationTimeZone" else local_tz
            template_params = self.parse_open_meteo_data(weather_data, aqi_data, tz, units, time_format, lat)
            template_params["current_is_night"] = weather_data.get("current", {}).get("is_day", 1) == 0
            return template_params, tz, api_key

        raise RuntimeError(f"Unknown weather provider: {weather_provider}")

    def _resolve_title(self, settings, weather_provider, lat, long, api_key):
        title_selection = settings.get("titleSelection", "location")
        custom_title = (settings.get("customTitle") or "").strip()

        if title_selection == "custom":
            if not custom_title:
                raise RuntimeError("Custom title is required.")
            return custom_title

        if weather_provider == "OpenWeatherMap":
            return self.get_location(api_key, lat, long)

        return self.get_reverse_geocoded_location(lat, long)

    def _resolve_title_with_fallback(self, settings, weather_provider, lat, long, api_key):
        try:
            title = self._resolve_title(settings, weather_provider, lat, long, api_key)
            if is_supported_title(title):
                return title
        except Exception as exc:
            logger.warning("Duo Weather title resolution failed, using fallback: %s", exc)

        quick_location = (settings.get("quickLocation") or "").strip()
        quick_location_label = QUICK_LOCATION_LABELS.get(quick_location)
        if quick_location_label:
            return quick_location_label

        matched_city = self._match_quick_location_by_coordinates(lat, long)
        if matched_city:
            return matched_city

        return self.format_coordinates(lat, long)

    def _match_quick_location_by_coordinates(self, lat, long, tolerance=0.02):
        for city, (city_lat, city_long) in QUICK_LOCATION_COORDS.items():
            if abs(lat - city_lat) <= tolerance and abs(long - city_long) <= tolerance:
                return city
        return None

    def parse_open_meteo_timezone(self, weather_data):
        timezone_name = weather_data.get("timezone")
        if not timezone_name:
            raise RuntimeError("Timezone not found in weather data.")

        logger.info("Using timezone from Open-Meteo data: %s", timezone_name)
        return pytz.timezone(timezone_name)

    def get_reverse_geocoded_location(self, lat, long):
        # Use rounded coordinates as cache key to avoid tiny float differences
        key = (round(float(lat), REVERSE_GEOCODE_ROUND_DECIMALS), round(float(long), REVERSE_GEOCODE_ROUND_DECIMALS))

        now_ts = datetime.datetime.now().timestamp()
        cached = REVERSE_GEOCODE_CACHE.get(key)
        if cached:
            age = now_ts - cached.get("ts", 0)
            if cached.get("title") and age < REVERSE_GEOCODE_SUCCESS_TTL:
                return cached["title"]
            if cached.get("failed") and age < REVERSE_GEOCODE_FAIL_TTL:
                # recent failure — avoid retrying too quickly
                return self.format_coordinates(lat, long)

        headers = {"User-Agent": "InkyPi Duo Weather/1.0 (+https://github.com/inkypi)"}
        try:
            response = requests.get(
                REVERSE_GEOCODE_URL.format(lat=lat, long=long),
                headers=headers,
                timeout=30,
            )
        except Exception as exc:
            logger.warning("Reverse geocode request failed: %s", exc)
            # store a failed marker to avoid hammering the service
            REVERSE_GEOCODE_CACHE[key] = {"failed": True, "ts": now_ts}
            return self.format_coordinates(lat, long)

        if not 200 <= response.status_code < 300:
            logger.warning("Failed to reverse geocode location: %s", response.content)
            REVERSE_GEOCODE_CACHE[key] = {"failed": True, "ts": now_ts}
            return self.format_coordinates(lat, long)

        try:
            location_data = response.json()
        except Exception as exc:
            logger.warning("Invalid JSON from reverse geocode: %s", exc)
            REVERSE_GEOCODE_CACHE[key] = {"failed": True, "ts": now_ts}
            return self.format_coordinates(lat, long)

        address = location_data.get("address", {})

        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("municipality")
            or address.get("county")
        )
        region = address.get("state") or address.get("country")

        if city and region:
            title = f"{city}, {region}"
        elif city:
            title = city
        elif region:
            title = region
        else:
            display_name = location_data.get("display_name", "")
            if display_name:
                title = ", ".join(display_name.split(", ")[:2])
            else:
                title = self.format_coordinates(lat, long)

        # Cache successful result
        REVERSE_GEOCODE_CACHE[key] = {"title": title, "ts": now_ts}
        return title

    def format_coordinates(self, lat, long):
        return f"{lat:.2f}, {long:.2f}"

    def _condition_from_icon(self, icon_path):
        icon_name = os.path.splitext(os.path.basename(icon_path or ""))[0].lower()
        is_night = icon_name.endswith("n")
        condition_key = ICON_CONDITION_KEYS.get(icon_name, "cloudy")
        return condition_key, is_night

    def _sky_theme(self, condition_key, is_night):
        sky = SKY_BY_CONDITION.get(condition_key, "default")
        if sky == "mostly-sunny" and is_night:
            sky = "mostly-clear"
        if is_night:
            candidate = f"{sky}-night"
        elif sky == "mostly-clear":
            candidate = "mostly-sunny"
        else:
            candidate = sky
        return candidate if candidate in BACKGROUND_PRESETS else "default"

    def _sky_background_data_uri(self, dimensions, sky_theme):
        """Render a local, deterministic atmospheric background as a data URI."""
        width, height = max(1, int(dimensions[0])), max(1, int(dimensions[1]))
        preset = BACKGROUND_PRESETS.get(sky_theme, BACKGROUND_PRESETS["default"])
        image = self._gradient_background(width, height, preset["gradient"])
        self._draw_glow(image, preset.get("glow"))
        self._draw_clouds(image, preset.get("clouds"))
        self._draw_haze(image, preset.get("haze"))
        self._draw_precipitation(image, preset.get("precipitation"))
        self._draw_effects(image, preset.get("effects"))

        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def _gradient_background(self, width, height, stops):
        image = Image.new("RGBA", (width, height))
        draw = ImageDraw.Draw(image)
        for y in range(height):
            t = y / max(height - 1, 1)
            color = self._interpolate_gradient(stops, t) + (255,)
            draw.line([(0, y), (width, y)], fill=color)
        return image

    def _draw_glow(self, image, glow):
        if not glow:
            return

        width, height = image.size
        radius = min(width, height) * glow.get("radius", 0.25)
        center_x = width * glow.get("x", 0.75)
        center_y = height * glow.get("y", 0.22)
        color = tuple(glow.get("color", (255, 230, 160)))
        alpha = glow.get("alpha", 70)

        for scale, opacity, blur_scale in ((1.0, 0.28, 0.22), (0.72, 0.38, 0.13), (0.42, 0.62, 0.06)):
            layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(layer)
            current_radius = radius * scale
            box = (
                center_x - current_radius,
                center_y - current_radius,
                center_x + current_radius,
                center_y + current_radius,
            )
            draw.ellipse(box, fill=color + (int(alpha * opacity),))
            layer = layer.filter(ImageFilter.GaussianBlur(max(1, radius * blur_scale)))
            image.alpha_composite(layer)

    def _draw_clouds(self, image, clouds):
        if not clouds:
            return

        width, height = image.size
        minimum_dimension = min(width, height)
        rng = random.Random(clouds.get("seed", 0))
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        color = tuple(clouds.get("color", (220, 232, 238)))
        alpha = int(clouds.get("alpha", 70))
        y_min, y_max = clouds.get("y", (0.2, 0.7))
        masses = clouds.get("masses")
        cloud_specs = masses if masses else [None] * clouds.get("count", 1)

        for mass in cloud_specs:
            if mass:
                cloud_width = (
                    minimum_dimension
                    * mass.get("scale", clouds.get("scale", 0.3))
                    * mass.get("stretch", 1.0)
                    * rng.uniform(0.94, 1.06)
                )
                height_ratio = mass.get("height", rng.uniform(0.32, 0.52))
                cloud_height = cloud_width * height_ratio * rng.uniform(0.94, 1.06)
                center_x = width * mass.get("x", rng.uniform(-0.08, 1.08))
                center_y = height * mass.get("y", rng.uniform(y_min, y_max))
                mass_alpha = int(mass.get("alpha", alpha))
            else:
                cloud_width = minimum_dimension * clouds.get("scale", 0.3) * rng.uniform(0.82, 1.24)
                cloud_height = cloud_width * rng.uniform(0.32, 0.52)
                center_x = width * rng.uniform(-0.08, 1.08)
                center_y = height * rng.uniform(y_min, y_max)
                mass_alpha = alpha

            fill = color + (mass_alpha,)
            base_box = (
                center_x - cloud_width * 0.52,
                center_y - cloud_height * 0.05,
                center_x + cloud_width * 0.52,
                center_y + cloud_height * 0.43,
            )
            draw.ellipse(base_box, fill=fill)

            if mass:
                for puff in mass.get("puffs", []):
                    puff_width = cloud_width * puff.get("width", 0.3)
                    puff_height = cloud_height * puff.get("height", 0.9)
                    puff_x = center_x + cloud_width * puff.get("offset", 0)
                    puff_y = center_y - cloud_height * puff.get("rise", 0.18)
                    puff_box = (
                        puff_x - puff_width / 2,
                        puff_y - puff_height / 2,
                        puff_x + puff_width / 2,
                        puff_y + puff_height / 2,
                    )
                    draw.ellipse(puff_box, fill=fill)
            else:
                for offset in (-0.33, -0.12, 0.12, 0.33):
                    puff_width = cloud_width * rng.uniform(0.25, 0.38)
                    puff_height = cloud_height * rng.uniform(0.72, 1.22)
                    puff_x = center_x + cloud_width * offset + rng.uniform(-0.025, 0.025) * cloud_width
                    puff_y = center_y - cloud_height * rng.uniform(0.12, 0.28)
                    puff_box = (
                        puff_x - puff_width / 2,
                        puff_y - puff_height / 2,
                        puff_x + puff_width / 2,
                        puff_y + puff_height / 2,
                    )
                    draw.ellipse(puff_box, fill=fill)

        blur = minimum_dimension * clouds.get("blur", 0.03)
        if blur > 0:
            layer = layer.filter(ImageFilter.GaussianBlur(max(1, blur)))
        image.alpha_composite(layer)

    def _draw_haze(self, image, haze):
        if not haze:
            return

        width, height = image.size
        for band in haze:
            layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(layer)
            band_height = height * band.get("height", 0.2)
            center_y = height * band.get("y", 0.6)
            color = tuple(band.get("color", (235, 240, 240)))
            alpha = int(band.get("alpha", 40))
            draw.rectangle(
                (0, center_y - band_height / 2, width, center_y + band_height / 2),
                fill=color + (alpha,),
            )
            blur = min(width, height) * band.get("blur", 0.05)
            layer = layer.filter(ImageFilter.GaussianBlur(max(1, blur)))
            image.alpha_composite(layer)

    def _draw_precipitation(self, image, precipitation):
        if not precipitation:
            return

        width, height = image.size
        minimum_dimension = min(width, height)
        rng = random.Random(precipitation.get("seed", 0))
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        kind = precipitation.get("kind", "rain")
        color = tuple(precipitation.get("color", (220, 240, 248)))
        alpha = int(precipitation.get("alpha", 70))
        count = precipitation.get("count", 40)

        if kind in ("rain", "sleet"):
            length_min, length_max = precipitation.get("length", (0.04, 0.1))
            line_width = max(1, round(minimum_dimension * 0.002))
            for _ in range(count):
                start_x = rng.uniform(-0.08, 1.08) * width
                start_y = rng.uniform(-0.1, 1.0) * height
                length = rng.uniform(length_min, length_max) * height
                drift = rng.uniform(-0.012, 0.012) * width
                draw.line(
                    (start_x, start_y, start_x + drift, start_y + length),
                    fill=color + (alpha,),
                    width=line_width,
                )
                if kind == "sleet":
                    dot_radius = max(1, line_width * 0.8)
                    draw.ellipse(
                        (
                            start_x - dot_radius,
                            start_y + length - dot_radius,
                            start_x + dot_radius,
                            start_y + length + dot_radius,
                        ),
                        fill=color + (alpha,),
                    )
        elif kind == "snow":
            for _ in range(count):
                center_x = rng.uniform(-0.04, 1.04) * width
                center_y = rng.uniform(-0.04, 1.04) * height
                radius = rng.uniform(0.002, 0.007) * minimum_dimension
                draw.ellipse(
                    (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
                    fill=color + (alpha,),
                )

        image.alpha_composite(layer)

    def _draw_effects(self, image, effects):
        if not effects:
            return

        width, height = image.size
        rng = random.Random(effects.get("seed", 0))
        stars = effects.get("stars", 0)
        if stars:
            layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(layer)
            for _ in range(stars):
                center_x = rng.uniform(0.04, 0.96) * width
                center_y = rng.uniform(0.05, 0.55) * height
                radius = rng.uniform(0.5, 1.5)
                alpha = rng.randint(45, 105)
                draw.ellipse(
                    (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
                    fill=(225, 240, 255, alpha),
                )
            image.alpha_composite(layer)

        if effects.get("lightning"):
            center_x = width * rng.uniform(0.58, 0.78)
            start_y = height * rng.uniform(0.12, 0.22)
            points = [(center_x, start_y)]
            for index in range(5):
                center_x += width * rng.uniform(-0.045, 0.045)
                start_y += height * rng.uniform(0.055, 0.09)
                points.append((center_x, start_y))

            glow_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            glow_draw = ImageDraw.Draw(glow_layer)
            glow_draw.line(points, fill=(214, 235, 255, 58), width=max(4, round(min(width, height) * 0.018)))
            glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(max(2, min(width, height) * 0.025)))
            image.alpha_composite(glow_layer)

            crisp_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            crisp_draw = ImageDraw.Draw(crisp_layer)
            crisp_draw.line(points, fill=(230, 244, 255, 118), width=max(1, round(min(width, height) * 0.004)))
            image.alpha_composite(crisp_layer)

    def _interpolate_gradient(self, stops, t):
        for (pos_a, color_a), (pos_b, color_b) in zip(stops, stops[1:]):
            if pos_a <= t <= pos_b:
                span = pos_b - pos_a
                local_t = (t - pos_a) / span if span else 0
                return tuple(
                    round(color_a[i] + (color_b[i] - color_a[i]) * local_t)
                    for i in range(3)
                )
        return stops[-1][1]

    def _format_clock(self, dt, time_format):
        if time_format == "24h":
            return dt.strftime("%H:%M")
        return dt.strftime("%I:%M").lstrip("0") or "0:00"

    def _compact_hour_label(self, time_label):
        label = str(time_label or "").strip()
        if not label:
            return label
        compact = label.replace(" ", "")
        compact = compact.replace(".M.", "M").replace("a.m.", "AM").replace("p.m.", "PM")
        compact = compact.replace("A.M.", "AM").replace("P.M.", "PM")
        compact = compact.replace("am", "AM").replace("pm", "PM")
        return compact

    def _split_hour_label(self, compact_label):
        """Split an hour label so its suffix can be rendered smaller."""
        match = re.match(r"^(-?\d{1,2}(?::\d{2})?)([AP]M|h)?$", compact_label or "")
        if not match:
            return compact_label or "", ""
        value, suffix = match.groups()
        return value, suffix or ""

    def _format_hourly_label(self, time_label, time_format):
        if time_format != "24h":
            return self._compact_hour_label(time_label)

        match = re.match(r"^(\d{1,2})(?::\d{2})?$", str(time_label or "").strip())
        if match:
            return f"{int(match.group(1))}h"
        return str(time_label or "").strip()

    def _select_hourly_points(self, hourly_forecast, now, time_format="12h", count=HOURLY_POINT_COUNT):
        # Provider parsers already start hourly data at the current hour.
        # Prefer remaining hours of the same day, then sample ~2-hour steps.
        if not hourly_forecast:
            return []

        points = []
        for hour in hourly_forecast:
            point = dict(hour)
            compact_label = self._format_hourly_label(point.get("time"), time_format)
            point["time"] = compact_label
            point["hour_value"], point["hour_suffix"] = self._split_hour_label(compact_label)
            try:
                point["temperature"] = int(round(float(point.get("temperature", 0))))
            except (TypeError, ValueError):
                point["temperature"] = 0
            points.append(point)

        remaining_today = max(1, 24 - int(getattr(now, "hour", 0)))
        today_points = points[: min(remaining_today, len(points))]
        source = today_points if len(today_points) >= count else points

        if len(source) <= count:
            return source

        stepped = source[::HOURLY_STEP_HOURS]
        if len(stepped) >= count:
            return stepped[:count]
        return source[:count]
