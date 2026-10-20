"""Недельный разбор «Радара роста» (воскресенье вечером).
Сжимает обратную связь в preferences.md, обновляет уровни тем, пишет запись в vector.md, шлёт итог в Telegram.
  python3 -I weekly.py         — по-настоящему
  python3 -I weekly.py --dry   — только показать, что получилось"""
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

SYSTEM = """Ты — куратор роста владельца бота (кто он(а) — в профиле). Раз в неделю подводишь итог его «Радара роста».
Пиши по-русски, просто и живо, без канцелярита. Опирайся только на данные ниже, ничего не выдумывай.
Если обратной связи мало — так и скажи, без натяжек."""

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['preferences_md', 'telegram', 'journal_entry', 'level_updates', 'next_focus'],
    'properties': {
        'preferences_md': {'type': 'string', 'description': 'новый раздел «## Устойчивое» целиком: 5–15 пунктов-строк «- ...», сжатие старого + свежих заметок + кнопок'},
        'telegram': {'type': 'string', 'description': 'итог недели для Telegram, HTML (<b>, <i>), 6–12 строк: что зашло, что нет, где вырос, фокус следующей недели'},
        'journal_entry': {'type': 'string', 'description': 'запись в журнал вектора (markdown, без заголовка): что изучено, что зацепило, сдвиги уровней, выводы, фокус дальше'},
        'level_updates': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['topic_id', 'level', 'reason'],
            'properties': {'topic_id': {'type': 'string'}, 'level': {'type': 'integer'}, 'reason': {'type': 'string'}}}},
        'next_focus': {'type': 'array', 'items': {'type': 'string'}, 'description': 'id 2–4 тем фокуса на следующую неделю'}}}


def week_data():
    fb = c.recent_feedback(7, 300)
    sent = []
    for i in range(7):
        d = (dt.date.today() - dt.timedelta(days=i)).isoformat()
        rec = c.load(f'{c.DATA}/sent/{d}.json', None)
        if rec:
            sent.append(rec)
    pr = c.progress()
    hist = [h for h in pr['history'] if h['date'] >= (dt.date.today() - dt.timedelta(days=7)).isoformat()]
    return fb, sent, pr, hist


def vector_block(pr):
    rm = c.roadmap()
    ph, weeks = c.phase_now()
    rows = ['| Тема | Уровень | Что было последним |', '|---|---|---|']
    for tr in rm['tracks']:
        for t in tr['topics']:
            p = pr['topics'][t['id']]
            rows.append(f'| {tr["emoji"]} {t["id"]} {t["title"]} | {"●" * p["level"]}{"○" * (4 - p["level"])} {p["level"]}/4 | {p.get("note") or "—"} |')
    head = f'Неделя {weeks}, фаза {ph["n"]} «{ph["title"]}». Обновлено {c.today()} ботом «Радар роста».\n\n'
    return head + '\n'.join(rows)


def write_journal(entry, pr):
    path = c.VECTOR_MD
    txt = c.read(path)
    if not txt:
        return False
    block = vector_block(pr)
    txt = re.sub(r'(<!-- radar:progress:start -->\n).*?(\n<!-- radar:progress:end -->)',
                 lambda m: m.group(1) + block + m.group(2), txt, flags=re.S)
    txt = re.sub(r'^updated: .*$', f'updated: {c.today()}', txt, count=1, flags=re.M)
    marker = '<!-- radar:log -->'
    rec = f'{marker}\n\n## {c.today()} [Радар роста] — итог недели\n\n{entry.strip()}\n'
    txt = txt.replace(marker, rec, 1)
    with open(path, 'w') as f:
        f.write(txt)
    return True


def main():
    dry = '--dry' in sys.argv
    fb, sent, pr, hist = week_data()
    prompt = f"""# Профиль
{c.read(c.P_PROFILE)}

# Текущий preferences.md
{c.read(c.P_PREFS)}

# Вектор сейчас
{c.vector_text(pr).replace('<b>', '').replace('</b>', '')}

# Сдвиги уровней за неделю
{json.dumps(hist, ensure_ascii=False) or '—'}

# Что отправлено за неделю
{json.dumps([{'date': s['date'], 'focus': s['focus'], 'news': [n['title'] for n in s['news']], 'learn': [l['title'] for l in s['learn']]} for s in sent], ensure_ascii=False)}

# Обратная связь за неделю (кнопки, ответы, голосовые)
{chr(10).join(json.dumps(r, ensure_ascii=False) for r in fb) or 'нет'}

Подведи итог недели. Уровни меняй только при явном свидетельстве (✅ Изучил + его объяснение своими словами, применение)."""
    res = c.claude(SYSTEM, prompt, schema=SCHEMA, timeout=600)
    if dry:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    for u in res['level_updates']:
        c.set_level(pr, u['topic_id'], u['level'], u['reason'], 'недельный разбор')
    c.save(c.P_PROGRESS, pr)
    prefs = c.read(c.P_PREFS)
    head = prefs.split('## Устойчивое')[0]
    body = res['preferences_md'].strip()
    if not body.startswith('## Устойчивое'):
        body = '## Устойчивое\n' + body
    with open(c.P_PREFS, 'w') as f:
        f.write(f'{head}{body}\n\n## Свежие заметки (до недельного сжатия)\n')
    st = c.load(c.P_STATE, {})
    st['next_focus'] = res['next_focus']
    c.save(c.P_STATE, st)
    ok = write_journal(res['journal_entry'], pr)
    c.send(f'🗓 <b>Итог недели — Радар роста</b>\n\n{res["telegram"]}\n\n' + c.vector_text(pr, compact=True)
           + ('' if ok else '\n\n⚠️ Файл vector.md не найден — запись не сделана.'))
    c.log('weekly', f'готово, vector={ok}')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        c.log('weekly', f'ОШИБКА: {e}')
        raise
