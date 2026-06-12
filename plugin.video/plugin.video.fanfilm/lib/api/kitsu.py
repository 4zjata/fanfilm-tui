# -*- coding: utf-8 -*-
"""
FanFilm ‑ moduł: kitsu
Mapowanie TMDB ID → cour structure + Kitsu API.

Mapowanie ID anime (TMDB → Kitsu/MAL/AniList) pobierane jest z serwera ARM,
a gdy ten jest niedostępny — ze statycznych list Fribb (anime-lists) jako
fallback. Po powrocie ARM jest znów używany (przy następnej sesji).

Copyright (C) 2025 :)
Dystrybuowane na licencji MIT <https://mit-license.org>
many thx for idea and code to skoruppa: https://github.com/skoruppa/
"""

from datetime import date, datetime
import json
import re
from typing import Dict, List, Optional, Tuple, TypedDict

from lib.ff import cache, control, requests
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.source_utils import DEFAULT_UA

# Mapowanie macron vowels (Hepburn romanization) → double vowels
_MACRON_MAP = str.maketrans('āīūēō', 'aiueo')


# ---------------------------------------------------------------------------
# Typy publiczne
# ---------------------------------------------------------------------------

class CourInfo(TypedDict):
    """Jeden cour (sezon Kitsu) — element listy zwracanej przez get_cour_structure."""
    kitsu_id:   int
    mal_id:     Optional[int]   # MyAnimeList ID (do docchi)
    anilist_id: Optional[int]   # AniList ID (do animerealms)
    title:      str             # en_jp — do wyszukiwania na animezone/shinden
    ep_count:   int             # liczba odcinków w tym cour
    start_date: Optional[date]
    end_date:   Optional[date]
    subtype:    str             # 'TV' / 'OVA' / 'movie' / ...


class MovieInfo(TypedDict):
    """Info o filmie anime — zwracane przez get_movie_info."""
    title:      str
    mal_id:     Optional[int]
    anilist_id: Optional[int]


# ---------------------------------------------------------------------------
# Stałe i cache
# ---------------------------------------------------------------------------

ARM_URL   = "https://arm.haglund.dev/api/v2/themoviedb?id=%s&include=kitsu,myanimelist,anilist"
FRIBB_URL = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-mini.json"
KITSU_URL = "https://kitsu.io/api/edge/anime/%s"

#: Nagłówki HTTP — przeglądarkowy User-Agent (część serwerów odrzuca inne).
_HEADERS = {'User-Agent': DEFAULT_UA}
#: Ważność trwałego cache pobranego indeksu Fribb (godziny).
_FRIBB_TTL_H = 24 * 7

# Cache w pamięci (żyje przez czas procesu/sesji)
_ids_memo:    Dict[str, Optional[List[Tuple[int, Optional[int], Optional[int]]]]] = {}
_fribb_index: Optional[Dict[str, List]] = None
_arm_down:    bool = False  # ARM zawiódł w tej sesji → pomiń go, używaj fallbacku
_kitsu_cache: Dict[int, '_KitsuMeta'] = {}


# ---------------------------------------------------------------------------
# API publiczne
# ---------------------------------------------------------------------------

_RE_SEASON_SUFFIX = re.compile(
    r'\s+(?:(\d+)(?:st|nd|rd|th)\s+Season|Season\s+(\d+))$', re.IGNORECASE
)


def strip_season_suffix(title: str) -> Tuple[str, Optional[int]]:
    """Zwraca (base_title, season_number) po odcięciu angielskiego sufiksu sezonu.

    Przykłady: "Frieren 2nd Season" → ("Frieren", 2)
               "Attack on Titan Season 4" → ("Attack on Titan", 4)
               "Frieren" → ("Frieren", None)
    """
    m = _RE_SEASON_SUFFIX.search(title)
    if m:
        num = int(m.group(1) or m.group(2))
        return title[:m.start()].strip(), num
    return title, None


