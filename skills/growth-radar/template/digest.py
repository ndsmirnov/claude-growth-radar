"""Ежедневная подборка «Радара роста».
  python3 -I digest.py          — собрать и отправить в Telegram
  python3 -I digest.py --dry    — собрать и напечатать, ничего не отправлять и не сохранять
  python3 -I digest.py --feeds  — только проверить RSS-ленты
  python3 -I digest.py --scheduled — запуск по расписанию: пропустить, если пауза или уже было сегодня"""
import datetime as dt
import email.utils
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

MAX_AGE_DAYS = 4
UA = 'Mozilla/5.0 (Macintosh) growth-radar/1.0'


# ---------- сбор новостей ----------

def _txt(el):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', ''.join(el.itertext()) if el is not None else '')).strip()


def _date(s):
    if not s:
        return None
    try:
        return email.utils.parsedate_to_datetime(s).date()
    except (TypeError, ValueError):
        pass
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _first(g, *names):
    for n in names:
        if g.get(n) is not None:
            return g[n]
    return None


def fetch_feed(f):
    req = urllib.request.Request(f['url'], headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        root = ET.fromstring(r.read())
    out = []
    for el in root.iter():
        tag = el.tag.split('}')[-1]
        if tag not in ('item', 'entry'):
            continue
        g = {ch.tag.split('}')[-1]: ch for ch in el}
        link = ''
        if 'link' in g:
            link = (g['link'].text or '').strip() or g['link'].get('href', '')
        for ch in el:
            if ch.tag.split('}')[-1] == 'link' and ch.get('rel', 'alternate') == 'alternate' and ch.get('href'):
                link = ch.get('href')
                break
        desc = _first(g, 'description', 'summary', 'content', 'encoded')
        de = _first(g, 'pubDate', 'published', 'updated', 'date')
        d = _date(de.text if de is not None else None)
        title = _txt(g.get('title'))
        if title and link:
            out.append({'source': f['name'], 'lang': f['lang'], 'title': title[:200], 'url': link,
                        'snippet': _txt(desc)[:280], 'date': d.isoformat() if d else ''})
        if len(out) >= f.get('take', 5) * 3:
            break
    return out


def collect():
    feeds = c.load(c.P_FEEDS, [])
    seen = set(c.load(c.P_SEEN, []))
    cutoff = (dt.date.today() - dt.timedelta(days=MAX_AGE_DAYS)).isoformat()
    cands, report = [], []
    for f in feeds:
        try:
            items = fetch_feed(f)
        except Exception as e:  # лента упала — не страшно, остальные работают
            report.append(f'✗ {f["name"]}: {str(e)[:80]}')
            continue
        fresh = [i for i in items if i['url'] not in seen and (not i['date'] or i['date'] >= cutoff)]
        report.append(f'✓ {f["name"]}: {len(items)} всего, {len(fresh)} свежих')
        cands += fresh[:f.get('take', 5)]
    for n, it in enumerate(cands, 1):
        it['id'] = f'n{n}'
    return cands, report


# ---------- генерация ----------

SYSTEM = """Ты — личный куратор роста владельца бота (кто он(а) — в профиле): собираешь ежедневную подборку в Telegram.
Пиши по-русски, просто, живо, без канцелярита и без воды, как умный друг, а не как учебник.
Правила:
- Новости выбирай ТОЛЬКО из списка кандидатов и указывай их id. Ссылок не придумывай.
- Материалы для изучения — только реально существующие, широко известные (каналы, подкасты, книги, видео, курсы), с конкретным автором или каналом — никаких «любой разбор». Если не уверен, что материал существует, не предлагай его. Вместо ссылки дай точный поисковый запрос, по которому он находится.
- Каждый пункт привязывай к вектору (id темы) и к реальности из профиля: работа, проекты, цели.
- Учитывай обратную связь: что зацепило — больше такого, что мимо — меньше. Не повторяй уже рекомендованное.
- Формат — как в профиле и в «что заходит» (по умолчанию: видео, подкасты, аудиокниги; чтение — максимум одна позиция).
- Язык: темы без базы — русские ролики с азов, лёгкие. Иностранный язык — по профилю (для тем, где уже есть контекст, и для практики языка).
- Где можно — YouTube (where=youtube): бот сам даёт прямую ссылку на ролик. Подкасты тоже бери с YouTube. Книги — аудиоверсия или хороший пересказ на YouTube.
- Не повторяй слишком базовое по темам с уровнем 2+ — давай глубже.
- Если на что-то нет реакции неделю — меняй подачу, а не дави.
- Если ниже есть «отложка» — то, что владелец сам сохранял, чтобы вернуться, — Примерно через день один из трёх материалов бери оттуда, с пометкой «из твоей отложки» в why, и привязывай к теме вектора. По субботам и воскресеньям — фильм или сериал из отложки для разгрузки, с одной продуктовой мыслью к нему. Не бери то, что уже отмечено изученным. Если отложки нет — пропусти это правило.
"""

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['hello', 'focus_topic', 'focus_why', 'news', 'learn', 'work_tie', 'question'],
    'properties': {
        'hello': {'type': 'string', 'description': '1-2 живых предложения: настрой дня, без пафоса'},
        'focus_topic': {'type': 'string', 'description': 'id темы вектора — фокус дня'},
        'focus_why': {'type': 'string', 'description': 'почему эта тема сегодня, 1 предложение'},
        'news': {'type': 'array', 'minItems': 2, 'maxItems': 5, 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['id', 'title_ru', 'summary', 'why_you', 'topic_id'],
            'properties': {
                'id': {'type': 'string', 'description': 'id кандидата, например n12'},
                'title_ru': {'type': 'string'},
                'summary': {'type': 'string', 'description': 'суть в 1-2 предложениях простым языком'},
                'why_you': {'type': 'string', 'description': 'чем это полезно именно владельцу, 1 предложение'},
                'topic_id': {'type': 'string'}}}},
        'learn': {'type': 'array', 'minItems': 3, 'maxItems': 3, 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['format', 'title', 'author', 'lang', 'minutes', 'search', 'where', 'why', 'topic_id'],
            'properties': {
                'format': {'type': 'string', 'enum': ['видео', 'подкаст', 'аудиокнига', 'интерактив', 'статья']},
                'title': {'type': 'string'}, 'author': {'type': 'string'},
                'lang': {'type': 'string', 'enum': ['ru', 'en']},
                'minutes': {'type': 'integer'},
                'search': {'type': 'string', 'description': 'точный поисковый запрос, по которому материал находится'},
                'where': {'type': 'string', 'enum': ['youtube', 'google']},
                'why': {'type': 'string', 'description': 'что из этого вынести и как это связано с работой/целями'},
                'topic_id': {'type': 'string'}}}},
        'work_tie': {'type': 'string', 'description': 'где фокус-тема прямо сейчас видна в его/её работе или проектах (по профилю) + мини-задание на 5 минут'},
        'question': {'type': 'string', 'description': 'один вопрос дня для ответа реплаем — на понимание или рефлексию'}}}


