#!/usr/bin/env python3
"""Panel web de Craft Pi Server, sólo para la LAN (interfaz en inglés).

Proceso sin privilegios (usuario mclan-panel). Toda operación sobre el servidor pasa por
helper.py mediante una regla sudo limitada a ese archivo; este proceso nunca habla con Docker.
Sin dependencias externas: HTML generado en el servidor, con /static/app.js como mejora progresiva
(todo funciona también sin JavaScript) y una CSP que no admite código ni estilos en línea.
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
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from views import ACTIONS, EDITIONS, PROPERTY_FORM, Views  # noqa: E402

HELPER = ['sudo', '-n', '/usr/bin/python3', '-I', str(HERE / 'helper.py')]
STATIC = HERE / 'static'
STATIC_TYPES = {'.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.ttf': 'font/ttf'}
# Rutas de la versión anterior (en español) → nuevas, para no romper marcadores.
LEGACY = {'/whitelist': '/players', '/ajustes': '/world', '/logs': '/console', '/auditoria': '/health',
          '/cuenta': '/account', '/mapa': '/map'}
SESSION_TTL = 12 * 3600
MAX_BODY = 16 * 1024
# Subida de mundos: se escribe por bloques en imports/, nunca en memoria. Una a la vez.
MAX_UPLOAD = 2 * 1024**3
UPLOAD_MARGIN = 4 * 1024**3
UPLOAD_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._ -]{0,99}\.(?:zip|mcworld)')
ZIP_MAGIC = (b'PK\x03\x04', b'PK\x05\x06')
UPLOADING = threading.Lock()
FAILURE_WINDOW, MAX_FAILURES = 600, 5
MAX_SESSIONS, MAX_TRACKED_IPS = 20, 1024
MAX_CONNECTIONS, MAX_CONNECTIONS_PER_IP = 32, 8
# __Host-: el navegador sólo la acepta con Secure, Path=/ y sin Domain.
COOKIE = '__Host-mclan_session'
ITERATIONS = 300_000
# PBKDF2 es caro a propósito: dos cálculos simultáneos como mucho para no saturar la Pi.
HASHING = threading.BoundedSemaphore(2)


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
        self.assets_dir = Path(config.get('assets_dir', '/etc/mclan-panel/assets'))
        try:
            assets = {p.name for p in self.assets_dir.glob('*.png')}
        except OSError:
            assets = set()  # sin texturas: el CSS usa sus equivalentes
        digest = hashlib.sha256()
        for name in ('app.css', 'app.js'):
            digest.update((STATIC / name).read_bytes())
        self.views = Views(assets, digest.hexdigest()[:12])

    # --- Infraestructura -----------------------------------------------------------------
    def helper(self, argv, timeout=60):
        try:
            result = subprocess.run([*self.helper_argv, *argv], capture_output=True, text=True,
                                    timeout=timeout, check=False, stdin=subprocess.DEVNULL)
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (subprocess.SubprocessError, json.JSONDecodeError, IndexError, OSError) as exc:
            return {'ok': False, 'output': f'The operation could not run ({type(exc).__name__}).'}

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

    def reserve_attempt(self, ip):
        """Apunta el intento ANTES de calcular el hash: peticiones simultáneas no esquivan el límite."""
        now = time.time()
        with self.lock:
            for key in [k for k, v in self.failures.items() if not v or now - v[-1] >= FAILURE_WINDOW]:
                del self.failures[key]
            recent = [t for t in self.failures.get(ip, []) if now - t < FAILURE_WINDOW]
            if len(recent) >= MAX_FAILURES or (ip not in self.failures and len(self.failures) >= MAX_TRACKED_IPS):
                return None
            recent.append(now)
            self.failures[ip] = recent
            return now

    def release_attempt(self, ip, stamp):
        with self.lock:
            if stamp in self.failures.get(ip, []):
                self.failures[ip].remove(stamp)

    def origin_ok(self, headers):
        """Defensa adicional a SameSite/CSRF: un Origin presente debe ser este mismo panel."""
        origin = headers.get('Origin')
        return origin is None or origin == f"https://{self.config['bind']}:{self.config['port']}"

    def render(self, title, body, session=None, active='', refresh=None, slug='page', status=200):
        flashes = (session or {}).pop('flash', [])
        return status, {}, self.views.page(title, body, session, active, refresh, slug, flashes).encode()

    def flash(self, session, kind, text):
        session.setdefault('flash', []).append((kind, text))

    def result_flash(self, session, result, success):
        output = (result.get('output') or '').strip()
        if result.get('ok'):
            self.flash(session, 'ok', f'{success} {output}'.strip())
        else:
            self.flash(session, 'bad', output or 'The operation failed.')

    @staticmethod
    def json(payload, status=200):
        return status, {'Content-Type': 'application/json'}, json.dumps(payload).encode()

    def static(self, path):
        """Archivos del repositorio (CSS, JS, fuentes OFL) y texturas extraídas en la Pi."""
        if match := re.fullmatch(r'/static/((?:fonts/)?[A-Za-z0-9_-]+\.(?:css|js|ttf))', path):
            source = STATIC / match[1]
            kind = STATIC_TYPES[source.suffix]
        elif match := re.fullmatch(r'/assets/([a-z0-9_]+\.png)', path):
            source = self.assets_dir / match[1]
            kind = 'image/png'
        else:
            return None
        if not source.is_file():
            return 404, {'Content-Type': 'text/plain'}, b'Not found'
        return 200, {'Content-Type': kind, 'Cache-Control': 'public, max-age=3600'}, source.read_bytes()

    # --- Rutas ---------------------------------------------------------------------------
    def handle(self, method, target, headers, body, ip):
        # Contra DNS rebinding: sólo se atiende el nombre con el que se publica el panel.
        if headers.get('Host') not in (f"{self.config['bind']}:{self.config['port']}", self.config['bind']):
            return 421, {}, b'Host not allowed'
        path = urlsplit(target).path
        query = parse_qs(urlsplit(target).query)
        if method == 'GET' and (asset := self.static(path)):
            return asset
        api = path.startswith('/api/')
        form = {}
        if body and not api:
            form = {k: v[0] for k, v in parse_qs(body.decode('utf-8', 'replace'), keep_blank_values=True).items()}
        if path == '/login':
            if method == 'POST' and not self.origin_ok(headers):
                return self.render('Request rejected', '<p>Origin not allowed.</p>', status=403)
            return self.login(method, form, ip)
        session = self.session(headers)
        if not session:
            return self.json({'error': 'sign-in required'}, 401) if api else (303, {'Location': '/login'}, b'')
        if api:
            return self.api(method, path, headers, body, session, query)
        if method == 'POST' and (not hmac.compare_digest(form.get('csrf', ''), session['csrf']) or not self.origin_ok(headers)):
            return self.render('Request rejected', '<p>Reload the page and try again.</p>', session, status=403)
        if method == 'GET' and path in LEGACY:
            return 301, {'Location': LEGACY[path]}, b''
        if method == 'GET' and path == '/map':
            return 303, {'Location': self.map_link()}, b''
        if method == 'GET' and (match := re.fullmatch(r'/(?:job|tarea)/([0-9a-f]{12})', path)):
            return self.job_page(session, match[1])
        if method == 'GET' and (match := re.fullmatch(r'/exports/([a-z0-9-]{1,120}\.(?:zip|mcworld))', path)):
            return self.download(match[1])
        if match := re.fullmatch(r'/players/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', path):
            return (self.profile_page if method == 'GET' else self.profile_post)(session, form, match[1])
        routes = {
            ('GET', '/'): self.dashboard, ('POST', '/action'): self.action,
            ('GET', '/players'): self.players_page, ('POST', '/players'): self.players_post,
            ('GET', '/world'): self.world_page, ('POST', '/world'): self.world_post,
            ('GET', '/console'): self.console_page, ('POST', '/console'): self.console_post,
            ('GET', '/backups'): self.backups_page, ('GET', '/health'): self.health_page,
            ('GET', '/account'): self.account_page, ('POST', '/account'): self.account_post,
            ('POST', '/logout'): self.logout,
        }
        handler = routes.get((method, path))
        if not handler:
            return self.render('Not found', '<p><a href="/" data-nav>Back to the dashboard</a></p>', session, status=404)
        return handler(session, form, query)

    def upload_problem(self, name, length):
        """Validación compartida por /api/upload-check (antes de enviar) y /upload."""
        if not 0 < length <= MAX_UPLOAD:
            return 413, f'files must be under {MAX_UPLOAD // 2**30} GiB'
        if not UPLOAD_NAME.fullmatch(name):
            return 400, 'only .zip (Java) or .mcworld (Bedrock) files with a simple name (letters, digits, . _ - space)'
        try:
            if shutil.disk_usage(Path(self.config.get('imports_dir', '/opt/minecraft-lan-installer/imports'))).free \
                    < length + UPLOAD_MARGIN:
                return 507, 'not enough free space on the Pi (4 GiB margin)'
        except OSError:
            return 500, 'the imports folder is not available'
        if UPLOADING.locked():
            return 409, 'another upload is in progress'
        return None

    def upload(self, headers, stream, ip):
        """POST /upload: el cuerpo es el archivo; nombre en X-Filename (URL-encoded). Se valida todo
        ANTES de leer el cuerpo y se escribe por bloques en imports/ con un nombre temporal."""
        if headers.get('Host') not in (f"{self.config['bind']}:{self.config['port']}", self.config['bind']):
            return 421, {}, b'Host not allowed'
        session = self.session(headers)
        if not session:
            return self.json({'error': 'sign-in required'}, 401)
        if not hmac.compare_digest(headers.get('X-CSRF-Token', ''), session['csrf']) or not self.origin_ok(headers):
            return self.json({'error': 'request rejected'}, 403)
        declared = headers.get('Content-Length', '')
        if headers.get('Transfer-Encoding') or not re.fullmatch(r'[0-9]{1,11}', declared):
            return self.json({'error': 'a Content-Length is required'}, 411)
        length = int(declared)
        name = urllib.parse.unquote(headers.get('X-Filename', '')).strip()
        problem = self.upload_problem(name, length)
        if problem:
            return self.json({'error': problem[1]}, problem[0])
        folder = Path(self.config.get('imports_dir', '/opt/minecraft-lan-installer/imports'))
        if not UPLOADING.acquire(blocking=False):
            return self.json({'error': 'another upload is in progress'}, 409)
        temporary = folder / f'.upload-{secrets.token_hex(8)}.part'
        try:
            remaining = length
            with open(temporary, 'xb') as out:
                os.chmod(temporary, 0o640)
                first = True
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ConnectionError('upload interrupted')
                    if first and not chunk.startswith(ZIP_MAGIC):
                        return self.json({'error': 'the file is not a zip / .mcworld archive'}, 400)
                    first = False
                    out.write(chunk)
                    remaining -= len(chunk)
            target, counter = folder / name, 2
            while target.exists():
                stem, dot, suffix = name.rpartition('.')
                target, counter = folder / f'{stem}-{counter}.{suffix}', counter + 1
            os.rename(temporary, target)
            return self.json({'ok': True, 'name': target.name, 'size': length})
        except (OSError, ConnectionError) as exc:
            return self.json({'error': f'upload failed ({type(exc).__name__})'}, 400)
        finally:
            temporary.unlink(missing_ok=True)
            UPLOADING.release()

    def api(self, method, path, headers, body, session, query):
        """JSON para app.js. Los POST exigen el token CSRF en cabecera y el mismo Origin."""
        if method == 'POST':
            if not hmac.compare_digest(headers.get('X-CSRF-Token', ''), session['csrf']) or not self.origin_ok(headers):
                return self.json({'error': 'request rejected'}, 403)
            try:
                payload = json.loads(body or b'{}')
            except ValueError:
                return self.json({'error': 'invalid JSON'}, 400)
            if path == '/api/console' and isinstance(payload, dict) and isinstance(payload.get('command'), str):
                return self.json(self.helper(['console', payload['command']], timeout=30))
            return self.json({'error': 'not found'}, 404)
        if path == '/api/upload-check':
            size = query.get('size', ['0'])[0]
            problem = self.upload_problem(query.get('name', [''])[0], int(size) if size.isdigit() else 0)
            return self.json({'ok': True} if not problem else {'ok': False, 'error': problem[1]})
        if path == '/api/pulse':
            return self.json(self.helper(['pulse'], timeout=30))
        if path == '/api/console':
            lines = query.get('lines', ['200'])[0]
            return self.json(self.helper(['logs', lines if lines in ('100', '200', '500') else '200'], timeout=30))
        if match := re.fullmatch(r'/api/job/([0-9a-f]{12})', path):
            job = self.jobs.items.get(match[1])
            if not job:
                return self.json({'error': 'not found'}, 404)
            return self.json({'state': job['state'], 'elapsed': int(time.time() - job['started']),
                              'result': job['result'] if job['state'] == 'done' else None})
        return self.json({'error': 'not found'}, 404)

    def login(self, method, form, ip):
        message = ''
        if method == 'POST':
            stamp = self.reserve_attempt(ip)
            if stamp is None:
                return self.render('Too many attempts', '<p>Wait a few minutes and try again.</p>', status=429)
            if not HASHING.acquire(timeout=10):
                self.release_attempt(ip, stamp)
                return self.render('Busy', '<p>Try again in a few seconds.</p>', status=503)
            try:
                valid = check_password(form.get('password', '')[:1024], self.config['password'])
            finally:
                HASHING.release()
            if valid:
                self.release_attempt(ip, stamp)
                token = secrets.token_urlsafe(32)
                now = time.time()
                with self.lock:
                    for key in [k for k, s in self.sessions.items() if s['expires'] <= now]:
                        del self.sessions[key]
                    while len(self.sessions) >= MAX_SESSIONS:
                        del self.sessions[min(self.sessions, key=lambda k: self.sessions[k]['expires'])]
                    self.sessions[token] = {'expires': now + SESSION_TTL, 'csrf': secrets.token_urlsafe(24)}
                cookie = f'{COOKIE}={token}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}'
                return 303, {'Location': '/', 'Set-Cookie': cookie}, b''
            message = 'Wrong password.'
        return self.render('Welcome back', self.views.login(message), slug='login')

    def logout(self, session, form, query):
        with self.lock:
            for token, value in list(self.sessions.items()):
                if value is session:
                    del self.sessions[token]
        return 303, {'Location': '/login', 'Set-Cookie': f'{COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0'}, b''

    def download(self, name):
        """Exportación como descarga en streaming (puede ocupar cientos de MiB)."""
        folder = Path(self.config.get('exports_dir', '/opt/minecraft-lan-installer/exports'))
        path = folder / name
        try:
            info = os.lstat(path)
        except OSError:
            return 404, {'Content-Type': 'text/plain'}, b'Not found'
        if not stat.S_ISREG(info.st_mode):
            return 404, {'Content-Type': 'text/plain'}, b'Not found'
        return 200, {'Content-Type': 'application/octet-stream',
                     'Content-Disposition': f'attachment; filename="{name}"'}, path

    def status_or_error(self, session, title, active, slug):
        status = self.helper(['status'])
        if status.get('ok'):
            return status, None
        body = f'<div class="alert bad">Could not read the server status: {html.escape(str(status.get("output", "")))}</div>'
        return None, self.render(title, body, session, active, slug=slug)

    def map_link(self, spawn=None):
        """URL del mapa centrada en el spawn: un enlace antiguo puede apuntar a zonas sin generar."""
        if spawn is None:
            status = self.helper(['status'])
            spawn = status.get('spawn') if status.get('ok') else None
        base = self.config['map_url']
        if spawn and all(isinstance(v, int) for v in spawn):
            return f'{base}#world:{spawn[0]}:{spawn[1]}:{spawn[2]}:150:0:0:0:0:perspective'
        return base

    def run_job(self, session, label, argv, back='/'):
        identifier = self.jobs.start(label, argv)
        if not identifier:
            self.flash(session, 'bad', 'Another operation is still running. Wait for it to finish.')
            return 303, {'Location': back}, b''
        return 303, {'Location': f'/job/{identifier}'}, b''

    def dashboard(self, session, form, query):
        status, error = self.status_or_error(session, 'Dashboard', '/', 'dashboard')
        if error:
            return error
        return self.render('Dashboard', self.views.dashboard(status, session, self.map_link(status.get('spawn'))),
                           session, '/', slug='dashboard')

    def action(self, session, form, query):
        verb = form.get('do')
        if verb not in ACTIONS:
            return 400, {}, b''
        return self.run_job(session, ACTIONS[verb], [verb], '/backups' if verb == 'backup' else '/')

    def job_page(self, session, identifier):
        job = self.jobs.items.get(identifier)
        if not job:
            return self.render('Job not found', '<p><a href="/" data-nav>Back</a></p>', session, status=404)
        refresh = 3 if job['state'] == 'running' else None
        return self.render(job['label'], self.views.job(job), session, refresh=refresh, slug='job')

    def players_page(self, session, form, query):
        status, error = self.status_or_error(session, 'Players', '/players', 'players')
        if error:
            return error
        known = self.helper(['players'])
        body = (self.views.known(known.get('players', [])) if known.get('ok') else '') + self.views.players(status, session)
        return self.render('Players', body, session, '/players', slug='players')

    def profile_page(self, session, form, uuid):
        profile = self.helper(['player', uuid])
        if not profile.get('ok'):
            return self.render('Player', f'<div class="alert bad">{html.escape(str(profile.get("output", "")))}</div>',
                               session, '/players', slug='profile', status=404)
        status = self.helper(['status'])
        map_base = self.config['map_url'] if (status.get('bluemap') or {}).get('enabled') else ''
        return self.render(profile['name'], self.views.profile(profile, session, map_base, status.get('spawn')),
                           session, '/players', slug='profile')

    def profile_post(self, session, form, uuid):
        action = form.get('do')
        known = {p['uuid']: p['name'] for p in self.helper(['players']).get('players', [])}
        if uuid not in known or action not in ('op', 'deop', 'kick', 'ban', 'pardon', 'gamemode', 'spawn', 'heal'):
            return 400, {}, b''
        argv = ['player-action', action, known[uuid]] + ([form.get('mode', '')] if action == 'gamemode' else [])
        labels = {'op': 'Operator rights granted.', 'deop': 'Operator rights removed.', 'kick': 'Player kicked.',
                  'ban': 'Player banned.', 'pardon': 'Player unbanned.', 'gamemode': 'Game mode changed.',
                  'spawn': 'Teleported to spawn.', 'heal': 'Healed and fed.'}
        self.result_flash(session, self.helper(argv, timeout=30), labels[action])
        return 303, {'Location': f'/players/{uuid}'}, b''

    def players_post(self, session, form, query):
        action = form.get('do')
        name = form.get('name', '').strip()
        if action == 'add' and form.get('edition') in EDITIONS:
            result = self.helper(['whitelist-add', form['edition'], name], timeout=60)
            if not result.get('ok') and 'find user in our cache' in (result.get('output') or ''):
                # Floodgate sólo resuelve gamertags que la caché global de GeyserMC ya conoce.
                self.flash(session, 'warn', f"Floodgate does not know {name} yet: GeyserMC's global cache has no Xbox ID "
                           "(XUID) for them. Use 'Open 5 min and wait' on their row and ask them to join from Bedrock, "
                           'or add them with their XUID.')
            else:
                self.result_flash(session, result, 'Player added.')
        elif action == 'remove':
            self.result_flash(session, self.helper(['whitelist-remove', name]), 'Player removed.')
        elif action == 'switch' and form.get('value') in ('on', 'off'):
            self.result_flash(session, self.helper(['whitelist', form['value']]),
                              'Whitelist on.' if form['value'] == 'on' else 'Whitelist off.')
        elif action == 'open' and form.get('edition') in ('java', 'bedrock'):
            return self.run_job(session, f'Waiting for {name}',
                                ['whitelist-open-for', form['edition'], name, form.get('minutes', '5')], '/players')
        else:
            return 400, {}, b''
        return 303, {'Location': '/players'}, b''

    def world_page(self, session, form, query):
        status, error = self.status_or_error(session, 'World', '/world', 'world')
        if error:
            return error
        lib = self.helper(['worlds'])
        body = ((self.views.library(lib, session) if lib.get('ok') and isinstance(lib.get('worlds'), list) else
                 f'<div class="alert bad">World library unavailable: {html.escape(str(lib.get("output", "")))}</div>')
                + self.views.world(status, session, self.map_link(status.get('spawn'))))
        return self.render('World', body, session, '/world', slug='world')

    def world_post(self, session, form, query):
        action, value = form.get('do'), form.get('value')
        if action == 'render' and value in ('on', 'off'):
            self.result_flash(session, self.helper(['bluemap-render', value]),
                              'Rendering resumed.' if value == 'on' else 'Rendering paused.')
            return 303, {'Location': '/world'}, b''
        if action == 'plugin' and value in ('on', 'off'):
            return self.run_job(session, 'Enable map' if value == 'on' else 'Disable map', ['plugin', 'bluemap', value], '/world')
        wid = form.get('id', '')
        if action == 'world-create':
            self.result_flash(session, self.helper(['world-create', form.get('name', ''), form.get('seed', '').strip()]), '')
            return 303, {'Location': '/world'}, b''
        if action == 'world-activate':
            return self.run_job(session, 'Switch world', ['world-activate', wid], '/world')
        if action == 'world-delete':
            return self.run_job(session, 'Delete world', ['world-delete', wid], '/world')
        if action == 'world-export' and form.get('format') in ('java', 'bedrock'):
            return self.run_job(session, f'Export world ({form["format"]})', ['world-export', wid, form['format']], '/world')
        if action == 'world-import':
            return self.run_job(session, 'Import world', ['world-import', form.get('file', ''), form.get('name', '')], '/world')
        if action == 'settings':
            status = self.helper(['status'])
            if not status.get('ok'):
                self.flash(session, 'bad', status.get('output', 'No status'))
                return 303, {'Location': '/world'}, b''
            changes = [f'{key}={form[key]}' for key, *_ in PROPERTY_FORM
                       if key in form and form[key] != status['properties'].get(key, '')]
            if not changes:
                self.flash(session, 'warn', 'You did not change any setting.')
                return 303, {'Location': '/world'}, b''
            return self.run_job(session, 'Apply settings', ['set-properties', *changes], '/world')
        return 400, {}, b''

    def console_page(self, session, form, query):
        result = self.helper(['logs', '200'])
        return self.render('Console', self.views.console(session, result.get('log', result.get('output', ''))),
                           session, '/console', slug='console')

    def console_post(self, session, form, query):
        """Sin JavaScript: ejecuta el comando y muestra el resultado como aviso."""
        result = self.helper(['console', form.get('command', '')], timeout=30)
        self.result_flash(session, result, '')
        return 303, {'Location': '/console'}, b''

    def backups_page(self, session, form, query):
        status, error = self.status_or_error(session, 'Backups', '/backups', 'backups')
        if error:
            return error
        return self.render('Backups', self.views.backups(status, session), session, '/backups', slug='backups')

    def health_page(self, session, form, query):
        return self.render('Health', self.views.health(self.helper(['audit'], timeout=180)), session, '/health', slug='health')

    def account_page(self, session, form, query):
        return self.render('Account', self.views.account(session), session, '/account', slug='account')

    def account_post(self, session, form, query):
        new = form.get('new', '')
        with HASHING:
            current_ok = check_password(form.get('current', '')[:1024], self.config['password'])
        if not current_ok:
            self.flash(session, 'bad', 'The current password is not correct.')
        elif len(new) < 12 or new != form.get('repeat'):
            self.flash(session, 'bad', 'The new password needs at least 12 characters and both fields must match.')
        else:
            self.config['password'] = hash_password(new)
            if self.config_path:
                write_config(self.config_path, self.config)
                # La contraseña inicial generada por install.sh deja de ser válida.
                (self.config_path.parent / 'initial-password').unlink(missing_ok=True)
            with self.lock:
                for token in [t for t, s in self.sessions.items() if s is not session]:
                    del self.sessions[token]
            self.flash(session, 'ok', 'Password changed.')
        return 303, {'Location': '/account'}, b''


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'CraftPiPanel'
    sys_version = ''
    timeout = 30

    def respond(self, method):
        if method == 'POST' and urlsplit(self.path).path == '/upload':
            # La subida se valida antes de leer el cuerpo y se escribe por bloques.
            status, headers, payload = self.server.app.upload(self.headers, self.rfile, self.client_address[0])
            self.send_response(status)
            for key, value in {'Content-Type': 'application/json', 'Cache-Control': 'no-store',
                               'X-Content-Type-Options': 'nosniff', **headers}.items():
                self.send_header(key, value)
            self.send_header('Content-Length', str(len(payload)))
            if status != 200:
                self.send_header('Connection', 'close')  # no se lee el resto de un cuerpo rechazado
                self.close_connection = True
            self.end_headers()
            self.wfile.write(payload)
            return
        declared = self.headers.get('Content-Length', '0')
        if self.headers.get('Transfer-Encoding') or not re.fullmatch(r'[0-9]{1,6}', declared.strip() or '0'):
            self.send_error(400)
            return
        length = int(declared.strip() or '0')
        if length > MAX_BODY:
            self.send_error(413)
            return
        body = self.rfile.read(length) if length else b''
        status, headers, payload = self.server.app.handle(method, self.path, self.headers, body, self.client_address[0])
        self.send_response(status)
        defaults = {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
                    'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
                                               "font-src 'self'; connect-src 'self'; form-action 'self'; "
                                               "frame-ancestors 'none'; base-uri 'none'",
                    'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
                    # no-referrer hace que el navegador envíe "Origin: null" en los POST del propio
                    # panel y la comprobación de Origin los rechazaría. same-origin no filtra nada fuera.
                    'Referrer-Policy': 'same-origin'}
        for key, value in {**defaults, **headers}.items():
            self.send_header(key, value)
        if isinstance(payload, Path):  # descarga: se envía por bloques, sin cargarla en memoria
            with payload.open('rb') as stream:
                self.send_header('Content-Length', str(os.fstat(stream.fileno()).st_size))
                self.end_headers()
                while chunk := stream.read(1024 * 1024):
                    self.wfile.write(chunk)
            return
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


class PanelServer(http.server.ThreadingHTTPServer):
    """TLS por conexión y en su propio hilo: un cliente que no completa el handshake no bloquea el
    bucle de aceptación. Una petición HTTP sin cifrar al mismo puerto recibe una redirección a HTTPS."""
    daemon_threads = True

    def __init__(self, address, handler, app, context):
        super().__init__(address, handler)
        self.app, self.context = app, context
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self.per_ip, self.per_ip_lock = {}, threading.Lock()

    def process_request(self, request, client_address):
        """Rechaza conexiones por encima del límite total o por IP antes de crear un hilo."""
        ip = client_address[0]
        with self.per_ip_lock:
            if self.per_ip.get(ip, 0) >= MAX_CONNECTIONS_PER_IP or not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            self.per_ip[ip] = self.per_ip.get(ip, 0) + 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.release(ip)
            raise

    def release(self, ip):
        with self.per_ip_lock:
            self.per_ip[ip] -= 1
            if not self.per_ip[ip]:
                del self.per_ip[ip]
        self.slots.release()

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.release(client_address[0])

    def finish_request(self, request, client_address):
        request.settimeout(15)
        try:
            first = request.recv(1, socket.MSG_PEEK)
            if first != b'\x16':  # no es un ClientHello de TLS
                if first:
                    request.recv(4096)
                    location = f"https://{self.app.config['bind']}:{self.app.config['port']}/"
                    request.sendall(f'HTTP/1.1 301 Moved Permanently\r\nLocation: {location}\r\n'
                                    'Content-Length: 0\r\nConnection: close\r\n\r\n'.encode())
                return
            request = self.context.wrap_socket(request, server_side=True)
        except (OSError, ssl.SSLError):
            return
        request.settimeout(30)
        try:
            self.RequestHandlerClass(request, client_address, self)
        finally:
            try:
                request.close()
            except OSError:
                pass


def serve(config_path):
    config = json.loads(Path(config_path).read_text())
    app = App(config, Path(config_path))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config['cert'], config['key'])
    server = PanelServer((config['bind'], int(config['port'])), Handler, app, context)
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
