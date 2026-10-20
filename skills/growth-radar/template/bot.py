"""Telegram-бот «Радара роста»: принимает кнопки, ответы текстом и голосом, команды.
Работает постоянно (launchd KeepAlive), long polling. Отвечает только владельцу (TG_CHAT_ID)."""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

PY = sys.executable
ACTIONS = {'hot': '🔥 Зацепило', 'ok': '👍 Норм', 'no': '👎 Мимо', 'take': '▶️ Взял', 'done': '✅ Изучил'}
LOCK = threading.Lock()

HELP = """<b>Радар роста</b> — каждое утро к 8:00 подборка под твой вектор роста.

Как дообучать:
• кнопки под пунктами: 🔥 / 👍 / 👎, ▶️ Беру, ✅ Изучил
• ответь реплаем на любой пункт или вопрос дня — текстом или голосом: что зацепило, что нет, что непонятно
• просто напиши или надиктуй что угодно: «объясни RLS попроще», «хочу больше про агентства», «устал от новостей»

Команды:
/today — собрать подборку прямо сейчас
/vector — карта вектора и уровни тем
/plan — видение: фазы погружения и где мы
/pause, /resume — пауза утренних подборок
/help — это сообщение"""

TUTOR_SYSTEM = """Ты — личный куратор и наставник владельца «Радара роста» (Telegram; кто он(а) — в профиле, обращайся в нужном роде).
Владелец отвечает на подборку или пишет сам: делится, что зацепило, что нет, задаёт вопросы, отвечает на вопрос дня.
Твоя задача:
1) Ответить коротко и по-человечески (обычно 2–6 предложений; если просит объяснить — объясни просто, через образ и пример из его/её работы, до ~15 предложений). Без воды и без пафоса. Если это ответ на вопрос дня — мягко скажи, что верно, и поправь неточное.
2) Вытащить из слов устойчивые предпочтения (что заходит / не заходит по формату, темам, подаче), если они есть. Не выдумывай.
3) Если просят изменить САМОГО бота — формат, кнопки, время, ссылки, новые функции — запиши это в bot_requests одной фразой-задачей. В ответе скажи: «записал в доработки, Claude Code сделает». Не предлагай делать это вручную и не обещай, что уже работает. Вкус к контенту (темы, язык, длина) — это не доработка, а preference_notes.
4) Если в словах есть явное свидетельство роста по теме вектора (объяснил своими словами, применил, сделал) — предложи новый уровень этой темы. Повышай осторожно, максимум на 1 за раз; без явного свидетельства уровни не трогай.
Форматирование ответа: простой текст, можно <b>жирный</b> и <i>курсив</i> (HTML), без markdown."""

TUTOR_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['reply', 'preference_notes', 'level_updates', 'bot_requests'],
    'properties': {
        'reply': {'type': 'string'},
        'bot_requests': {'type': 'array', 'items': {'type': 'string'}, 'description': 'просьбы доработать самого бота; пусто, если нет'},
        'preference_notes': {'type': 'array', 'items': {'type': 'string'}, 'description': 'короткие устойчивые выводы о его предпочтениях; пусто, если нет'},
        'level_updates': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['topic_id', 'level', 'reason'],
            'properties': {'topic_id': {'type': 'string'}, 'level': {'type': 'integer'}, 'reason': {'type': 'string'}}}}}}


# ---------- помощники ----------

def state():
    return c.load(c.P_STATE, {})


def set_state(**kw):
    st = state()
    st.update(kw)
    c.save(c.P_STATE, st)


def owner_ok(chat):
    cid = c.chat_id()
    if not cid:  # первый, кто написал боту, становится владельцем
        c.set_env('TG_CHAT_ID', str(chat))
        st = state()
        st.setdefault('started', c.today())
        c.save(c.P_STATE, st)
        return True
    return str(chat) == str(cid)


def typing():
    try:
        c.tg('sendChatAction', {'chat_id': c.chat_id(), 'action': 'typing'})
    except Exception:
        pass


def transcribe(file_id):
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        raise RuntimeError('голосовые не настроены: нужен pip install faster-whisper (см. SKILL.md)')
    f = c.tg('getFile', {'file_id': file_id})
    tok = c.env()['TG_TOKEN']
    path = f'{c.DATA}/voice.oga'
    with urllib.request.urlopen(f'https://api.telegram.org/file/bot{tok}/{f["file_path"]}', timeout=60) as r, open(path, 'wb') as out:
        out.write(r.read())
    code = ('import os,sys\n'
            'from faster_whisper import WhisperModel\n'
            'm=WhisperModel("small", device="cpu", compute_type="int8")\n'
            'segs,_=m.transcribe(sys.argv[1], language="ru", vad_filter=True, beam_size=1)\n'
            'print(" ".join(s.text.strip() for s in segs))')
    r = subprocess.run([PY, '-I', '-c', code, path], capture_output=True, text=True, timeout=600)
    os.remove(path)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-300:])
    return r.stdout.strip()


