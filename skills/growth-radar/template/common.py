"""Общее для «Радара роста»: пути, секреты, Telegram, Claude, файлы состояния.
Секреты — только в .env (права 600), в логи не попадают."""
import datetime as dt
import html
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = f'{ROOT}/data'
ENV = f'{ROOT}/.env'
LOGDIR = os.path.expanduser('~/Library/Logs/growth-radar')
CLAUDE = shutil.which('claude') or os.path.expanduser('~/.local/bin/claude')
MODEL = 'sonnet'
VECTOR_MD = f'{ROOT}/vector.md'        # видение, карта прогресса, журнал недель (можно перенести в Obsidian)
LIBRARY_MD = f'{ROOT}/library.md'      # «отложка»: что сам(а) сохранял(а), чтобы вернуться (необязательно)

P_STATE = f'{DATA}/state.json'          # offset, chat_id, paused
P_ITEMS = f'{DATA}/items.json'          # message_id -> пункт подборки
P_FEEDBACK = f'{DATA}/feedback.jsonl'   # вся обратная связь, по строке
P_PROGRESS = f'{DATA}/progress.json'    # уровни тем вектора + история
P_PREFS = f'{ROOT}/preferences.md'      # что владельцу заходит / не заходит (дообучается)
P_PROFILE = f'{ROOT}/profile.md'
P_ROADMAP = f'{ROOT}/roadmap.json'
P_FEEDS = f'{ROOT}/feeds.json'
P_SEEN = f'{DATA}/seen_urls.json'
P_BACKLOG = f'{ROOT}/backlog.md'      # просьбы владельца доработать самого бота

os.makedirs(DATA, exist_ok=True)
os.makedirs(f'{DATA}/sent', exist_ok=True)
os.makedirs(LOGDIR, exist_ok=True)


def now():
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M')


def today():
    return dt.date.today().isoformat()


def log(name, msg):
    with open(f'{LOGDIR}/{name}.log', 'a') as f:
        f.write(f'{now()} {msg}\n')


def load(p, default):
    try:
        with open(p) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(p, obj):
    tmp = p + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def read(p, default=''):
    try:
        with open(p) as f:
            return f.read()
    except FileNotFoundError:
        return default


def env():
    d = {}
    for ln in read(ENV).splitlines():
        if '=' in ln and not ln.lstrip().startswith('#'):
            k, v = ln.split('=', 1)
            d[k.strip()] = v.strip()
    return d


def set_env(key, val):
    lines = [ln for ln in read(ENV).splitlines() if not ln.startswith(key + '=')]
    lines.append(f'{key}={val}')
    fd = os.open(ENV + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    os.replace(ENV + '.tmp', ENV)


def esc(s):
    return html.escape(str(s or ''), quote=False)


# ---------- Telegram ----------

def tg(method, params=None, timeout=30):
    tok = env().get('TG_TOKEN')
    if not tok:
        raise RuntimeError('нет TG_TOKEN в .env')
    p = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
         for k, v in (params or {}).items() if v is not None}
    data = urllib.parse.urlencode(p).encode()
    try:
        with urllib.request.urlopen(f'https://api.telegram.org/bot{tok}/{method}', data=data, timeout=timeout) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:  # 4xx: вернуть текст ошибки Telegram, а не голый HTTPError
        raise RuntimeError(f'telegram {method}: {e.code} {e.read().decode(errors="replace")[:300]}')
    if not res.get('ok'):
        raise RuntimeError(f'telegram {method}: {res}')
    return res['result']


def chat_id():
    return env().get('TG_CHAT_ID')


def send(text, buttons=None, silent=False, reply_to=None):
    """Отправить HTML-сообщение. buttons — список рядов [(текст, callback_data)]."""
    markup = None
    if buttons:
        markup = {'inline_keyboard': [[{'text': t, 'callback_data': c} for t, c in row] for row in buttons]}
    parts = split_text(text)
    msg = None
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        msg = tg('sendMessage', {'chat_id': chat_id(), 'text': part, 'parse_mode': 'HTML',
                                 'disable_web_page_preview': 'true',
                                 'disable_notification': 'true' if silent else None,
                                 'reply_to_message_id': reply_to if i == 0 else None,
                                 'reply_markup': markup if last else None})
    return msg


def split_text(text, limit=3900):
    if len(text) <= limit:
        return [text]
    out, cur = [], ''
    for para in text.split('\n\n'):
        if len(cur) + len(para) + 2 > limit and cur:
            out.append(cur)
            cur = ''
        cur = (cur + '\n\n' + para) if cur else para
    if cur:
        out.append(cur)
    return out


