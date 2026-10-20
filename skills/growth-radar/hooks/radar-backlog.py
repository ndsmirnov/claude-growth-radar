#!/usr/bin/env python3
"""SessionStart-хук Claude Code: напомнить об открытых просьбах доработать «Радар роста». Молчит, если их нет."""
import json, os
p = os.path.expanduser(os.environ.get('RADAR_DIR', '~/growth-radar') + '/backlog.md')
try:
    items = [l.strip() for l in open(p) if l.startswith('- [ ]')]
except OSError:
    items = []
if items:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext":
        f"📡 Радар роста: {len(items)} открытых просьб доработать бота:\n" + "\n".join(items[:8])
        + "\nСказать владельцу одной строкой и предложить сделать. Сделано — отметить [x] с датой в backlog.md."}}, ensure_ascii=False))
