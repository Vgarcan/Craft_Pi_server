#!/usr/bin/env python3
"""Panel web de Craft Pi Server, sólo para la LAN.

Proceso sin privilegios (usuario mclan-panel). Toda operación sobre el servidor pasa por
helper.py mediante una regla sudo limitada a ese archivo; este proceso nunca habla con Docker.
Sin dependencias externas ni JavaScript: HTML generado en el servidor sobre HTTPS.
"""
import argparse
import hashlib
import hmac
import html
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit

HELPER = ['sudo', '-n', '/usr/bin/python3', '-I', str(Path(__file__).resolve().with_name('helper.py'))]
SESSION_TTL = 12 * 3600
MAX_BODY = 16 * 1024
FAILURE_WINDOW, MAX_FAILURES = 600, 5
COOKIE = 'mclan_session'
ITERATIONS = 300_000


def hash_password(password, salt=None, iterations=ITERATIONS):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations)
    return {'algorithm': 'pbkdf2-sha256', 'iterations': iterations, 'salt': salt.hex(), 'hash': digest.hex()}


def check_password(password, record):
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(record['salt']), int(record['iterations']))
    return hmac.compare_digest(digest.hex(), record['hash'])


def write_config(path, config):
    fd, name = tempfile.mkstemp(prefix='.panel-', dir=path.parent)
    with os.fdopen(fd, 'w') as out:
        json.dump(config, out, indent=2)
    os.chmod(name, 0o600)
    os.replace(name, path)


def e(value):
    return html.escape(str(value), quote=True)


EDITIONS = {'java': 'Java', 'bedrock': 'Bedrock', 'xuid': 'Bedrock (XUID)'}
ACTIONS = {'start': 'Arrancar', 'stop': 'Parar', 'restart': 'Reiniciar', 'backup': 'Backup'}
PROPERTY_FORM = [
    ('motd', 'Mensaje (MOTD)', 'text', None),
    ('difficulty', 'Dificultad', 'select', [('peaceful', 'Pacífico'), ('easy', 'Fácil'), ('normal', 'Normal'), ('hard', 'Difícil')]),
    ('gamemode', 'Modo de juego', 'select', [('survival', 'Supervivencia'), ('creative', 'Creativo'),
                                             ('adventure', 'Aventura'), ('spectator', 'Espectador')]),
    ('max-players', 'Jugadores máximos', 'number', (1, 20)),
    ('view-distance', 'Distancia de visión (chunks)', 'number', (3, 12)),
    ('simulation-distance', 'Distancia de simulación (chunks)', 'number', (3, 10)),
    ('pvp', 'PvP', 'select', [('true', 'Activado'), ('false', 'Desactivado')]),
    ('allow-flight', 'Permitir vuelo', 'select', [('true', 'Sí'), ('false', 'No')]),
    ('spawn-protection', 'Protección del spawn (bloques)', 'number', (0, 64)),
]

