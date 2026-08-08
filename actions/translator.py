"""
MARK XLIX - Translator

Fetches REAL translations from the MyMemory public API (free, no API key
required) and returns a spoken-friendly result.

If the network is unavailable it falls back to a small built-in phrasebook
(clearly labelled as approximate).

API used:
  - https://api.mymemory.translated.net/get?q=hello&langpair=en|ja
"""

import requests

API_URL = "https://api.mymemory.translated.net/get"
_TIMEOUT = 10.0

# language name/code -> ISO 639-1 code
_LANGUAGES = {
    "english": "en", "en": "en",
    "spanish": "es", "es": "es", "espanol": "es",
    "french": "fr", "fr": "fr", "francais": "fr",
    "german": "de", "de": "de", "deutsch": "de",
    "italian": "it", "it": "it", "italiano": "it",
    "portuguese": "pt", "pt": "pt", "portugues": "pt",
    "japanese": "ja", "ja": "ja", "nihongo": "ja",
    "chinese": "zh", "zh": "zh", "mandarin": "zh",
    "korean": "ko", "ko": "ko",
    "russian": "ru", "ru": "ru",
    "arabic": "ar", "ar": "ar",
    "turkish": "tr", "tr": "tr", "turkce": "tr",
    "dutch": "nl", "nl": "nl",
    "swedish": "sv", "sv": "sv",
    "polish": "pl", "pl": "pl",
    "hindi": "hi", "hi": "hi",
    "greek": "el", "el": "el",
    "hebrew": "he", "he": "he",
    "thai": "th", "th": "th",
    "vietnamese": "vi", "vi": "vi",
    "indonesian": "id", "id": "id",
    "ukrainian": "uk", "uk": "uk",
}

# tiny built-in phrasebook for the offline fallback (source: english)
_PHRASES = {
    ("hello", "es"): "hola", ("hello", "fr"): "bonjour",
    ("hello", "de"): "hallo", ("hello", "it"): "ciao",
    ("hello", "ja"): "konnichiwa", ("hello", "ko"): "annyeonghaseyo",
    ("hello", "zh"): "ni hao", ("hello", "hi"): "namaste",
    ("hello", "tr"): "merhaba", ("hello", "ru"): "privet",
    ("hello", "pt"): "ola", ("hello", "nl"): "hallo",
    ("hello", "ar"): "marhabaan", ("hello", "th"): "sawatdee",
    ("hello", "vi"): "xin chao", ("hello", "id"): "halo",
    ("hello", "pl"): "czesc", ("hello", "sv"): "hej",
    ("hello", "el"): "yia sas", ("hello", "he"): "shalom",
    ("hello", "uk"): "pryvit",
    ("thank you", "es"): "gracias", ("thank you", "fr"): "merci",
    ("thank you", "de"): "danke", ("thank you", "it"): "grazie",
    ("thank you", "ja"): "arigato", ("thank you", "ko"): "gamsahamnida",
    ("thank you", "zh"): "xie xie", ("thank you", "hi"): "dhanyavaad",
    ("thank you", "tr"): "tesekkurler", ("thank you", "ru"): "spasibo",
    ("thank you", "pt"): "obrigado", ("thank you", "nl"): "dank je",
    ("thank you", "ar"): "shukran", ("thank you", "th"): "khob khun",
    ("thank you", "vi"): "cam on", ("thank you", "id"): "terima kasih",
    ("goodbye", "es"): "adios", ("goodbye", "fr"): "au revoir",
    ("goodbye", "de"): "auf wiedersehen", ("goodbye", "it"): "arrivederci",
    ("goodbye", "ja"): "sayonara", ("goodbye", "ko"): "annyeong",
    ("goodbye", "zh"): "zai jian", ("goodbye", "tr"): "hosca kalin",
    ("goodbye", "ru"): "do svidaniya", ("goodbye", "pt"): "tchau",
    ("goodbye", "nl"): "tot ziens", ("goodbye", "ar"): "wadaean",
    ("good morning", "es"): "buenos dias", ("good morning", "fr"): "bonjour",
    ("good morning", "de"): "guten morgen", ("good morning", "ja"): "ohayo",
    ("good morning", "tr"): "gunaydin", ("good morning", "ru"): "dobroye utro",
    ("good morning", "pt"): "bom dia", ("good morning", "it"): "buongiorno",
}

_NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "ja": "Japanese", "zh": "Chinese",
    "ko": "Korean", "ru": "Russian", "ar": "Arabic", "tr": "Turkish",
    "nl": "Dutch", "sv": "Swedish", "pl": "Polish", "hi": "Hindi",
    "el": "Greek", "he": "Hebrew", "th": "Thai", "vi": "Vietnamese",
    "id": "Indonesian", "uk": "Ukrainian",
}


def _resolve_lang(token: str):
    t = (token or "").strip().lower()
    return _LANGUAGES.get(t)


def _translate_live(text: str, source: str, target: str) -> str:
    resp = requests.get(
        API_URL,
        params={
            "q": text,
            "langpair": f"{source}|{target}",
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    translated = data.get("responseData", {}).get("translatedText", "")
    if not translated or translated == text:
        raise ValueError("empty translation")
    return translated


def _phrasebook(text: str, target: str):
    key = (text or "").strip().lower()
    if key in _PHRASES:
        return _PHRASES[(key, target)]
    for phrase in ("hello", "thank you", "goodbye", "good morning"):
        if phrase in key:
            return _PHRASES.get((phrase, target))
    return None


def translate_text(parameters=None, response=None, player=None,
                   session_memory=None) -> str:
    params = parameters or {}
    text   = (params.get("text") or params.get("query") or "").strip()
    target = _resolve_lang(params.get("to") or params.get("target") or "japanese")
    source = _resolve_lang(params.get("from") or params.get("source")) or "en"

    if not text:
        return "Tell me what to translate and into which language."
    if not target:
        return (
            "I don't recognise that target language. Try one of: "
            "Spanish, French, German, Japanese, Chinese, Korean, Turkish, "
            "Russian, Arabic, Hindi, Italian, Portuguese, and more."
        )
    if source == target:
        return f"That is already {_NAMES.get(target, target)}: {text}."

    try:
        translated = _translate_live(text, source, target)
        return f"In {_NAMES.get(target, target)}, that is: {translated}"
    except Exception as e:
        print(f"[Translator] live fetch failed: {e}")
        fallback = _phrasebook(text, target)
        if fallback:
            return (
                f"In {_NAMES.get(target, target)}, that is approximately: {fallback} "
                "(offline phrasebook - no internet)."
            )
        return (
            f"I couldn't translate that right now - no internet connection. "
            f"Supported languages: {', '.join(sorted(set(_NAMES.values())))}."
        )