def library_text():
    t = c.read(c.LIBRARY_MD)
    main = t.split('## Как бот использует карту')[0]
    ocr = t.split('<!-- radar:ocr -->')[-1] if '<!-- radar:ocr -->' in t else ''
    return main[-14000:] + '\n## С обложек и постеров\n' + ocr[:7000]


def build_prompt(cands):
    pr = c.progress()
    ph, weeks = c.phase_now()
    rm = c.roadmap()
    titles = {t['id']: t['title'] for tr in rm['tracks'] for t in tr['topics']}
    focus = '\n'.join(f'- {tid} {titles[tid]} — уровень {pr["topics"][tid]["level"]}/4' for tid in ph['focus'])
    fb = c.recent_feedback(14, 60)
    fb_txt = '\n'.join(f'- {r["date"]} [{r.get("action") or r.get("type")}] {r.get("title", "")} {r.get("text", "")}'.strip()
                       for r in fb) or '- пока нет (первые дни)'
    sent_titles = []
    for fn in sorted(os.listdir(f'{c.DATA}/sent'))[-30:]:
        d = c.load(f'{c.DATA}/sent/{fn}', {})
        sent_titles += [x['title'] for x in d.get('learn', [])]
    nf = c.load(c.P_STATE, {}).get('next_focus')
    wk = f'Фокус этой недели по итогам недельного разбора: {", ".join(nf)}.\n' if nf else ''
    cand_txt = '\n'.join(f'{i["id"]} | {i["source"]} | {i["lang"]} | {i["date"]} | {i["title"]} — {i["snippet"]}' for i in cands)
    return f"""Сегодня {dt.date.today().strftime('%d.%m.%Y, %A')}.

# Профиль
{c.read(c.P_PROFILE)}

# Что ему заходит
{c.read(c.P_PREFS)}

# Его отложка (карта интересов)
{library_text()}

# Вектор
{c.vector_text(pr, compact=False).replace('<b>', '').replace('</b>', '')}
Уровни: {', '.join(rm['levels'])}

# Фаза погружения: {ph['n']} «{ph['title']}», неделя {weeks}
Цель фазы: {ph['goal']}
Темы фокуса фазы:
{focus}
{wk}Выбирай фокус дня среди них, чередуй треки, чаще — темы с низким уровнем.

# Обратная связь за 2 недели
{fb_txt}

# Уже рекомендовал (не повторять)
{chr(10).join('- ' + t for t in sent_titles[-60:]) or '- ничего'}

# Кандидаты в новости (id | источник | язык | дата | заголовок — описание)
{cand_txt}

Собери подборку: 2–5 новостей (только то, что реально важно ему, остальное отбрось), 3 материала на выбор, связку с работой и вопрос дня."""


