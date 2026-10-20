"""Установка/снятие «Радара роста» в launchd.
  python3 -I install.py install    — бот (всегда), подборка 7:45 ежедневно, недельный разбор ВС 19:00
  python3 -I install.py uninstall  — снять всё
  python3 -I install.py status     — что запущено"""
import os
import plistlib
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable  # тот python, которым запущен install.py
LOGDIR = os.path.expanduser('~/Library/Logs/growth-radar')
UID = os.getuid()

AGENTS = {
    'com.growth-radar.bot': {'ProgramArguments': [PY, '-I', f'{ROOT}/bot.py'],
                                    'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 30},
    # caffeinate: после автопробуждения (pmset 7:45) Мак не уснёт, пока подборка собирается
    'com.growth-radar.digest': {'ProgramArguments': ['/usr/bin/caffeinate', '-i', PY, '-I', f'{ROOT}/digest.py', '--scheduled'],
                                       'StartCalendarInterval': [{'Hour': 7, 'Minute': 45}, {'Hour': 10, 'Minute': 0}]},
    'com.growth-radar.weekly': {'ProgramArguments': [PY, '-I', f'{ROOT}/weekly.py'],
                                       'StartCalendarInterval': {'Weekday': 0, 'Hour': 19, 'Minute': 0}},
}


def plist_path(label):
    return os.path.expanduser(f'~/Library/LaunchAgents/{label}.plist')


def put(label, spec):
    os.makedirs(LOGDIR, exist_ok=True)
    d = {'Label': label, **spec, 'WorkingDirectory': ROOT,
         'EnvironmentVariables': {'PATH': '/usr/bin:/bin:/opt/homebrew/bin', 'LANG': 'ru_RU.UTF-8'},
         'StandardOutPath': f'{LOGDIR}/{label.split(".")[-1]}.out', 'StandardErrorPath': f'{LOGDIR}/{label.split(".")[-1]}.out'}
    with open(plist_path(label), 'wb') as f:
        plistlib.dump(d, f)
    subprocess.run(['launchctl', 'bootout', f'gui/{UID}/{label}'], capture_output=True)
    r = subprocess.run(['launchctl', 'bootstrap', f'gui/{UID}', plist_path(label)], capture_output=True, text=True)
    print(label, 'установлен' if r.returncode == 0 else f'ОШИБКА: {r.stderr.strip()}')


def drop(label):
    subprocess.run(['launchctl', 'bootout', f'gui/{UID}/{label}'], capture_output=True)
    if os.path.exists(plist_path(label)):
        os.remove(plist_path(label))
    print(label, 'снят')


if __name__ == '__main__':
    a = sys.argv[1:2]
    if a == ['install']:
        for k, v in AGENTS.items():
            put(k, v)
    elif a == ['uninstall']:
        for k in AGENTS:
            drop(k)
    elif a == ['status']:
        out = subprocess.run(['launchctl', 'list'], capture_output=True, text=True).stdout
        print('\n'.join(ln for ln in out.splitlines() if 'growth-radar' in ln) or 'ничего не запущено')
    else:
        print(__doc__)