def normalize_romaji(text: str) -> str:
    """Normalizuje tytuł romaji — macron vowels → double vowels, lowercase, strip interpunkcji."""
    # ā→aa, ī→ii, ū→uu, ē→ee, ō→oo
    result = []
    for ch in text.lower():
        base = ch.translate(_MACRON_MAP)
        if base != ch:
            result.append(base * 2)  # ū → uu
        else:
            result.append(ch)
    text = ''.join(result)
    text = re.sub(r'[^\w\s]', ' ', text)
    # Normalizuj warianty numeracji sezonów: "2nd season" → "season 2", "3rd season" → "season 3"
    text = re.sub(r'(\d+)(?:st|nd|rd|th)\s+season', r'season \1', text)
    return re.sub(r'\s+', ' ', text).strip()


def get_cour_structure(tmdb_id: Optional[str]) -> Optional[List[CourInfo]]:
    """
    Zwraca listę WSZYSTKICH courów dla danego serialu (wszystkie sezony TMDB łącznie),
    posortowanych chronologicznie. Filmy i speciale (subtype=movie/special) są pomijane.

    Zwraca None jeśli ARM/Fribb/Kitsu niedostępne lub brak danych.

    UWAGA: scraper używa abs_ep względnego do sezonu TMDB
    (ffitem.absolute_episode_number() liczy od 1 per season),
    więc musi przekazać tylko cours należące do danego sezonu.
    Nie potrafimy tego rozróżnić bez TMDB API — scraperowi zwracamy
    WSZYSTKIE cours i pozwalamy mu działać metodą cumulative fallback
    (patrz map_episode_in_season).
    """
    if not tmdb_id:
        return None

    kitsu_entries = _get_kitsu_ids(str(tmdb_id), 'tv')
    if not kitsu_entries:
        return None

    cours: List[CourInfo] = []
    for kid, mal_id, anilist_id in kitsu_entries:
        meta = _get_kitsu_meta(kid)
        if not meta:
            continue
        if meta['subtype'].lower() in ('movie', 'special'):
            fflog(f'kitsu: pominięto kitsu_id={kid} ({meta["subtype"]}): {meta["title"]}')
            continue
        cours.append(CourInfo(**meta, mal_id=mal_id, anilist_id=anilist_id))

    if not cours:
        return None

    cours.sort(key=lambda x: x['start_date'] or date.max)

    fflog(f'kitsu: {len(cours)} cours dla tmdb={tmdb_id}')
    for c in cours:
        fflog(f'  kitsu_id={c["kitsu_id"]} ep={c["ep_count"]} start={c["start_date"]} end={c["end_date"]} "{c["title"]}"')

    return cours


def find_cour_by_date(cours: List[CourInfo], episode_date: Optional[date]) -> Tuple[Optional[CourInfo], int]:
    """
    Dopasowuje cour na podstawie daty odcinka/sezonu.
    Szuka coura, którego zakres dat (start_date..end_date) obejmuje episode_date.
    Zwraca (cour, offset) lub (None, 0).
    offset = suma ep_count courów PRZED znalezionym (do obliczenia local_ep).
    """
    if not cours or not episode_date:
        return None, 0

    offset = 0
    for i, cour in enumerate(cours):
        start = cour['start_date']
        end   = cour['end_date']
        if not start:
            offset += cour['ep_count'] or 0
            continue

        # Sprawdź czy data mieści się w zakresie tego coura
        if end and start <= episode_date <= end:
            fflog(f'kitsu: find_cour_by_date: date={episode_date} pasuje do cour "{cour["title"]}" ({start}..{end})')
            return cour, offset

        # Jeśli nie ma end_date, sprawdź czy data >= start i < start następnego coura
        if not end and episode_date >= start:
            next_start = cours[i + 1]['start_date'] if i + 1 < len(cours) else None
            if not next_start or episode_date < next_start:
                fflog(f'kitsu: find_cour_by_date: date={episode_date} pasuje do cour "{cour["title"]}" (start={start}, no end)')
                return cour, offset

        offset += cour['ep_count'] or 0

    fflog(f'kitsu: find_cour_by_date: date={episode_date} nie pasuje do żadnego coura')
    return None, 0


