"""Run with qt-venv/bin/python -m tests.check_ranking; no GUI, network or state writes."""
import math
from main.core import Result
from main.ranking import HALF_LIFE, TERM_WINDOW, frecency, fuzzy_match, passes, rank, record_visit, transliterate

assert transliterate('Café Ærø Straße') == 'Cafe AEro Strasse'
for title, query, points, skipped in [
    ('Firefox', 'fir', 8, 0), ('Firefox', 'fx', 5, 0),
    ('LibreOffice Writer', 'lw', 6, 0), ('LibreOffice Writer', 'lo', 6, 0),
    ('Visual Studio Code', 'vsc', 8, 0), ('foo-bar', 'f-b', 7, 0),
    ('Font Viewer', 'fir', 6, 0), ('LibreOffice Writer', 'fir', 5, 0),
    ('Firefox', 'fire ', 10, 1), ('org-gnome', 'org.g', 12, 0),
]:
    assert fuzzy_match(title, query) == (points, skipped), (title, query, fuzzy_match(title, query))
assert fuzzy_match('Firefox', 'firefox') == (math.inf, 0)
for title, query in [('abc', 'cba'), ('Files', 'fir'), ('ab', 'abc'), ('Firefox', '')]:
    assert fuzzy_match(title, query) is None
assert passes((5.5, 0), 3) and not passes((5, 0), 3)
assert passes((5.5, 1), 4) and not passes((5.5, 0), 4)
assert passes((6.5, 0), 3, 'high') and not passes((6, 0), 3, 'high')
assert passes((-3, 0), 3, 'low') and not passes(None, 1, 'low')

now = 1800000000
visit = record_visit({}, '  FIR ', now)
assert visit['terms'] == ['fir'] and visit['count'] == 1
assert math.isclose(frecency(visit, now), 101)
assert math.isclose(frecency(visit, now + HALF_LIFE), 50.5)
assert math.isclose(frecency(visit, now + 2 * HALF_LIFE), 25.25)
visit = record_visit(visit, 'ff', now + HALF_LIFE)
assert math.isclose(frecency(visit, now + HALF_LIFE), 150.5)
visit = record_visit(visit, 'FIR', now + HALF_LIFE)
assert math.isclose(frecency(visit, now + HALF_LIFE), 250.5)
assert visit['terms'] == ['ff', 'fir']
for i in range(30):
    visit = record_visit(visit, 'q' + str(i), now + HALF_LIFE)
assert len(visit['terms']) == 20 and visit['terms'][-1] == 'q29'
assert frecency({}, now) == 1 and frecency({'anchor': float('nan')}, now) == 1
assert math.isfinite(frecency({'anchor': 1e300}, now))
assert math.isclose(frecency({'count': 2, 'last': now}, now), 201)

apps = [Result(t, t, kind='app') for t in
        ['Firefox', 'Files', 'Fire Dragon', 'Chromium', 'Signal', 'LibreOffice Writer', 'Alacritty', 'Font Viewer']]
assert [r.title for r in rank(apps, 'fir', now=now)] == ['Fire Dragon', 'Firefox', 'Font Viewer']
assert [r.title for r in rank(apps, 'fir', sensitivity='high', now=now)] == ['Fire Dragon', 'Firefox']
usage = {'Firefox': record_visit({}, 'fir', now)}
assert rank(apps, 'fir', usage, now=now)[0].title == 'Firefox'
assert rank([Result('x', 'Xylophone'), Result('f', 'Fluffy Forest')], 'fl',
            aliases={'x': 'flu'})[0].id == 'x'
assert rank([Result('f', 'Firefox'), Result('x', 'ff')], 'ff', aliases={'f': 'ff'})[0].id == 'f'
assert rank([Result('w', 'Writerly Notes App'), Result('l', 'LibreOffice Writer')], 'writer')[0].id == 'l'
apps = [Result('f', 'Firefox', 'Web Browser'), Result('w', 'Web Builder')]
usage = {'f': record_visit({}, 'web', now)}
assert rank(apps, 'web', usage, now=now)[0].id == 'f'
assert rank(apps, 'web', usage, now=now + TERM_WINDOW + 1)[0].id == 'w'
assert rank(apps, 'web', usage, now=now, learn_queries=False)[0].id == 'w'
assert not rank([Result('x', 'Unrelated')], 'needle', {'x': record_visit({}, 'needle', now)}, now=now)
print('Ranking OK: DP scores, boundaries, accent folding, thresholds, aliases, whole words, decay, legacy history, learned-term expiry')