STYLE = '''
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--muted:#5d6675;--line:#dde1e7;--accent:#2f6f4f;--accent-fg:#fff;
--warn-bg:#fff6dc;--warn-fg:#6b4e00;--bad-bg:#fde8e8;--bad-fg:#8a1c1c;--ok-bg:#e6f4ea;--ok-fg:#1e5c33;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--card:#1d2128;--fg:#e6e9ee;--muted:#9aa3b0;--line:#323844;
--accent:#5fb487;--accent-fg:#0d1a12;--warn-bg:#3a3016;--warn-fg:#f3d48a;--bad-bg:#3b1d1d;--bad-fg:#f4b4b4;
--ok-bg:#1b3325;--ok-fg:#a6dcb8;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px;display:flex;flex-wrap:wrap;gap:8px 18px;align-items:center}
header strong{font-size:17px;margin-right:auto}nav{display:flex;flex-wrap:wrap;gap:4px 14px}
a{color:var(--accent)}nav a{text-decoration:none;font-weight:600}nav a.on{text-decoration:underline}
main{max-width:980px;margin:0 auto;padding:16px}h1{font-size:22px;margin:4px 0 14px}h2{font-size:17px;margin:0 0 10px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:14px}
.muted{color:var(--muted)}.big{font-size:22px;font-weight:700}
.msg{border-radius:8px;padding:10px 12px;margin-bottom:12px}.ok{background:var(--ok-bg);color:var(--ok-fg)}
.warn{background:var(--warn-bg);color:var(--warn-fg)}.bad{background:var(--bad-bg);color:var(--bad-fg)}
table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.scroll{overflow-x:auto}pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;overflow-x:auto;
font-size:12.5px;line-height:1.45;white-space:pre-wrap;word-break:break-word;max-height:70vh}
form.inline{display:inline}button,input,select{font:inherit;padding:7px 10px;border-radius:7px;border:1px solid var(--line);
background:var(--card);color:var(--fg)}button{cursor:pointer;font-weight:600}button.primary{background:var(--accent);
color:var(--accent-fg);border-color:var(--accent)}button.danger{border-color:var(--bad-fg);color:var(--bad-fg)}
label{display:block;margin:8px 0 3px;font-weight:600}.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.pill{display:inline-block;padding:1px 9px;border-radius:99px;font-size:13px;font-weight:600}
input[type=text],input[type=password],input[type=number],select{width:100%;max-width:420px}
'''


class Jobs:
    """Una operación larga a la vez (backup, reinicio, ajustes, apertura temporal)."""

    def __init__(self, runner):
        self.runner = runner
        self.items = {}
        self.lock = threading.Lock()

    def busy(self):
        return any(job['state'] == 'running' for job in self.items.values())

    def start(self, label, argv):
        with self.lock:
            if self.busy():
                return None
            identifier = secrets.token_hex(6)
            job = {'id': identifier, 'label': label, 'state': 'running', 'started': time.time(), 'result': None}
            self.items[identifier] = job
            for old in sorted(self.items, key=lambda k: self.items[k]['started'])[:-10]:
                del self.items[old]
        def work():
            job['result'] = self.runner(argv, timeout=7200)
            job['state'] = 'done'
        threading.Thread(target=work, daemon=True).start()
        return identifier