def get_movie_info(tmdb_id: str) -> Optional[MovieInfo]:
    """
    Zwraca info o filmie anime z Kitsu: MovieInfo lub None.
    Używane przez movie() w scraperach — nie filtruje subtype=movie.
    """
    if not tmdb_id:
        return None

    entries = _get_kitsu_ids(str(tmdb_id), 'movie')
    if not entries:
        return None

    for kid, mal_id, anilist_id in entries:
        meta = _get_kitsu_meta(kid)
        if not meta:
            continue
        if meta['subtype'].lower() == 'movie':
            fflog(f'kitsu/movie: tmdb={tmdb_id} → "{meta["title"]}" mal_id={mal_id} anilist_id={anilist_id}')
            return MovieInfo(title=meta['title'], mal_id=mal_id, anilist_id=anilist_id)

    return None


def resolve_cour(cours: List[CourInfo], ffitem) -> Tuple[Optional[CourInfo], Optional[int]]:
    """
    Na podstawie ffitem (abs_ep + data odcinka) wybiera właściwy cour i oblicza lokalny numer odcinka.
    Zwraca (cour, local_ep) lub (None, None).
    """
    abs_ep = ffitem.absolute_episode_number()
    if not abs_ep:
        return None, None

    ep_date = ffitem.date
    if not ep_date and ffitem.season_item:
        ep_date = ffitem.season_item.date

    cour: Optional[CourInfo] = None
    local_ep: Optional[int] = None

    if ep_date:
        cour, offset = find_cour_by_date(cours, ep_date)
        if cour:
            # Gdy TMDB sezon startuje >= start Kitsu coura (tzn. cour należy do tego sezonu),
            # używamy tmdb_ep zamiast abs_ep, żeby uniknąć rozbieżności ep_count między TMDB a Kitsu
            # (np. OPM: Kitsu S1=13 odcinków, TMDB S01=12 → abs_ep byłby o 1 za duży).
            # Gdy season_start > cour.start (np. DBZ Kai: kilka sezonów TMDB = 1 Kitsu cour),
            # wracamy do podejścia abs_ep - offset.
            tmdb_ep = ffitem.episode
            season_start = ffitem.season_item.date if ffitem.season_item else None
            is_continuous = ffitem.show_item.continuous_episode_number if ffitem.show_item else False
            if (not is_continuous and tmdb_ep and season_start and cour['start_date']
                    and season_start <= cour['start_date']):
                season_offset = sum(
                    c['ep_count'] for c in cours
                    if c['start_date'] and c['ep_count']
                    and season_start <= c['start_date'] < cour['start_date']
                )
                local_ep = tmdb_ep - season_offset
                fflog(f'kitsu: resolve_cour: date-based (tmdb_ep={tmdb_ep}, season_start={season_start}, season_offset={season_offset}, local_ep={local_ep}, ep_count={cour["ep_count"]})')
            else:
                local_ep = abs_ep - offset
                fflog(f'kitsu: resolve_cour: date-based (abs_ep={abs_ep}, offset={offset}, local_ep={local_ep}, ep_count={cour["ep_count"]})')
            if local_ep < 1 or (cour['ep_count'] and local_ep > cour['ep_count']):
                fflog(f'kitsu: resolve_cour: local_ep out of bounds, falling back to map_episode')
                cour = None

    if not cour:
        fflog(f'kitsu: resolve_cour: using map_episode fallback with abs_ep={abs_ep}')
        cour, local_ep = map_episode(cours, abs_ep)

    return cour, local_ep