# ---------- обработка ----------

def on_callback(q):
    data = q.get('data', '')
    action, _, key = data.partition(':')
    msg = q.get('message', {})
    items = c.load(c.P_ITEMS, {})
    it = items.get(str(msg.get('message_id')), {'key': key, 'title': '?'})
    c.add_feedback({'type': 'button', 'action': ACTIONS.get(action, action), 'key': key, 'kind': it.get('kind'),
                    'title': it.get('title'), 'topic_id': it.get('topic_id'), 'item_date': it.get('date')})
    c.tg('answerCallbackQuery', {'callback_query_id': q['id'], 'text': f'{ACTIONS.get(action, action)} — учёл'})
    if action == 'take':
        rows = [[('✅ Изучил', f'done:{key}'), ('👎 Бросил', f'no:{key}')]]
    else:
        rows = [[(f'{ACTIONS.get(action, action)} ✓', f'noop:{key}')]]
    if action != 'noop':
        try:
            c.tg('editMessageReplyMarkup', {'chat_id': c.chat_id(), 'message_id': msg['message_id'],
                                            'reply_markup': {'inline_keyboard': [[{'text': t, 'callback_data': d} for t, d in r] for r in rows]}})
        except Exception:
            pass
    if action == 'done':
        c.send('Круто. Что зацепило, что запомнилось, что осталось непонятным? Ответь реплаем на это сообщение — '
               'пары слов или голосового хватит. Так я пойму, растёт ли тема.',
               reply_to=msg.get('message_id'))


def on_text(text, msg):
    reply_to = msg.get('reply_to_message') or {}
    items = c.load(c.P_ITEMS, {})
    it = items.get(str(reply_to.get('message_id'))) if reply_to else None
    if reply_to and not it:  # ответ на служебное сообщение бота: берём его текст как контекст
        it = {'kind': 'bot_message', 'title': (reply_to.get('text') or '')[:300]}
    c.add_feedback({'type': 'text', 'text': text, 'kind': (it or {}).get('kind'), 'title': (it or {}).get('title'),
                    'topic_id': (it or {}).get('topic_id')})
    typing()
    pr = c.progress()
    dialog = c.load(f'{c.DATA}/dialog.json', [])[-8:]
    prompt = f"""# Профиль
{c.read(c.P_PROFILE)}

# Что ему заходит
{c.read(c.P_PREFS)}

# Вектор
{c.vector_text(pr).replace('<b>', '').replace('</b>', '')}

# Последние реплики
{chr(10).join(f'{d["who"]}: {d["text"]}' for d in dialog) or '—'}

# На что он отвечает
{json.dumps(it, ensure_ascii=False) if it else 'ни на что конкретное — пишет сам'}

# Его сообщение
{text}"""
    res = c.claude(TUTOR_SYSTEM, prompt, schema=TUTOR_SCHEMA, timeout=300)
    extra = []
    with LOCK:
        notes = [n.strip() for n in res.get('preference_notes', []) if n.strip()]
        if notes:
            with open(c.P_PREFS, 'a') as f:
                for n in notes:
                    f.write(f'- {c.today()}: {n}\n')
        pr = c.progress()
        for u in res.get('level_updates', []):
            old = pr['topics'].get(u['topic_id'], {}).get('level')
            if c.set_level(pr, u['topic_id'], u['level'], u['reason'], 'диалог'):
                extra.append(f'📈 {u["topic_id"]}: {old} → {pr["topics"][u["topic_id"]]["level"]} — {c.esc(u["reason"])}')
        c.save(c.P_PROGRESS, pr)
        reqs = [r.strip() for r in res.get('bot_requests', []) if r.strip()]
        if reqs:
            with open(c.P_BACKLOG, 'a') as f:
                for r in reqs:
                    f.write(f'- [ ] {c.today()}: {r} — «{text[:200]}»\n')
            extra.append('🛠 В доработки: ' + c.esc('; '.join(reqs)))
    dialog += [{'who': 'Владелец', 'text': text[:800]}, {'who': 'Куратор', 'text': res['reply'][:800]}]
    c.save(f'{c.DATA}/dialog.json', dialog[-20:])
    out = res['reply']
    if notes:
        out += '\n\n<i>Запомнил: ' + c.esc('; '.join(notes)) + '</i>'
    if extra:
        out += '\n\n' + '\n'.join(extra)
    try:
        c.send(out, reply_to=msg['message_id'])
    except RuntimeError as e:  # кривой HTML от модели — отправить без разметки
        c.log('bot', f'HTML не принят, шлю текстом: {str(e)[:150]}')
        plain = re.sub(r'<[^>]+>', '', out)
        for part in c.split_text(plain):
            c.tg('sendMessage', {'chat_id': c.chat_id(), 'text': part, 'reply_to_message_id': msg['message_id']})