class App:
    def __init__(self, config, config_path=None, helper=HELPER):
        self.config, self.config_path, self.helper_argv = config, config_path, helper
        self.sessions, self.failures = {}, {}
        self.lock = threading.Lock()
        self.jobs = Jobs(self.helper)

    # --- Infraestructura -----------------------------------------------------------------
    def helper(self, argv, timeout=60):
        try:
            result = subprocess.run([*self.helper_argv, *argv], capture_output=True, text=True,
                                    timeout=timeout, check=False, stdin=subprocess.DEVNULL)
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (subprocess.SubprocessError, json.JSONDecodeError, IndexError, OSError) as exc:
            return {'ok': False, 'output': f'No se pudo ejecutar la operación ({type(exc).__name__}).'}

    def session(self, headers):
        cookie = headers.get('Cookie', '')
        match = re.search(rf'(?:^|;\s*){COOKIE}=([A-Za-z0-9_-]{{20,}})', cookie)
        if not match:
            return None
        with self.lock:
            session = self.sessions.get(match[1])
            if session and session['expires'] > time.time():
                return session
            self.sessions.pop(match[1], None)
        return None

    def throttled(self, ip):
        now = time.time()
        recent = [t for t in self.failures.get(ip, []) if now - t < FAILURE_WINDOW]
        self.failures[ip] = recent
        return len(recent) >= MAX_FAILURES

    def origin_ok(self, headers):
        """Defensa adicional a SameSite/CSRF: un Origin presente debe ser este mismo panel."""
        origin = headers.get('Origin')
        return origin is None or origin == f"https://{self.config['bind']}:{self.config['port']}"

    # --- HTML ----------------------------------------------------------------------------
    def page(self, title, body, session=None, active='', refresh=None):
        links = [('/', 'Estado'), ('/whitelist', 'Whitelist'), ('/ajustes', 'Ajustes'), ('/logs', 'Logs'),
                 ('/auditoria', 'Auditoría'), ('/mapa', 'Mapa'), ('/cuenta', 'Cuenta')]
        nav = ''
        if session:
            nav = '<nav>' + ''.join(f'<a href="{href}" class="{"on" if href == active else ""}">{label}</a>'
                                    for href, label in links)
            nav += (f'<form class="inline" method="post" action="/logout"><input type="hidden" name="csrf" '
                    f'value="{e(session["csrf"])}"><button>Salir</button></form></nav>')
        flashes = ''.join(f'<div class="msg {e(kind)}">{e(text)}</div>' for kind, text in (session or {}).pop('flash', []))
        meta = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ''
        return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" '
                f'content="width=device-width,initial-scale=1">{meta}<title>{e(title)} · Craft Pi</title>'
                f'<style>{STYLE}</style></head><body><header><strong>Craft Pi Server</strong>{nav}</header>'
                f'<main>{flashes}<h1>{e(title)}</h1>{body}</main></body></html>')

    def csrf(self, session):
        return f'<input type="hidden" name="csrf" value="{e(session["csrf"])}">'

    def flash(self, session, kind, text):
        session.setdefault('flash', []).append((kind, text))

    def result_flash(self, session, result, success):
        output = (result.get('output') or '').strip()
        if result.get('ok'):
            self.flash(session, 'ok', f'{success} {output}'.strip())
        else:
            self.flash(session, 'bad', output or 'La operación falló.')

    # --- Rutas ---------------------------------------------------------------------------
    def handle(self, method, target, headers, body, ip):
        path = urlsplit(target).path
        query = parse_qs(urlsplit(target).query)
        form = {k: v[0] for k, v in parse_qs(body.decode('utf-8', 'replace'), keep_blank_values=True).items()} if body else {}
        if path == '/login':
            return self.login(method, form, ip)
        session = self.session(headers)
        if not session:
            return 303, {'Location': '/login'}, b''
        if method == 'POST' and (not hmac.compare_digest(form.get('csrf', ''), session['csrf']) or not self.origin_ok(headers)):
            return 403, {}, self.page('Petición rechazada', '<p>Recarga la página e inténtalo de nuevo.</p>', session).encode()
        routes = {
            ('GET', '/'): self.dashboard, ('POST', '/accion'): self.action,
            ('GET', '/whitelist'): self.whitelist_page, ('POST', '/whitelist'): self.whitelist_post,
            ('GET', '/ajustes'): self.settings_page, ('POST', '/ajustes'): self.settings_post,
            ('GET', '/logs'): self.logs_page, ('GET', '/auditoria'): self.audit_page,
            ('GET', '/cuenta'): self.account_page, ('POST', '/cuenta'): self.account_post,
            ('POST', '/logout'): self.logout,
        }
        if method == 'GET' and path == '/mapa':
            return 303, {'Location': self.config['map_url']}, b''
        if method == 'GET' and (match := re.fullmatch(r'/tarea/([0-9a-f]{12})', path)):
            return self.job_page(session, match[1])
        handler = routes.get((method, path))
        if not handler:
            return 404, {}, self.page('No encontrado', '<p><a href="/">Volver al estado</a></p>', session).encode()
        return handler(session, form, query)

    def login(self, method, form, ip):
        message = ''
        if method == 'POST':
            if self.throttled(ip):
                return 429, {}, self.page('Demasiados intentos', '<p>Espera unos minutos.</p>').encode()
            if check_password(form.get('password', ''), self.config['password']):
                token = secrets.token_urlsafe(32)
                with self.lock:
                    self.sessions[token] = {'expires': time.time() + SESSION_TTL, 'csrf': secrets.token_urlsafe(24)}
                cookie = f'{COOKIE}={token}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}'
                return 303, {'Location': '/', 'Set-Cookie': cookie}, b''
            self.failures.setdefault(ip, []).append(time.time())
            message = '<div class="msg bad">Contraseña incorrecta.</div>'
        body = (f'<div class="card">{message}<form method="post" action="/login"><label for="p">Contraseña</label>'
                '<input id="p" type="password" name="password" autocomplete="current-password" required autofocus>'
                '<p><button class="primary">Entrar</button></p></form></div>')
        return 200, {}, self.page('Acceso', body).encode()

    def logout(self, session, form, query):
        with self.lock:
            for token, value in list(self.sessions.items()):
                if value is session:
                    del self.sessions[token]
        return 303, {'Location': '/login', 'Set-Cookie': f'{COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0'}, b''

    def dashboard(self, session, form, query):
        status = self.helper(['status'])
        if not status.get('ok'):
            body = f'<div class="msg bad">No se pudo leer el estado: {e(status.get("output", ""))}</div>'
            return 200, {}, self.page('Estado', body, session, '/').encode()
        release, container = status['release'], status['container']
        running = container['running']
        health = {'healthy': ('ok', 'Saludable'), 'starting': ('warn', 'Arrancando'), 'unhealthy': ('bad', 'Con fallos')}.get(
            container.get('health'), ('warn', container.get('health') or 'Sin datos'))
        pill = (f'<span class="pill {"ok" if running else "bad"}">{"En marcha" if running else "Detenido"}</span> '
                f'<span class="pill {health[0]}">{e(health[1])}</span>') if container['exists'] else '<span class="pill bad">Sin contenedor</span>'
        players = status.get('players') or ''
        match = re.search(r'There are (\d+) of a max of (\d+) players online:?\s*(.*)', players)
        online = (f'<div class="big">{e(match[1])} / {e(match[2])}</div><div class="muted">{e(match[3]) or "Nadie conectado"}</div>'
                  if match else '<div class="muted">Sin datos (servidor detenido o arrancando)</div>')
        memory = container.get('memory')
        mem = (f'{memory["used"] / 2**20:.0f} MiB de {memory["limit"] / 2**30:.1f} GiB' if memory and memory['limit']
               else (f'{memory["used"] / 2**20:.0f} MiB (sin límite)' if memory else 'Sin datos'))
        temp = status['host']['temperature']
        warnings = []
        if status['pending_operation']:
            warnings.append(('bad', 'Hay una operación incompleta. Consulta la guía de recuperación antes de arrancar.'))
        if status['project_drift']:
            warnings.append(('warn', 'El proyecto tiene código Docker/Compose nuevo: ejecuta manage.sh update en la Pi.'))
        if not status['host']['memory_limits']:
            warnings.append(('warn', 'El kernel no aplica el límite de memoria del contenedor.'))
        if status['properties']['white-list'] == 'true' and not status['whitelist']:
            warnings.append(('warn', 'La whitelist está activa y vacía: nadie puede entrar.'))
        if status['properties']['white-list'] != 'true':
            warnings.append(('warn', 'La whitelist está desactivada: puede entrar cualquier cuenta válida de la LAN.'))
        if status['rejected']:
            warnings.append(('warn', f'{len(status["rejected"])} intento(s) rechazado(s) por la whitelist. Revísalos en Whitelist.'))
        ip = e(release['lan_ip'])
        actions = ''.join(
            f'<form class="inline" method="post" action="/accion">{self.csrf(session)}<input type="hidden" name="do" value="{verb}">'
            f'<button class="{"danger" if verb in ("stop", "restart") else ""}">{label}</button></form> '
            for verb, label in ACTIONS.items())
        backups = ''.join(f'<tr><td>{e(b["name"])}</td><td>{b["size"] / 2**20:.0f} MiB</td>'
                          f'<td>{time.strftime("%d-%m-%Y %H:%M", time.localtime(b["mtime"]))}</td></tr>'
                          for b in status['backups'][:5]) or '<tr><td colspan="3" class="muted">Sin backups</td></tr>'
        body = (''.join(f'<div class="msg {k}">{e(t)}</div>' for k, t in warnings) +
                f'<div class="grid"><div class="card"><h2>Servidor</h2><p>{pill}</p>'
                f'<p class="muted">Reinicios del contenedor: {e(container.get("restarts"))}</p></div>'
                f'<div class="card"><h2>Jugadores</h2>{online}</div>'
                f'<div class="card"><h2>Recursos</h2><p>Memoria: {e(mem)}<br>Temperatura: '
                f'{e(f"{temp:.1f} °C" if temp is not None else "sin datos")}</p></div></div>'
                f'<div class="card"><h2>Cómo conectarse</h2><div class="scroll"><table>'
                f'<tr><th>Java {e(release["minecraft"])}</th><td>{ip}:25565</td></tr>'
                f'<tr><th>Bedrock {e(release["bedrock"])}</th><td>{ip}, puerto 19132</td></tr>'
                f'<tr><th>Mapa</th><td><a href="/mapa">{e(self.config["map_url"])}</a></td></tr></table></div></div>'
                f'<div class="card"><h2>Acciones</h2><p class="muted">Parar o reiniciar desconecta a los jugadores. '
                f'Backup para el servidor un minuto y lo vuelve a arrancar.</p><div class="row">{actions}</div></div>'
                f'<div class="card"><h2>Versiones</h2><p class="muted">Paper {e(release["paper"])} · Geyser {e(release["geyser"])} · '
                f'Floodgate {e(release["floodgate"])} · BlueMap {e(release["bluemap"] or "no instalado")} · release {e(release["id"])}</p></div>'
                f'<div class="card"><h2>Últimos backups</h2><div class="scroll"><table>{backups}</table></div>'
                f'<p class="muted">Están en la misma microSD: copia periódicamente alguno fuera de la Pi.</p></div>')
        return 200, {}, self.page('Estado', body, session, '/').encode()

    def run_job(self, session, label, argv):
        identifier = self.jobs.start(label, argv)
        if not identifier:
            self.flash(session, 'bad', 'Ya hay otra operación en curso. Espera a que termine.')
            return 303, {'Location': '/'}, b''
        return 303, {'Location': f'/tarea/{identifier}'}, b''

    def action(self, session, form, query):
        verb = form.get('do')
        if verb not in ACTIONS:
            return 400, {}, b''
        return self.run_job(session, ACTIONS[verb], [verb])

    def job_page(self, session, identifier):
        job = self.jobs.items.get(identifier)
        if not job:
            return 404, {}, self.page('Tarea no encontrada', '<p><a href="/">Volver</a></p>', session).encode()
        elapsed = int(time.time() - job['started'])
        if job['state'] == 'running':
            body = (f'<div class="card"><p class="big">En curso… {elapsed} s</p><p class="muted">La página se actualiza sola. '
                    'Puedes cerrarla: la operación continúa en la Pi.</p></div>')
            return 200, {}, self.page(job['label'], body, session, refresh=3).encode()
        result = job['result'] or {}
        kind, text = ('ok', 'Terminado correctamente.') if result.get('ok') else ('bad', 'La operación falló.')
        body = (f'<div class="msg {kind}">{text}</div><div class="card"><pre>{e(result.get("output", ""))}</pre>'
                '<p><a href="/">Volver al estado</a></p></div>')
        return 200, {}, self.page(job['label'], body, session).encode()

    def whitelist_page(self, session, form, query):
        status = self.helper(['status'])
        if not status.get('ok'):
            return 200, {}, self.page('Whitelist', f'<div class="msg bad">{e(status.get("output"))}</div>', session, '/whitelist').encode()
        enabled = status['properties']['white-list'] == 'true'
        def post(fields, label, cls=''):
            hidden = ''.join(f'<input type="hidden" name="{e(k)}" value="{e(v)}">' for k, v in fields.items())
            return f'<form class="inline" method="post" action="/whitelist">{self.csrf(session)}{hidden}<button class="{cls}">{label}</button></form>'
        rows = ''.join(f'<tr><td>{e(p["name"])}</td><td>{EDITIONS.get(p["edition"], p["edition"])}</td>'
                       f'<td class="muted">{e(p["uuid"])}</td><td>{post({"do": "remove", "name": p["name"]}, "Quitar", "danger")}</td></tr>'
                       for p in status['whitelist']) or '<tr><td colspan="4" class="muted">Nadie en la whitelist</td></tr>'
        rejected = ''.join(
            f'<tr><td>{e(r["name"])}</td><td>{EDITIONS[r["edition"]]}</td><td class="muted">{e(r["last_seen"])}</td><td>'
            f'{post({"do": "add", "edition": r["edition"], "name": r["name"]}, "Permitir", "primary")}</td></tr>'
            for r in status['rejected']) or '<tr><td colspan="4" class="muted">Sin intentos rechazados recientes</td></tr>'
        switch = post({'do': 'switch', 'value': 'off' if enabled else 'on'},
                      'Desactivar whitelist' if enabled else 'Activar whitelist', 'danger' if enabled else 'primary')
        options = ''.join(f'<option value="{k}">{v}</option>' for k, v in EDITIONS.items())
        state = ('<span class="pill ok">Activa</span> Sólo entran los jugadores de la lista.' if enabled else
                 '<span class="pill bad">Desactivada</span> Puede entrar cualquier cuenta válida que llegue a la Pi.')
        body = (f'<div class="card"><h2>Estado</h2><p>{state}</p>{switch}</div>'
                f'<div class="card"><h2>Jugadores admitidos</h2><div class="scroll"><table><tr><th>Nombre</th><th>Edición</th>'
                f'<th>UUID</th><th></th></tr>{rows}</table></div><p class="muted">Los jugadores Bedrock aparecen con un punto delante.</p></div>'
                f'<div class="card"><h2>Intentos rechazados</h2><div class="scroll"><table><tr><th>Nombre</th><th>Edición</th>'
                f'<th>Último intento</th><th></th></tr>{rejected}</table></div></div>'
                f'<div class="card"><h2>Añadir jugador</h2><form method="post" action="/whitelist">{self.csrf(session)}'
                f'<input type="hidden" name="do" value="add"><label for="ed">Edición</label><select id="ed" name="edition">{options}</select>'
                '<label for="nm">Nombre o XUID</label><input id="nm" type="text" name="name" maxlength="20" required '
                'pattern="[A-Za-z0-9_]{1,20}"><p class="muted">Java: nombre de la cuenta. Bedrock: gamertag sin espacios '
                '(usa _ en su lugar). Si Floodgate no encuentra el gamertag, usa su XUID o la apertura temporal.</p>'
                '<p><button class="primary">Añadir</button></p></form></div>'
                f'<div class="card"><h2>Apertura temporal para un jugador</h2><form method="post" action="/whitelist">{self.csrf(session)}'
                '<input type="hidden" name="do" value="open"><label for="on">Gamertag o nombre</label>'
                '<input id="on" type="text" name="name" maxlength="16" required pattern="[A-Za-z0-9_]{1,16}">'
                '<label for="mi">Minutos (máximo 15)</label><input id="mi" type="number" name="minutes" min="1" max="15" value="5">'
                '<p class="muted">Desactiva la whitelist hasta que ese jugador entre. Entonces lo añade y vuelve a activarla; '
                'si no entra a tiempo, la reactiva sin añadir a nadie. Mientras tanto puede entrar cualquier cuenta válida.</p>'
                '<p><button>Abrir y esperar</button></p></form></div>')
        return 200, {}, self.page('Whitelist', body, session, '/whitelist').encode()

    def whitelist_post(self, session, form, query):
        action = form.get('do')
        name = form.get('name', '').strip()
        if action == 'add' and form.get('edition') in EDITIONS:
            result = self.helper(['whitelist-add', form['edition'], name], timeout=60)
            self.result_flash(session, result, 'Jugador añadido.')
        elif action == 'remove':
            self.result_flash(session, self.helper(['whitelist-remove', name]), 'Jugador quitado.')
        elif action == 'switch' and form.get('value') in ('on', 'off'):
            self.result_flash(session, self.helper(['whitelist', form['value']]),
                              'Whitelist activada.' if form['value'] == 'on' else 'Whitelist desactivada.')
        elif action == 'open':
            return self.run_job(session, f'Esperando a {name}', ['whitelist-open-for', name, form.get('minutes', '5')])
        else:
            return 400, {}, b''
        return 303, {'Location': '/whitelist'}, b''

    def settings_page(self, session, form, query):
        status = self.helper(['status'])
        if not status.get('ok'):
            return 200, {}, self.page('Ajustes', f'<div class="msg bad">{e(status.get("output"))}</div>', session, '/ajustes').encode()
        current = status['properties']
        fields = ''
        for key, label, kind, extra in PROPERTY_FORM:
            value = current.get(key, '')
            if kind == 'select':
                options = ''.join(f'<option value="{v}"{" selected" if v == value else ""}>{t}</option>' for v, t in extra)
                control = f'<select id="{key}" name="{key}">{options}</select>'
            elif kind == 'number':
                control = f'<input id="{key}" type="number" name="{key}" min="{extra[0]}" max="{extra[1]}" value="{e(value)}" required>'
            else:
                control = f'<input id="{key}" type="text" name="{key}" maxlength="59" value="{e(value)}" required>'
            fields += f'<label for="{key}">{label}</label>{control}'
        body = (f'<div class="card"><form method="post" action="/ajustes">{self.csrf(session)}{fields}'
                '<p class="muted">Guardar para el servidor, aplica los cambios en server.properties y lo vuelve a arrancar: '
                'los jugadores conectados se desconectan. Autenticación, puertos y RCON no se editan desde aquí.</p>'
                '<p><button class="primary">Guardar y reiniciar</button></p></form></div>')
        return 200, {}, self.page('Ajustes', body, session, '/ajustes').encode()

    def settings_post(self, session, form, query):
        status = self.helper(['status'])
        if not status.get('ok'):
            self.flash(session, 'bad', status.get('output', 'Sin estado'))
            return 303, {'Location': '/ajustes'}, b''
        changes = [f'{key}={form[key]}' for key, *_ in PROPERTY_FORM
                   if key in form and form[key] != status['properties'].get(key, '')]
        if not changes:
            self.flash(session, 'warn', 'No has cambiado ningún ajuste.')
            return 303, {'Location': '/ajustes'}, b''
        return self.run_job(session, 'Aplicar ajustes', ['set-properties', *changes])

    def logs_page(self, session, form, query):
        lines = query.get('lineas', ['200'])[0]
        lines = lines if lines in ('100', '200', '500') else '200'
        result = self.helper(['logs', lines])
        choices = ' · '.join(f'<a href="/logs?lineas={n}">{n}</a>' for n in ('100', '200', '500'))
        body = (f'<div class="card"><p class="muted">Últimas {lines} líneas ({choices}). '
                f'<a href="/logs?lineas={lines}">Actualizar</a></p><pre>{e(result.get("log", result.get("output", "")))}</pre></div>')
        return 200, {}, self.page('Logs del servidor', body, session, '/logs').encode()

    def audit_page(self, session, form, query):
        result = self.helper(['audit'], timeout=180)
        kind, text = ('ok', 'Sin fallos.') if result.get('ok') else ('bad', 'La auditoría encontró fallos o no pudo completarse.')
        body = f'<div class="msg {kind}">{text}</div><div class="card"><pre>{e(result.get("output", ""))}</pre></div>'
        return 200, {}, self.page('Auditoría', body, session, '/auditoria').encode()

    def account_page(self, session, form, query):
        body = (f'<div class="card"><form method="post" action="/cuenta">{self.csrf(session)}'
                '<label for="c">Contraseña actual</label><input id="c" type="password" name="current" autocomplete="current-password" required>'
                '<label for="n">Nueva contraseña (mínimo 12 caracteres)</label><input id="n" type="password" name="new" minlength="12" autocomplete="new-password" required>'
                '<label for="r">Repite la nueva</label><input id="r" type="password" name="repeat" minlength="12" autocomplete="new-password" required>'
                '<p><button class="primary">Cambiar contraseña</button></p></form>'
                '<p class="muted">Al cambiarla se cierran las demás sesiones abiertas.</p></div>')
        return 200, {}, self.page('Cuenta', body, session, '/cuenta').encode()

    def account_post(self, session, form, query):
        new = form.get('new', '')
        if not check_password(form.get('current', ''), self.config['password']):
            self.flash(session, 'bad', 'La contraseña actual no es correcta.')
        elif len(new) < 12 or new != form.get('repeat'):
            self.flash(session, 'bad', 'La nueva contraseña debe tener al menos 12 caracteres y coincidir en ambos campos.')
        else:
            self.config['password'] = hash_password(new)
            if self.config_path:
                write_config(self.config_path, self.config)
                # La contraseña inicial generada por install.sh deja de ser válida.
                (self.config_path.parent / 'initial-password').unlink(missing_ok=True)
            with self.lock:
                for token in [t for t, s in self.sessions.items() if s is not session]:
                    del self.sessions[token]
            self.flash(session, 'ok', 'Contraseña cambiada.')
        return 303, {'Location': '/cuenta'}, b''


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'CraftPiPanel'
    sys_version = ''
    timeout = 30

    def respond(self, method):
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_BODY:
            self.send_error(413)
            return
        body = self.rfile.read(length) if length else b''
        status, headers, payload = self.server.app.handle(method, self.path, self.headers, body, self.client_address[0])
        self.send_response(status)
        defaults = {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
                    'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                                               "frame-ancestors 'none'; base-uri 'none'",
                    'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY'}
        for key, value in {**defaults, **headers}.items():
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self.respond('GET')

    def do_POST(self):
        self.respond('POST')

    def log_message(self, fmt, *args):
        # Sin cuerpos ni cookies: sólo método, ruta y código.
        sys.stderr.write(f'{self.client_address[0]} {fmt % args}\n')


def serve(config_path):
    config = json.loads(Path(config_path).read_text())
    app = App(config, Path(config_path))
    server = http.server.ThreadingHTTPServer((config['bind'], int(config['port'])), Handler)
    server.app = app
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config['cert'], config['key'])
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"Panel en https://{config['bind']}:{config['port']}/", file=sys.stderr)
    server.serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    run = sub.add_parser('serve')
    run.add_argument('--config', required=True)
    sub.add_parser('hash-password', help='Lee una contraseña por stdin y escribe su registro JSON')
    args = parser.parse_args(argv)
    if args.mode == 'hash-password':
        password = sys.stdin.readline().rstrip('\n')
        if len(password) < 12:
            print('La contraseña necesita al menos 12 caracteres.', file=sys.stderr)
            return 1
        print(json.dumps(hash_password(password)))
        return 0
    serve(args.config)
    return 0


if __name__ == '__main__':
    sys.exit(main())