# ---------- Claude (подписка, без CLAUDE.md, хуков и инструментов) ----------

def claude(system, prompt, schema=None, timeout=420, model=MODEL):
    """Один вызов claude -p. Возвращает dict (если schema) или текст."""
    cmd = [CLAUDE, '-p', '--model', model, '--output-format', 'json', '--tools', '',
           '--strict-mcp-config', '--no-session-persistence', '--setting-sources', '',
           '--system-prompt', system]
    if schema:
        cmd += ['--json-schema', json.dumps(schema, ensure_ascii=False)]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout,
                       cwd='/private/tmp', env={**os.environ, 'PATH': '/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin'})
    if r.returncode != 0:
        raise RuntimeError(f'claude rc={r.returncode}: {(r.stderr or r.stdout)[-500:]}')
    d = json.loads(r.stdout)
    if d.get('is_error'):
        raise RuntimeError(f'claude error: {str(d.get("result"))[:500]}')
    u = d.get('usage', {})
    log('claude', f'in={u.get("input_tokens")} cache={u.get("cache_creation_input_tokens")}/'
                  f'{u.get("cache_read_input_tokens")} out={u.get("output_tokens")} cost=${d.get("total_cost_usd")}')
    if schema:
        so = d.get('structured_output')
        if so is None:
            so = parse_json(d.get('result', ''))
        return so
    return d.get('result', '')


def parse_json(s):
    s = s.strip()
    if s.startswith('```'):
        s = s.split('\n', 1)[1].rsplit('```', 1)[0]
    i, j = s.find('{'), s.rfind('}')
    return json.loads(s[i:j + 1])


# ---------- Вектор ----------

LEVELS = ['не трогал(а)', 'знаю слова', 'понимаю на своём примере', 'применил(а) сам(а)', 'могу объяснить и показать']


def roadmap():
    return load(P_ROADMAP, {})


def progress():
    rm = roadmap()
    pr = load(P_PROGRESS, {'topics': {}, 'history': []})
    for tr in rm.get('tracks', []):
        for t in tr['topics']:
            pr['topics'].setdefault(t['id'], {'level': t.get('start', 0), 'note': '', 'updated': ''})
    return pr


def set_level(pr, tid, level, reason, source):
    if tid not in pr['topics']:
        return False
    level = max(0, min(4, int(level)))
    old = pr['topics'][tid]['level']
    if level == old:
        return False
    pr['topics'][tid].update(level=level, note=reason, updated=today())
    pr['history'].append({'date': today(), 'topic': tid, 'from': old, 'to': level, 'reason': reason, 'source': source})
    return True


def vector_text(pr=None, compact=False):
    """Карта вектора: треки, уровни тем, процент."""
    pr = pr or progress()
    rm = roadmap()
    lines = []
    total = got = 0
    for tr in rm['tracks']:
        tl = [pr['topics'][t['id']]['level'] for t in tr['topics']]
        pct = round(100 * sum(tl) / (4 * len(tl)))
        total += 4 * len(tl)
        got += sum(tl)
        lines.append(f'\n<b>{esc(tr["emoji"])} {esc(tr["title"])}</b> — {pct}%')
        if not compact:
            for t in tr['topics']:
                lv = pr['topics'][t['id']]['level']
                lines.append(f'{"●" * lv}{"○" * (4 - lv)} {t["id"]} {esc(t["title"])}')
    head = f'<b>Вектор: {esc(rm.get("title", "мой рост"))}</b> — общий прогресс {round(100 * got / total)}%'
    return head + '\n' + '\n'.join(lines)


def phase_now():
    """Текущая фаза погружения по общему прогрессу и дате старта."""
    rm = roadmap()
    st = load(P_STATE, {})
    start = st.get('started') or today()
    weeks = (dt.date.today() - dt.date.fromisoformat(start)).days // 7 + 1
    cur = rm['phases'][0]
    for ph in rm['phases']:
        if weeks >= ph['from_week']:
            cur = ph
    return cur, weeks


def recent_feedback(days=14, limit=60):
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    out = []
    for ln in read(P_FEEDBACK).splitlines():
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if r.get('date', '') >= cutoff:
            out.append(r)
    return out[-limit:]


def add_feedback(rec):
    rec.setdefault('date', today())
    rec.setdefault('ts', now())
    with open(P_FEEDBACK, 'a') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