def plan_text():
    rm = c.roadmap()
    ph, weeks = c.phase_now()
    titles = {t['id']: t['title'] for tr in rm['tracks'] for t in tr['topics']}
    lines = [f'<b>Видение: как погружаемся</b>\nСейчас неделя {weeks}, фаза {ph["n"]}.\n']
    for p in rm['phases']:
        mark = '👉' if p['n'] == ph['n'] else ('✅' if p['n'] < ph['n'] else '·')
        lines.append(f'{mark} <b>Фаза {p["n"]} «{c.esc(p["title"])}»</b> (с {p["from_week"]}-й недели)\n{c.esc(p["goal"])}\n'
                     f'<i>Темы: {c.esc(", ".join(titles[t].split(":")[0] for t in p["focus"]))}</i>\n')
    lines.append('Фазы сдвигаются по времени, но главное — уровни тем: /vector.')
    return '\n'.join(lines)


def run_digest():
    def go():
        r = subprocess.run([PY, '-I', f'{c.ROOT}/digest.py'], capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            c.log('bot', f'digest упал: {r.stderr[-300:]}')
    threading.Thread(target=go, daemon=True).start()


def on_command(cmd):
    if cmd in ('/start', '/help'):
        c.send(HELP)
    elif cmd == '/today':
        c.send('Собираю подборку, это минута-две…')
        run_digest()
    elif cmd == '/vector':
        c.send(c.vector_text() + '\n\n<i>●●●○ — уровень 0–4: не трогал → знаю слова → понимаю на своём примере → применил → могу объяснить и показать.</i>')
    elif cmd == '/plan':
        c.send(plan_text())
    elif cmd == '/pause':
        set_state(paused=True)
        c.send('Поставил на паузу. Утренних подборок не будет до /resume. Писать мне можно и сейчас.')
    elif cmd == '/resume':
        set_state(paused=False)
        c.send('Снова в деле. Подборка — завтра к 8:00, или сейчас: /today')
    else:
        c.send('Не знаю такой команды. /help')


def handle(u):
    if 'callback_query' in u:
        q = u['callback_query']
        if not owner_ok(q['message']['chat']['id']):
            return
        on_callback(q)
        return
    msg = u.get('message')
    if not msg or not owner_ok(msg['chat']['id']):
        return
    text = msg.get('text') or ''
    if text.startswith('/'):
        on_command(text.split()[0].split('@')[0])
        return
    voice = msg.get('voice') or msg.get('video_note') or msg.get('audio')
    if voice:
        typing()
        text = transcribe(voice['file_id'])
        if not text:
            c.send('Не расслышал голосовое, попробуй ещё раз.', reply_to=msg['message_id'])
            return
        c.send(f'🎙 <i>{c.esc(text[:1500])}</i>', silent=True, reply_to=msg['message_id'])
    if text:
        on_text(text, msg)


def main():
    c.log('bot', 'старт')
    while True:
        st = state()
        try:
            ups = c.tg('getUpdates', {'offset': st.get('offset', 0), 'timeout': 25,
                                      'allowed_updates': '["message","callback_query"]'}, timeout=40)
        except Exception as e:
            c.log('bot', f'getUpdates: {str(e)[:200]}')
            time.sleep(10)  # сеть пропала — ждём и пробуем снова, не падаем
            continue
        for u in ups:
            set_state(offset=u['update_id'] + 1)
            try:
                handle(u)
            except Exception as e:
                c.log('bot', f'ошибка обработки: {str(e)[:300]}')
                try:
                    c.send(f'⚠️ Не получилось обработать: {c.esc(str(e)[:200])}')
                except Exception:
                    pass


if __name__ == '__main__':
    main()