# ---------- отправка ----------

def youtube_first(q):
    """Первый ролик по запросу: (url, название, канал, длительность) или None."""
    try:
        u = 'https://www.youtube.com/results?search_query=' + urllib.parse.quote_plus(q) + '&hl=ru'
        h = urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': UA, 'Accept-Language': 'ru'}), timeout=20).read().decode()
        d = json.loads(re.search(r'var ytInitialData = (\{.*?\});</script>', h).group(1))
    except Exception:
        return None
    stack = [d]
    while stack:
        o = stack.pop(0)
        if isinstance(o, dict):
            if 'videoRenderer' in o:
                v = o['videoRenderer']
                return (f'https://youtu.be/{v["videoId"]}', ''.join(r['text'] for r in v['title']['runs']),
                        v.get('ownerText', {}).get('runs', [{}])[0].get('text', ''), v.get('lengthText', {}).get('simpleText', ''))
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return None


def link_for(it):
    if it['where'] == 'youtube':
        hit = youtube_first(it['search'])
        if hit:
            return hit
        return (f'https://www.youtube.com/results?search_query={urllib.parse.quote_plus(it["search"])}', '', '', '')
    return (f'https://www.google.com/search?q={urllib.parse.quote_plus(it["search"])}', '', '', '')


FMT_ICON = {'видео': '▶️', 'подкаст': '🎧', 'аудиокнига': '📚', 'интерактив': '🕹', 'статья': '📄'}