def map_episode(cours: List[CourInfo], abs_ep: int) -> Tuple[Optional[CourInfo], Optional[int]]:
    """
    Mapuje absolutny numer odcinka na (cour, local_ep_number).

    abs_ep: liczony od 1 w ramach listy cours którą przekazujesz.
            Scraper odpowiada za przekazanie właściwego podzbioru courów
            (np. tylko courów danego TMDB season) lub pełnej listy
            z właściwym abs_ep liczonym globalnie.

    Zwraca (None, None) jeśli abs_ep wykracza poza sumę ep_count.
    """
    cumulative = 0
    for cour in cours:
        ep_count = cour['ep_count'] or 0
        if cumulative < abs_ep <= cumulative + ep_count:
            return cour, abs_ep - cumulative
        cumulative += ep_count

    fflog(f'kitsu: map_episode abs_ep={abs_ep} wykracza poza {cumulative} odcinków łącznie')
    return None, None


# ---------------------------------------------------------------------------
# Wewnętrzne helpery
# ---------------------------------------------------------------------------

class _KitsuMeta(TypedDict):
    kitsu_id:   int
    title:      str
    ep_count:   int
    start_date: Optional[date]
    end_date:   Optional[date]
    subtype:    str


def _get_kitsu_ids(tmdb_id: str, media_type: str
                   ) -> Optional[List[Tuple[int, Optional[int], Optional[int]]]]:
    """Zwraca listę (kitsu_id, mal_id, anilist_id) dla TMDB ID danego typu.

    Najpierw próbuje serwera ARM, a gdy ten zawiedzie — list Fribb (fallback).
    media_type: 'tv' lub 'movie' (przestrzenie ID TMDB są rozłączne).
    """
    memo_key = f'{media_type}:{tmdb_id}'
    if memo_key in _ids_memo:
        return _ids_memo[memo_key]

    ids = _ids_from_arm(tmdb_id)
    if ids is None:
        ids = _ids_from_fribb(tmdb_id, media_type)
    _ids_memo[memo_key] = ids
    return ids


def _ids_from_arm(tmdb_id: str
                  ) -> Optional[List[Tuple[int, Optional[int], Optional[int]]]]:
    """Mapowanie z serwera ARM. None gdy ARM niedostępny lub brak danych."""
    global _arm_down
    if _arm_down:
        return None
    try:
        resp = requests.get(ARM_URL % tmdb_id, headers=_HEADERS, timeout=8)
        if not resp or resp.status_code != 200:
            fflog(f'kitsu/ARM: status={getattr(resp, "status_code", "?")} '
                  f'— przełączam na fallback (Fribb)')
            _arm_down = True
            return None
        # ARM zwraca listę: [{kitsu: 123, myanimelist: 456, anilist: 789}, ...]
        ids = [(e['kitsu'], e.get('myanimelist'), e.get('anilist'))
               for e in resp.json() if e.get('kitsu')]
        if ids:
            fflog(f'kitsu/ARM: tmdb={tmdb_id} → {ids}')
            return ids
        return None  # ARM zdrowy, ale brak wpisu — spróbuj jeszcze Fribb
    except Exception:
        fflog('kitsu/ARM: serwer niedostępny — przełączam na fallback (Fribb)')
        _arm_down = True
        return None


def _ids_from_fribb(tmdb_id: str, media_type: str
                    ) -> Optional[List[Tuple[int, Optional[int], Optional[int]]]]:
    """Mapowanie ze statycznych list Fribb (fallback gdy ARM padł)."""
    entries = _get_fribb_index().get(f'{media_type}:{tmdb_id}')
    if entries:
        fflog(f'kitsu/Fribb: tmdb={media_type}:{tmdb_id} → {entries}')
        return [tuple(e) for e in entries]
    return None


