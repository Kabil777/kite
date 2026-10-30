"""Independent boundary-aware fuzzy ranking and exponentially decaying launch history."""
from functools import lru_cache
import math
import re
import time
import unicodedata

SEPARATORS = frozenset('\t\n -./()[]')
HALF_LIFE = 10 * 86400
TERM_WINDOW = 17 * 86400
_TRANSLATION = str.maketrans(dict(zip(
    'ßæÆøØđĐłŁœŒþÞðÐıħĦ',
    ['ss', 'ae', 'AE', 'o', 'O', 'd', 'D', 'l', 'L', 'oe', 'OE', 'th', 'Th', 'd', 'D', 'i', 'h', 'H'])))


@lru_cache(maxsize=8192)
def transliterate(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.translate(_TRANSLATION))
                   if not unicodedata.combining(c))


@lru_cache(maxsize=8192)
def target_info(text):
    text = transliterate(text)
    boundaries = {i for i in range(1, len(text))
                  if (text[i - 1].islower() or text[i - 1].isdigit()) and text[i].isupper()}
    return text.lower(), boundaries


def fuzzy_match(target, query):
    """Return (score, skipped separators), or None; DP uses O(target length) memory."""
    text, boundaries = target_info(target)
    query = transliterate(query).lower()
    if not query:
        return None
    if text == query:
        return float('inf'), 0
    required = sum(c not in SEPARATORS for c in query)
    if required > len(text):
        return None
    # Cheap rejection keeps DP out of the common nonmatching-app path.
    cursor = 0
    for char in query:
        if char not in SEPARATORS:
            cursor = text.find(char, cursor)
            if cursor < 0:
                return None
            cursor += 1
    previous, first, matched, skipped = None, -1, 0, 0
    for char in query:
        current = [-math.inf] * len(text)
        first_current, gap_best = -1, -math.inf
        for i in range(first + 1, min(len(text), len(text) - required + 1 + matched)):
            if previous is not None and i - 2 >= first:
                gap_best = max(gap_best, previous[i - 2] - 1)
            separator = char in SEPARATORS and text[i] in SEPARATORS
            if text[i] != char and not separator:
                continue
            points = (1 if separator else 4 if matched == 0 and i == 0 else
                      3 if text[i] not in SEPARATORS and
                      (i in boundaries or i > 0 and text[i - 1] in SEPARATORS) else 2)
            value = points if previous is None else points + max(previous[i - 1], gap_best)
            if value != -math.inf:
                current[i] = value
                if first_current < 0:
                    first_current = i
        if first_current >= 0:
            previous, first, matched = current, first_current, matched + 1
        elif char in SEPARATORS:
            skipped += 1
        else:
            return None
    return (max(previous), skipped) if previous is not None else None


def passes(match, length, sensitivity='medium'):
    if match is None:
        return False
    points, skipped = match
    return (sensitivity == 'low' or points == math.inf or
            (points > 2 * (length - skipped) if sensitivity == 'high' else
             points >= 1.5 * (length - skipped - 2) + 4))


def score(query, title, keywords=''):
    """Compatibility helper; exact titles keep the previous public score of 1000."""
    query = query.strip()
    if not query:
        return 1
    matches = [fuzzy_match(title, query), fuzzy_match(keywords, query)]
    return max((1000 if m[0] == math.inf else m[0] for m in matches
                if m is not None and passes(m, len(query))), default=0)


def frecency(record, now=None):
    now = time.time() if now is None else now
    anchor = record.get('anchor')
    if type(anchor) in (int, float) and math.isfinite(anchor):
        return max(1, math.exp(min(700, (anchor - now) * math.log(2) / HALF_LIFE)))
    # ponytail: old count/last records approximate prior visits at their last launch; no destructive migration.
    count, last = record.get('count', 0), record.get('last', 0)
    if type(count) is not int or count < 0 or type(last) not in (int, float) or not math.isfinite(last):
        return 1
    return max(1, (1 + 100 * min(count, 1000000)) * 2 ** (-max(0, now - last) / HALF_LIFE))


def record_visit(record, query='', now=None):
    now = time.time() if now is None else now
    terms = record.get('terms', [])
    terms = [s for s in terms if isinstance(s, str)] if isinstance(terms, list) else []
    query = query.strip().lower()[:512]
    if query:
        terms = [s for s in terms if s != query] + [query]
    return {'count': record.get('count', 0) + 1, 'last': now,
            'anchor': now + HALF_LIFE * math.log2(frecency(record, now) + 100),
            'terms': terms[-20:]}


def rank(rows, query, usage=None, limit=60, *, sensitivity='medium', aliases=None,
         learn_queries=True, now=None):
    usage, aliases = usage or {}, aliases or {}
    now = time.time() if now is None else now
    query = transliterate(query.strip()).lower()
    ranked = []
    # ponytail: scan bounded app/emoji catalogs; indexed providers if larger datasets hurt typing latency.
    for row in rows:
        record = usage.get(row.id, {})
        frequency = frecency(record, now)
        alias = transliterate(aliases.get(row.id, '')).lower()
        title, boundaries = target_info(row.title)
        kind = 5 if row.kind == 'app' else 4 if row.kind == 'mode' else 1
        if not query:
            key = (-frequency, -bool(alias), -kind, title, row.id)
        else:
            title_match, subtitle_match = fuzzy_match(row.title, query), fuzzy_match(row.subtitle, query)
            keyword_match = fuzzy_match(row.keywords, query)
            alias_exact, alias_prefix = alias == query, bool(alias and alias.startswith(query))
            if not (alias_prefix or any(passes(m, len(query), sensitivity)
                                        for m in (title_match, subtitle_match, keyword_match))):
                continue
            title_points = title_match[0] if title_match else -math.inf
            if len(query) >= 3 and not re.search(r'\s', query):
                for found in re.finditer(re.escape(query), title):
                    start, end = found.span()
                    if ((start == 0 or title[start - 1] in SEPARATORS or start in boundaries)
                            and (end == len(title) or title[end] in SEPARATORS or end in boundaries)):
                        title_points = max(title_points, 4 + 2 * (len(query) - 1) + .5)
            terms = record.get('terms', [])
            terms = terms if learn_queries and isinstance(terms, list) and frequency > 1 and now - record.get('last', 0) <= TERM_WINDOW else []
            term_exact = query in terms
            term_prefix = any(isinstance(t, str) and t.startswith(query) for t in terms)
            subtitle_points = subtitle_match[0] if subtitle_match else -math.inf
            # Keywords admit candidates, but do not outrank a strong visible-title match.
            key = (-alias_exact, -term_exact, -(title == query), -(subtitle_points == math.inf),
                   -alias_prefix, -term_prefix, -max(title_points, subtitle_points),
                   -frequency, -title_points, -kind, title, row.id)
        ranked.append((key, row))
    return [row for _, row in sorted(ranked, key=lambda pair: pair[0])[:limit]]