def send_digest(d, cands):
    by_id = {i['id']: i for i in cands}
    rm = c.roadmap()
    titles = {t['id']: t['title'] for tr in rm['tracks'] for t in tr['topics']}
    ph, weeks = c.phase_now()
    day = dt.date.today()
    key = day.strftime('%m%d')
    items = c.load(c.P_ITEMS, {})
    record = {'date': c.today(), 'focus': d['focus_topic'], 'news': [], 'learn': [], 'question': d['question']}

    head = (f'☀️ <b>Радар роста · {day.strftime("%d.%m")}</b>\n'
            f'Фаза {ph["n"]} «{c.esc(ph["title"])}», неделя {weeks}\n\n'
            f'{c.esc(d["hello"])}\n\n'
            f'🎯 <b>Фокус дня: {c.esc(titles.get(d["focus_topic"], d["focus_topic"]))}</b>\n{c.esc(d["focus_why"])}\n\n'
            f'🔧 <b>Где это в твоей работе</b>\n{c.esc(d["work_tie"])}\n\n'
            f'❓ <b>Вопрос дня</b> (ответь реплаем — текстом или голосом)\n{c.esc(d["question"])}')
    m = c.send(head)
    items[str(m['message_id'])] = {'kind': 'question', 'key': f'{key}q', 'title': d['question'], 'topic_id': d['focus_topic'], 'date': c.today()}

    news = [n for n in d['news'] if n['id'] in by_id]
    if news:
        c.send('📰 <b>Новости</b>', silent=True)
    for n_i, n in enumerate(news, 1):
        src = by_id[n['id']]
        k = f'{key}n{n_i}'
        txt = (f'<b>{c.esc(n["title_ru"])}</b>\n{c.esc(n["summary"])}\n'
               f'<i>Тебе:</i> {c.esc(n["why_you"])}\n<a href="{c.esc(src["url"])}">{c.esc(src["source"])}</a>')
        m = c.send(txt, buttons=[[('🔥 Зацепило', f'hot:{k}'), ('👍', f'ok:{k}'), ('👎 Мимо', f'no:{k}')]], silent=True)
        it = {'kind': 'news', 'key': k, 'title': n['title_ru'], 'url': src['url'], 'topic_id': n['topic_id'], 'date': c.today()}
        items[str(m['message_id'])] = it
        record['news'].append(it)

    pin_lines = []
    c.send('🎧 <b>На выбор, чтобы потупить с пользой</b>', silent=True)
    for l_i, l in enumerate(d['learn'], 1):
        k = f'{key}l{l_i}'
        url, vt, vch, vlen = link_for(l)
        dur = vlen or f'~{l["minutes"]} мин'
        found = f'\n▶️ <a href="{c.esc(url)}">{c.esc(vt)}</a> — {c.esc(vch)}' if vt else f'\n<a href="{c.esc(url)}">Найти</a>'
        txt = (f'{FMT_ICON.get(l["format"], "•")} {c.esc(l["format"])} · {c.esc(dur)} · {l["lang"].upper()}\n'
               f'<b>{c.esc(l["title"])}</b> — {c.esc(l["author"])}\n{c.esc(l["why"])}\n'
               f'<i>Тема: {c.esc(titles.get(l["topic_id"], l["topic_id"]))}</i>{found}')
        pin_lines.append(f'{l_i}. <a href="{c.esc(url)}">{c.esc(vt or l["title"])}</a> · {c.esc(dur)}')
        m = c.send(txt, buttons=[[('▶️ Беру', f'take:{k}'), ('✅ Изучил', f'done:{k}'), ('👎 Не моё', f'no:{k}')]], silent=True)
        it = {'kind': 'learn', 'key': k, 'title': l['title'], 'author': l['author'], 'format': l['format'],
              'url': url, 'topic_id': l['topic_id'], 'date': c.today()}
        items[str(m['message_id'])] = it
        record['learn'].append(it)

    c.save(c.P_ITEMS, items)
    c.save(f'{c.DATA}/sent/{c.today()}.json', record)
    pin_txt = (f'📌 <b>Подборка {day.strftime("%d.%m")}</b> · фокус: {c.esc(titles.get(d["focus_topic"], d["focus_topic"]))}\n\n'
               + '\n'.join(pin_lines)
               + ('\n\n📰 ' + '\n📰 '.join(f'<a href="{c.esc(it["url"])}">{c.esc(it["title"])}</a>' for it in record['news']) if record['news'] else '')
               + f'\n\n❓ {c.esc(d["question"])}')
    pm = c.send(pin_txt, silent=True)
    st0 = c.load(c.P_STATE, {})
    try:
        if st0.get('pinned'):
            c.tg('unpinChatMessage', {'chat_id': c.chat_id(), 'message_id': st0['pinned']})
        c.tg('pinChatMessage', {'chat_id': c.chat_id(), 'message_id': pm['message_id'], 'disable_notification': 'true'})
        st0['pinned'] = pm['message_id']
        c.save(c.P_STATE, st0)
    except Exception as e:
        c.log('digest', f'закрепить не вышло: {e}')
    seen = c.load(c.P_SEEN, [])
    seen += [by_id[n['id']]['url'] for n in news]
    c.save(c.P_SEEN, seen[-2000:])
    st = c.load(c.P_STATE, {})
    st['last_digest'] = c.today()
    c.save(c.P_STATE, st)


def main():
    import fcntl
    args = sys.argv[1:]
    lock = open(f'{c.DATA}/digest.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # одна подборка за раз
    except BlockingIOError:
        c.log('digest', 'пропуск: другая подборка уже собирается')
        return
    st = c.load(c.P_STATE, {})
    if '--scheduled' in args and (st.get('paused') or st.get('last_digest') == c.today()):
        c.log('digest', 'пропуск: пауза или уже отправлено сегодня')
        return
    cands, report = collect()
    if '--feeds' in args:
        print('\n'.join(report))
        print(f'кандидатов: {len(cands)}')
        return
    c.log('digest', f'кандидатов {len(cands)}; ' + '; '.join(r for r in report if r.startswith('✗')))
    d = c.claude(SYSTEM, build_prompt(cands), schema=SCHEMA)
    if '--dry' in args:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return
    send_digest(d, cands)
    c.log('digest', 'отправлено')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        c.log('digest', f'ОШИБКА: {e}')
        if '--dry' not in sys.argv and c.chat_id():
            try:
                c.send(f'⚠️ Подборка сегодня не собралась: {c.esc(str(e)[:300])}\nПопробую ещё раз командой /today.')
            except Exception:
                pass
        raise