def _get_fribb_index() -> Dict[str, List]:
    """Indeks 'typ:tmdb_id' → [[kitsu_id, mal_id, anilist_id], ...] z list Fribb.

    Listy Fribb pobierane są raz i trzymane w trwałym cache (7 dni).
    """
    global _fribb_index
    if _fribb_index is not None:
        return _fribb_index

    # 1) trwały cache
    cached = cache.cache_get('kitsu_fribb_index', control.providercacheFile)
    if cached and cached.get('date') \
            and cache._is_cache_valid(int(cached['date']), _FRIBB_TTL_H):
        try:
            _fribb_index = json.loads(cached['value'])
            return _fribb_index
        except (ValueError, TypeError):
            pass

    # 2) pobierz listę i zbuduj indeks (TMDB ma rozłączne ID dla tv i movie)
    index: Dict[str, List] = {}
    try:
        resp = requests.get(FRIBB_URL, headers=_HEADERS, timeout=30)
        if resp and resp.status_code == 200:
            for entry in resp.json():
                kitsu_id = entry.get('kitsu_id')
                if not kitsu_id:
                    continue
                tmdb = entry.get('themoviedb_id')
                if isinstance(tmdb, dict):
                    pairs = [(t, v) for t, v in tmdb.items() if v]
                elif tmdb:
                    pairs = [('tv', tmdb), ('movie', tmdb)]  # typ nieznany
                else:
                    continue
                row = [kitsu_id, entry.get('mal_id'), entry.get('anilist_id')]
                for media_type, tid in pairs:
                    index.setdefault(f'{media_type}:{tid}', []).append(row)
            cache.cache_insert('kitsu_fribb_index', json.dumps(index),
                               control.providercacheFile)
            fflog(f'kitsu/Fribb: zbudowano indeks ({len(index)} pozycji)')
        else:
            fflog(f'kitsu/Fribb: błąd pobrania, '
                  f'status={getattr(resp, "status_code", "?")}')
    except Exception:
        fflog_exc()

    # 3) pobranie padło — użyj nieświeżego cache, jeśli istnieje
    if not index and cached and cached.get('value'):
        try:
            index = json.loads(cached['value'])
            fflog('kitsu/Fribb: używam nieświeżego indeksu')
        except (ValueError, TypeError):
            pass

    _fribb_index = index
    return index


def _get_kitsu_meta(kitsu_id: int) -> Optional[_KitsuMeta]:
    """Zwraca metadane anime z Kitsu API (z cache)."""
    if kitsu_id in _kitsu_cache:
        return _kitsu_cache[kitsu_id]

    try:
        resp = requests.get(KITSU_URL % kitsu_id, headers=_HEADERS, timeout=10)
        if not resp or resp.status_code != 200:
            fflog(f'kitsu: błąd dla kitsu_id={kitsu_id} status={getattr(resp, "status_code", "?")}')
            return None

        data = resp.json().get('data', {})
        if not data:
            return None

        attrs = data.get('attributes', {})

        # Preferuj en_jp (romaji) — to czego używają polskie strony animezone/shinden
        titles = attrs.get('titles', {})
        title: str = (titles.get('en_jp')
                      or titles.get('en')
                      or attrs.get('canonicalTitle')
                      or '')

        start_date: Optional[date] = None
        if start_raw := attrs.get('startDate'):
            try:
                start_date = datetime.strptime(start_raw, '%Y-%m-%d').date()
            except ValueError:
                pass

        end_date: Optional[date] = None
        if end_raw := attrs.get('endDate'):
            try:
                end_date = datetime.strptime(end_raw, '%Y-%m-%d').date()
            except ValueError:
                pass

        meta = _KitsuMeta(
            kitsu_id=kitsu_id,
            title=title,
            ep_count=attrs.get('episodeCount') or 0,
            start_date=start_date,
            end_date=end_date,
            subtype=attrs.get('subtype', ''),
        )
        _kitsu_cache[kitsu_id] = meta
        return meta

    except Exception:
        fflog_exc()
        return None
