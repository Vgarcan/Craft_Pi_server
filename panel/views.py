"""HTML del panel: componentes con estilo Minecraft y páginas. Sólo genera texto; no tiene estado.

Todo dato dinámico pasa por e(). No hay estilos ni scripts en línea: la CSP sólo permite
/static/app.css y /static/app.js. Las texturas de /assets/ se extraen en la Pi del cliente de
Minecraft (Mojang) y no forman parte del repositorio: sin ellas el panel usa equivalentes en CSS.
"""
import html
import random
import re
import time

import catalog

SPLASHES = (
    'Now with NetherNet!', 'Raspberry Pi powered!', 'LAN only!', 'Java + Bedrock!', 'Pre-generated!',
    'Verified backups!', 'One world, two editions!', 'Also try BlueMap!', 'Pixel perfect!',
    'Made with Python!', 'No port forwarding!', 'Cross-play!', 'Floodgate open!', '100% local!',
)
# (ruta, etiqueta, textura, letra de reserva). El orden es el de las teclas 1-9.
HOTBAR = (
    ('/', 'Dashboard', 'compass_00', 'D'),
    ('/players', 'Players', 'name_tag', 'P'),
    ('/world', 'World', 'grass_block_side', 'W'),
    ('/console', 'Console', 'command_block_front', '>'),
    ('/backups', 'Backups', 'chest_minecart', 'B'),
    ('/health', 'Health', 'golden_apple', 'H'),
    ('/map', 'Live map', 'filled_map', 'M'),
    ('/account', 'Account', 'writable_book', 'A'),
)
EDITIONS = {'java': 'Java', 'bedrock': 'Bedrock', 'xuid': 'Bedrock (XUID)'}
ACTIONS = {'start': 'Start', 'stop': 'Stop', 'restart': 'Restart', 'backup': 'Backup now'}
PROPERTY_FORM = (
    ('motd', 'Message of the day', 'text', None),
    ('difficulty', 'Difficulty', 'select', (('peaceful', 'Peaceful'), ('easy', 'Easy'), ('normal', 'Normal'), ('hard', 'Hard'))),
    ('gamemode', 'Game mode', 'select', (('survival', 'Survival'), ('creative', 'Creative'),
                                         ('adventure', 'Adventure'), ('spectator', 'Spectator'))),
    ('max-players', 'Max players', 'number', (1, 20)),
    ('view-distance', 'View distance (chunks)', 'number', (3, 12)),
    ('simulation-distance', 'Simulation distance (chunks)', 'number', (3, 10)),
    ('pvp', 'PvP', 'select', (('true', 'On'), ('false', 'Off'))),
    ('allow-flight', 'Allow flight', 'select', (('true', 'Yes'), ('false', 'No'))),
    ('spawn-protection', 'Spawn protection (blocks)', 'number', (0, 64)),
)
# Silueta de colinas en bloques de 64 px (alturas 2-6). Columnas, no recorte: así cada columna
# conserva su capa de hierba arriba.
HILLS_FRONT = (3, 3, 4, 4, 5, 4, 3, 3, 2, 2, 3, 4, 4, 4, 3, 2, 2, 3, 3, 4, 5, 5, 4, 3, 3, 2, 3, 3, 4, 4, 3, 2)
HILLS_BACK = (4, 5, 5, 6, 6, 5, 4, 4, 5, 6, 6, 6, 5, 4, 3, 4, 5, 5, 6, 5, 4, 4, 5, 6, 6, 5, 4, 3, 4, 5, 6, 5)
STAT_LABELS = (
    ('play_time', 'Time played', 'ticks'), ('deaths', 'Deaths', 'count'), ('mob_kills', 'Mob kills', 'count'),
    ('player_kills', 'Player kills', 'count'), ('walk_one_cm', 'Walked', 'cm'), ('sprint_one_cm', 'Sprinted', 'cm'),
    ('swim_one_cm', 'Swum', 'cm'), ('fly_one_cm', 'Flown', 'cm'), ('boat_one_cm', 'By boat', 'cm'), ('jump', 'Jumps', 'count'),
    ('damage_dealt', 'Damage dealt', 'damage'), ('damage_taken', 'Damage taken', 'damage'),
    ('time_since_death', 'Since last death', 'ticks'), ('leave_game', 'Sessions', 'count'),
    ('open_chest', 'Chests opened', 'count'), ('traded_with_villager', 'Villager trades', 'count'),
    ('animals_bred', 'Animals bred', 'count'), ('fish_caught', 'Fish caught', 'count'), ('sleep_in_bed', 'Nights slept', 'count'),
)
GAMEMODE_LABELS = {'survival': 'Survival', 'creative': 'Creative', 'adventure': 'Adventure', 'spectator': 'Spectator'}
CONSOLE_SHORTCUTS = ('list', 'time set day', 'weather clear', 'tps', 'say Hello from the panel!', 'worldborder get')


def e(value):
    return html.escape(str(value), quote=True)


def pretty(identifier):
    return str(identifier).replace('_', ' ').replace('/', ' › ').capitalize()


def amount(value, unit):
    if not isinstance(value, (int, float)):
        return '—'
    if unit == 'ticks':
        minutes = int(value) // 1200
        return f'{minutes // 60} h {minutes % 60} min' if minutes >= 60 else f'{minutes} min'
    if unit == 'cm':
        return f'{value / 100000:.2f} km' if value >= 100000 else f'{value / 100:.0f} m'
    if unit == 'damage':
        return f'{value / 20:.0f} ♥'  # en décimas de corazón: 20 = 1 corazón completo
    return f'{value:,}'.replace(',', ' ')


def moment(millis):
    if not isinstance(millis, (int, float)) or millis <= 0:
        return '—'
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(millis / 1000))


class Views:
    def __init__(self, assets=frozenset(), version='', items=None):
        self.assets = set(assets)
        self.items = dict(items or {})  # catálogo {id: nombre en inglés} para «Give item»
        # Huella de app.css + app.js: cada despliegue cambia la URL y el navegador no usa la versión vieja.
        self.version = version

    # --- Componentes ------------------------------------------------------------------------
    def texture(self, name, alt='', cls='tex'):
        """Textura de Minecraft si está instalada; si no, nada (el CSS pone su equivalente)."""
        if f'{name}.png' not in self.assets:
            return ''
        return f'<img class="{cls}" src="/assets/{e(name)}.png" alt="{e(alt)}" width="16" height="16" loading="lazy">'

    def item_asset(self, identifier):
        """Textura que representa un objeto o mob (objeto, bloque o huevo de generación), o ''.
        Los objetos sin textura plana usan una cercana: brújula y reloj su primer fotograma, escaleras,
        losas, muros y vallas su bloque base, camas y estandartes la lana de su color."""
        identifier = str(identifier)
        names = [f'item_{identifier}', f'block_{identifier}', f'block_{identifier}_side', f'block_{identifier}_front',
                 f'block_{identifier}_top', f'item_{identifier}_spawn_egg', f'item_{identifier}_00',
                 f'item_{identifier.removeprefix("enchanted_")}']
        base = re.sub(r'_(stairs|slab|wall|fence_gate|fence|button|pressure_plate)$', '', identifier)
        if base != identifier:
            names += [f'block_{base}', f'block_{base}s', f'block_{base}_planks', f'block_{base.removesuffix("_brick")}_bricks']
        if match := re.fullmatch(r'([a-z_]+)_(bed|banner|carpet)', identifier):
            names.append(f'block_{match[1]}_wool')
        return next((name for name in names if f'{name}.png' in self.assets), '')

    def item_icon(self, identifier, cls='item-icon'):
        """Icono de un objeto o mob: textura de objeto, de bloque, o huevo de generación."""
        if name := self.item_asset(identifier):
            return f'<img class="{cls}" src="/assets/{e(name)}.png" alt="" width="16" height="16" loading="lazy">'
        return f'<span class="{cls} missing">{e(str(identifier)[:2].upper())}</span>'

    def item_name(self, identifier):
        return self.items.get(identifier) or pretty(identifier)

    def slot(self, item=None, empty_label=''):
        if not item:
            return f'<div class="inv-slot empty" title="{e(empty_label)}"></div>'
        count = item.get('count') or 1
        return (f'<div class="inv-slot" title="{e(pretty(item["id"]))}" tabindex="0">{self.item_icon(item["id"])}'
                + (f'<span class="count">{e(count)}</span>' if count != 1 else '')
                + f'<span class="tooltip">{e(pretty(item["id"]))}{f" × {e(count)}" if count != 1 else ""}</span></div>')

    @staticmethod
    def csrf(session):
        return f'<input type="hidden" name="csrf" value="{e(session["csrf"])}">'

    def button(self, action, fields, label, session, kind='', confirm='', job=False):
        """job=True: la acción es una tarea larga; app.js la lanza en segundo plano y avisa al terminar."""
        hidden = ''.join(f'<input type="hidden" name="{e(k)}" value="{e(v)}">' for k, v in fields.items())
        ask = f' data-confirm="{e(confirm)}"' if confirm else ''
        ask += ' data-async' if job else ''
        return (f'<form class="inline" method="post" action="{e(action)}"{ask}>{self.csrf(session)}{hidden}'
                f'<button class="mc-btn {e(kind)}">{e(label)}</button></form>')

    @staticmethod
    def pill(kind, text):
        return f'<span class="pill {e(kind)}">{e(text)}</span>'

    @staticmethod
    def bar(percent, kind='xp', label=''):
        """Barra de experiencia: el ancho va en clases w-0…w-100 porque la CSP prohíbe style=."""
        step = max(0, min(100, int(round((percent or 0) / 5)) * 5))
        return (f'<div class="bar {e(kind)}" role="img" aria-label="{e(label or f"{step}%")}">'
                f'<div class="fill w-{step}"></div></div>')

    @staticmethod
    def card(title, body, cls='', icon=''):
        return f'<section class="panel {e(cls)}"><h2>{icon}{e(title)}</h2>{body}</section>'

    @staticmethod
    def table(headers, rows, empty):
        head = ''.join(f'<th>{e(h)}</th>' for h in headers)
        body = ''.join('<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows)
        if not rows:
            body = f'<tr><td colspan="{len(headers)}" class="muted">{e(empty)}</td></tr>'
        return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'

    # --- Marco de página --------------------------------------------------------------------
    def page(self, title, body, session=None, active='', refresh=None, slug='page', flashes=()):
        logo = ('<img class="logo" src="/assets/logo.png" alt="Minecraft" width="256" height="64">'
                if 'logo.png' in self.assets else '<span class="logo-text">MINECRAFT</span>')
        splash = e(random.choice(SPLASHES))
        nav = ''
        chip = ''
        if session:
            slots = ''
            for index, (href, label, texture, letter) in enumerate(HOTBAR, start=1):
                current = ' active' if href == active else ''
                external = ' target="_blank" rel="noopener"' if href == '/map' else ' data-nav'
                slots += (f'<a class="slot{current}" href="{href}" data-key="{index}"{external} aria-label="{e(label)}">'
                          f'{self.texture(texture, "", "item") or f"<span class=item-letter>{e(letter)}</span>"}'
                          f'<span class="tooltip">{e(label)} <kbd>{index}</kbd></span></a>')
            slots += (f'<form class="slot logout" method="post" action="/logout">{self.csrf(session)}'
                      f'<button aria-label="Log out" data-key="9">{self.texture("barrier", "", "item") or "<span class=item-letter>X</span>"}'
                      '<span class="tooltip">Log out <kbd>9</kbd></span></button></form>')
            nav = f'<nav class="hotbar" aria-label="Main navigation">{slots}</nav>'
            chip = ('<div class="chips-top"><button type="button" class="job-tray" data-tray hidden aria-haspopup="dialog">'
                    '<span class="spinner" aria-hidden="true"></span><span class="tray-text">Jobs</span>'
                    '<span class="badge-count" hidden></span></button>'
                    '<a class="server-chip" href="/" data-nav data-live="chip"><span class="dot"></span>'
                    '<span class="chip-text">Server</span></a></div>')
        toasts = ''.join(f'<div class="toast {e(kind)}" role="status"><strong>{e(self.toast_title(kind))}</strong>'
                         f'<span>{e(text)}</span></div>' for kind, text in flashes)
        meta = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ''
        csrf = f' data-csrf="{e(session["csrf"])}"' if session else ''
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'{meta}<title>{e(title)} · Craft Pi Server</title>'
            f'<link rel="stylesheet" href="/static/app.css?v={e(self.version)}">'
            + ('<link rel="icon" href="/assets/grass_block_side.png">' if 'grass_block_side.png' in self.assets else '')
            + f'<script src="/static/app.js?v={e(self.version)}" defer></script></head>'
            f'<body class="{e(slug)}{" with-assets" if self.assets else ""}"{csrf}>'
            '<div class="scene" aria-hidden="true"><div class="layer sky"></div><div class="layer sun"></div>'
            '<div class="layer clouds far"></div><div class="layer clouds near"></div>'
            '<div class="layer hills back">' + ''.join(f'<i class="h{h}"></i>' for h in HILLS_BACK) + '</div>'
            '<div class="layer hills front">' + ''.join(f'<i class="h{h}"></i>' for h in HILLS_FRONT) + '</div>'
            '<div class="layer ground"></div></div>'
            f'<header class="top"><a class="brand" href="/" data-nav>{logo}<span class="splash">{splash}</span>'
            f'<span class="subtitle">Craft Pi Server</span></a>{chip}</header>'
            f'<div class="toasts">{toasts}</div>'
            + ('<dialog class="mc-dialog" data-jobs-dialog><div class="dialog-head"><h2 data-dialog-title>Operations</h2>'
               '<button type="button" class="mc-btn small" data-dialog-close>Close</button></div>'
               '<div data-dialog-body></div></dialog>' if session else '')
            + f'<main id="main" data-page="{e(slug)}" data-title="{e(title)} · Craft Pi Server">'
            f'<h1 class="page-title">{e(title)}</h1>{body}</main>{nav}</body></html>')

    @staticmethod
    def toast_title(kind):
        return {'ok': 'Done!', 'bad': 'Something went wrong', 'warn': 'Heads up'}.get(kind, 'Notice')

    # --- Páginas ----------------------------------------------------------------------------
    def login(self, message=''):
        alert = f'<div class="alert bad">{e(message)}</div>' if message else ''
        body = ('<section class="panel narrow">'
                f'<h2>{self.texture("iron_door", "", "icon")}Sign in</h2>{alert}'
                '<form method="post" action="/login"><label for="p">Password</label>'
                '<input id="p" type="password" name="password" autocomplete="current-password" required autofocus>'
                '<p><button class="mc-btn primary wide">Join server panel</button></p></form></section>')
        return body

    def dashboard(self, status, session, map_link):
        release, container = status['release'], status['container']
        running = container['running']
        health = container.get('health')
        hearts = {'healthy': 10, 'starting': 5, 'unhealthy': 2}.get(health, 0) if running else 0
        heart_row = ''.join(f'<span class="heart{" empty" if i >= hearts else ""}">{self.texture("heart", "", "heart-img")}</span>'
                            for i in range(10))
        state = (self.pill('ok', 'Online') if running and health == 'healthy' else
                 self.pill('warn', 'Starting') if running else self.pill('bad', 'Offline'))
        players = status.get('players') or ''
        match = re.search(r'There are (\d+) of a max of (\d+) players online:?\s*(.*)', players)
        names = [n.strip() for n in match[3].split(',') if n.strip()] if match else []
        player_list = ''.join(f'<li>{self.texture("name_tag", "", "icon")}{e(n)}</li>' for n in names) or \
            '<li class="muted">Nobody online right now</li>'
        memory = container.get('memory')
        mem_pct = memory['used'] * 100 / memory['limit'] if memory and memory.get('limit') else 0
        mem_text = (f'{memory["used"] / 2**30:.2f} / {memory["limit"] / 2**30:.1f} GiB' if memory and memory.get('limit')
                    else (f'{memory["used"] / 2**30:.2f} GiB' if memory else 'n/a'))
        temp = status['host'].get('temperature')
        temp_pct = ((temp - 30) * 100 / 55) if temp else 0
        ip = e(release['lan_ip'])
        alerts = self.alerts(status)
        actions = ''.join(self.button('/action', {'do': verb}, label, session,
                                      'danger' if verb in ('stop', 'restart') else ('primary' if verb == 'start' else ''),
                                      confirm={'stop': 'Stop the server? Players will be disconnected.',
                                               'restart': 'Restart the server? Players will be disconnected.'}.get(verb, ''),
                                      job=True)
                          for verb, label in ACTIONS.items())
        bluemap = status.get('bluemap')
        map_row = (f'<a href="{e(map_link)}" target="_blank" rel="noopener">Open live map at spawn</a>'
                   if bluemap and bluemap['enabled'] else '<span class="muted">Map disabled</span>')
        return (
            alerts +
            '<div class="grid stats">'
            + self.card('Server', f'<p class="big" data-live="state">{state}</p><div class="hearts" data-live="hearts" '
                        f'data-hearts="{hearts}">{heart_row}</div><p class="muted">Container restarts: '
                        f'{e(container.get("restarts"))}</p>', icon=self.texture('compass_00', '', 'icon'))
            + self.card('Players', f'<p class="big"><span data-live="online">{e(match[1] if match else "–")}</span>'
                        f'<span class="muted"> / {e(match[2] if match else "–")}</span></p>'
                        f'<ul class="players" data-live="players">{player_list}</ul>', icon=self.texture('name_tag', '', 'icon'))
            + self.card('Memory', f'<p class="big xp-level" data-live="memory-text">{e(mem_text)}</p>'
                        f'<div data-live="memory-bar">{self.bar(mem_pct, "xp", "memory")}</div>',
                        icon=self.texture('experience_bottle', '', 'icon'))
            + self.card('Temperature', f'<p class="big" data-live="temp-text">{e(f"{temp:.1f} °C" if temp else "n/a")}</p>'
                        f'<div data-live="temp-bar">{self.bar(temp_pct, "heat", "temperature")}</div>',
                        icon=self.texture('redstone', '', 'icon'))
            + '</div><div class="grid two">'
            + self.card('How to connect', self.table(('Edition', 'Address'), [
                (f'{self.texture("grass_block_side", "", "icon")}Java {e(release["minecraft"])}',
                 f'<code class="copy" data-copy="{ip}:25565">{ip}:25565</code>'),
                (f'{self.texture("bedrock", "", "icon")}Bedrock {e(release["bedrock"])}',
                 f'<code class="copy" data-copy="{ip}">{ip}</code> port <code>19132</code>'),
                (f'{self.texture("filled_map", "", "icon")}Live map', map_row)], ''), icon=self.texture('ender_eye', '', 'icon'))
            + self.card('Actions', '<p class="muted">Stop and Restart disconnect players. Backup pauses the server for '
                        'about a minute and starts it again.</p>' f'<div class="row">{actions}</div>',
                        icon=self.texture('command_block_front', '', 'icon'))
            + '</div>'
            + self.card('Versions', f'<p class="muted mono">Minecraft {e(release["minecraft"])} · Paper {e(release["paper"])} · '
                        f'Geyser {e(release["geyser"])} · Floodgate {e(release["floodgate"])} · BlueMap '
                        f'{e(release.get("bluemap") or "—")} · Chunky {e(release.get("chunky") or "—")} · '
                        f'ViaVersion {e(release.get("viaversion") or "—")} · '
                        f'release {e(release["id"])}</p>', 'thin', icon=self.texture('book', '', 'icon')))

    def alerts(self, status):
        items = []
        if status['pending_operation']:
            items.append(('bad', 'An operation was interrupted. Read the recovery guide before starting the server.'))
        if status['project_drift']:
            items.append(('warn', 'New Docker/Compose project files: run sudo ./manage.sh update on the Pi.'))
        if not status['host']['memory_limits']:
            items.append(('warn', 'The kernel does not enforce the container memory limit.'))
        if status['properties']['white-list'] == 'true' and not status['whitelist']:
            items.append(('warn', 'The whitelist is on and empty: nobody can join.'))
        if status['properties']['white-list'] != 'true':
            items.append(('warn', 'The whitelist is off: any valid account on your LAN can join.'))
        if status['rejected']:
            items.append(('warn', f'{len(status["rejected"])} player(s) were rejected by the whitelist. See Players.'))
        return ''.join(f'<div class="alert {k}">{e(t)}</div>' for k, t in items)

    def known(self, players):
        rows = []
        for p in players:
            state = self.pill('ok', 'Online') if p['online'] else self.pill('', 'Offline')
            tags = ''.join(t for t in (self.pill('warn', 'OP') if p['op'] else '', self.pill('bad', 'Banned') if p['banned'] else '',
                                       self.pill('ok', 'Whitelisted') if p['whitelisted'] else '') if t)
            rows.append((f'<a class="player-link" href="/players/{e(p["uuid"])}" data-nav>{self.texture("name_tag", "", "icon")}'
                         f'{e(p["name"])}</a>', EDITIONS.get(p['edition'], ''), f'{state} {tags}', e(moment(p['last_played']))))
        return self.card('Known players', self.table(('Player', 'Edition', 'Status', 'Last seen'), rows,
                                                     'Nobody has joined yet') +
                         '<p class="muted">Open a player to see their profile, inventory, statistics and server policies.</p>',
                         icon=self.texture('totem_of_undying', '', 'icon'))

    def profile(self, p, session, map_base, spawn, others=()):
        name, vitals, location, stats = p['name'], p['vitals'], p['location'], p['stats']
        uuid = p['uuid']
        badges = ''.join(t for t in (
            self.pill('ok', 'Online') if p['online'] else self.pill('', 'Offline'),
            self.pill('', EDITIONS.get(p['edition'], '')),
            self.pill('warn', f'Operator (level {p["op_level"]})') if p['op'] else '',
            self.pill('bad', 'Banned') if p['banned'] else '',
            self.pill('ok', 'Whitelisted') if p['whitelisted'] else self.pill('bad', 'Not whitelisted')) if t)
        ids = f'<p class="muted mono">UUID {e(uuid)}' + (f' · XUID {e(p["xuid"])}' if p.get('xuid') else '') + '</p>'
        ban = ''
        if p.get('ban'):
            ban = (f'<div class="alert bad">Banned {e(p["ban"].get("created") or "")} by {e(p["ban"].get("source") or "?")}: '
                   f'{e(p["ban"].get("reason") or "no reason")} (expires: {e(p["ban"].get("expires") or "never")})</div>')
        header = (f'<section class="panel nameplate"><div class="plate"><span class="head" aria-hidden="true"></span>'
                  f'<div><h2 class="player-name">{e(name)}</h2><p class="badges">{badges}</p>{ids}</div></div>{ban}'
                  f'<div class="facts"><span><b>First joined</b>{e(moment(p.get("first_played")))}</span>'
                  f'<span><b>Last seen</b>{e("now" if p["online"] else moment(p.get("last_played")))}</span>'
                  f'<span><b>Time played</b>{e(amount(stats["custom"].get("play_time"), "ticks"))}</span>'
                  f'<span><b>Game mode</b>{e(GAMEMODE_LABELS.get(vitals.get("gamemode"), "—"))}</span></div></section>')
        health = vitals.get('health') if isinstance(vitals.get('health'), (int, float)) else 0
        food = vitals.get('food') if isinstance(vitals.get('food'), (int, float)) else 0
        hearts = ''.join(f'<span class="heart{" empty" if i * 2 >= round(health) else ""}">{self.texture("heart", "", "heart-img")}</span>'
                         for i in range(10))
        drums = ''.join(f'<span class="food{" empty" if i * 2 >= round(food) else ""}">{self.texture("food", "", "heart-img")}</span>'
                        for i in range(10))
        xp = vitals.get('xp_progress') or 0
        saturation = f"{(vitals.get('saturation') or 0):.1f}"
        abilities = ', '.join(pretty(k) for k, v in (vitals.get('abilities') or {}).items() if v and k != 'mayBuild') or 'none'
        vitals_card = self.card('Vitals', f'<div class="hearts">{hearts}</div><p class="muted">Health {e(f"{health:.0f}")}/20</p>'
                                f'<div class="hearts">{drums}</div><p class="muted">Hunger {e(f"{food:.0f}")}/20 · saturation '
                                f'{e(saturation)}</p>'
                                f'<p class="big xp-level">Level {e(vitals.get("xp_level") or 0)}</p>{self.bar(xp * 100, "xp", "experience")}'
                                f'<p class="muted">Total XP {e(vitals.get("xp_total") or 0)} · abilities: {e(abilities)}</p>',
                                'r-vitals', icon=self.texture('golden_apple', '', 'icon'))
        def where(entry):
            if not entry or not entry.get('pos'):
                return '—'
            x, y, z = (round(v) for v in entry['pos'][:3])
            link = ''
            if map_base and entry.get('dimension') in ('overworld', ''):
                link = (f' <a class="mc-btn small" href="{e(map_base)}#world:{x}:{y}:{z}:80:0:0:0:0:perspective" '
                        'target="_blank" rel="noopener">Map</a>')
            return f'<span class="mono">{e(pretty(entry.get("dimension") or "overworld"))} {x}, {y}, {z}</span>{link}'
        location_card = self.card('Location', self.table(('', ''), [
            ('Current', where({'dimension': location.get('dimension'), 'pos': location.get('pos')})),
            ('Respawn point', where(location.get('respawn')) if location.get('respawn') else
             f'World spawn <span class="mono">{e(", ".join(str(v) for v in spawn or []))}</span>'),
            ('Last death', where(location.get('death')))], ''), 'r-location', icon=self.texture('compass_00', '', 'icon'))
        inventory = {item['slot']: item for item in p['inventory'] if isinstance(item.get('slot'), int)}
        main = ''.join(self.slot(inventory.get(i)) for i in range(9, 36))
        hotbar = ''.join(self.slot(inventory.get(i)) for i in range(9))
        equipment = p.get('equipment') or {}
        armor = ''.join(self.slot(equipment.get(s) or inventory.get(n), label)
                        for s, n, label in (('head', 103, 'Helmet'), ('chest', 102, 'Chestplate'), ('legs', 101, 'Leggings'),
                                            ('feet', 100, 'Boots')))
        offhand = self.slot(equipment.get('offhand') or inventory.get(-106), 'Off hand')
        ender = {item['slot']: item for item in p['ender_chest'] if isinstance(item.get('slot'), int)}
        inventory_card = self.card('Inventory', f'<div class="inventory"><div class="inv-side"><div class="inv-armor">{armor}</div>'
                                   f'<div class="inv-offhand">{offhand}</div></div><div class="inv-main"><div class="inv-grid">{main}</div>'
                                   f'<div class="inv-grid hotbar-row">{hotbar}</div></div></div>'
                                   + ('<p class="muted">Inventory is empty.</p>' if not p['inventory'] and not equipment else '')
                                   + '<details><summary>Ender chest</summary><div class="inv-grid">'
                                   + ''.join(self.slot(ender.get(i)) for i in range(27)) + '</div></details>'
                                   + ('<p class="muted">Inventory comes from the last autosave while the player is online.</p>'
                                      if p['online'] else ''), 'wide r-inventory', icon=self.texture('chest_minecart', '', 'icon'))
        headline = ''.join(f'<div class="stat"><span class="stat-value">{e(amount(stats["custom"].get(key), unit))}</span>'
                           f'<span class="stat-label">{e(label)}</span></div>'
                           for key, label, unit in STAT_LABELS if key in stats['custom'])
        def ranking(title, entries):
            if not entries:
                return ''
            rows = ''.join(f'<li>{self.item_icon(i["id"])}<span>{e(pretty(i["id"]))}</span><b>{e(i["count"])}</b></li>' for i in entries)
            return f'<div class="ranking"><h3>{e(title)}</h3><ol>{rows}</ol></div>'
        rankings = ''.join(ranking(t, stats.get(k)) for t, k in (('Blocks mined', 'mined'), ('Mobs killed', 'killed'),
                                                                   ('Killed by', 'killed_by'), ('Items crafted', 'crafted'),
                                                                   ('Items used', 'used'), ('Picked up', 'picked_up')))
        stats_card = self.card('Statistics', f'<div class="stat-grid">{headline or "<p class=muted>No statistics yet.</p>"}</div>'
                               f'<div class="rankings">{rankings}</div>', 'wide', icon=self.texture('writable_book', '', 'icon'))
        advancements = ''.join(f'<li>{self.texture("experience_bottle", "", "icon")}{e(pretty(a))}</li>' for a in p['advancements'])
        advancements_card = self.card(f'Advancements ({len(p["advancements"])})',
                                      f'<ul class="advancements">{advancements or "<li class=muted>None yet</li>"}</ul>',
                                      icon=self.texture('nether_star', '', 'icon'))
        actions_card = self.player_actions(p, session, others, spawn, inventory)
        return (header + actions_card + '<div class="grid two">' + vitals_card + location_card + '</div>' + inventory_card
                + stats_card + advancements_card
                + '<p><a class="mc-btn" href="/players" data-nav>Back to players</a></p>')

    # --- Acciones de jugador ------------------------------------------------------------------
    def act(self, p, session, do, label, kind='', confirm='', fields=None, icon='', offline_ok=False, hint=''):
        """Botón de una acción de jugador: formulario normal (sin JS) que app.js envía a la API JSON."""
        hidden = ''.join(f'<input type="hidden" name="{e(k)}" value="{e(v)}">' for k, v in (fields or {}).items())
        ask = f' data-confirm="{e(confirm)}"' if confirm else ''
        disabled = '' if p['online'] or offline_ok else ' disabled'
        tip = f' title="{e(hint)}"' if hint else ''
        return (f'<form class="inline" method="post" action="/players/{e(p["uuid"])}" '
                f'data-player-action="/api/players/{e(p["uuid"])}/action"{ask}>{self.csrf(session)}'
                f'<input type="hidden" name="do" value="{e(do)}">{hidden}'
                f'<button class="mc-btn {e(kind)}"{disabled}{tip}>{icon}<span>{e(label)}</span></button></form>')

    def action_form(self, p, session, do, body, cls='', offline_ok=False):
        """Formulario con campos: sin conexión se desactiva entero (fieldset disabled)."""
        disabled = '' if p['online'] or offline_ok else ' disabled'
        return (f'<form class="pa-form {e(cls)}" method="post" action="/players/{e(p["uuid"])}" '
                f'data-player-action="/api/players/{e(p["uuid"])}/action">{self.csrf(session)}'
                f'<input type="hidden" name="do" value="{e(do)}"><fieldset class="plain"{disabled}>{body}</fieldset></form>')

    @staticmethod
    def radios(name, options, checked, cls='segmented', legend=''):
        items = ''.join(f'<label><input type="radio" name="{e(name)}" value="{e(value)}"'
                        f'{" checked" if value == checked else ""}><span>{label}</span></label>'
                        for value, label in options)
        return f'<fieldset class="{e(cls)}"><legend>{e(legend)}</legend>{items}</fieldset>'

    def player_actions(self, p, session, others, spawn, inventory):
        name, online = p['name'], p['online']
        def section(title, body, note=''):
            return (f'<div class="pa-section"><h3>{e(title)}</h3>{body}'
                    + (f'<p class="muted small">{note}</p>' if note else '') + '</div>')
        roman = {1: 'I', 2: 'II', 3: 'III', 4: 'IV', 5: 'V'}

        # Objetos
        popular = ('diamond', 'iron_ingot', 'golden_apple', 'ender_pearl', 'cooked_beef', 'torch', 'oak_log', 'elytra',
                   'totem_of_undying', 'firework_rocket', 'experience_bottle', 'name_tag', 'saddle', 'cake')
        picks = ''.join(f'<button type="button" class="item-pick" data-item="{e(i)}" title="{e(self.item_name(i))}">'
                        f'{self.item_icon(i)}<span class="sr-only">{e(self.item_name(i))}</span></button>' for i in popular)
        counts = ''.join(f'<button type="button" class="mc-btn small" data-count="{n}">{n}</button>' for n in (1, 16, 32, 64))
        give = self.action_form(p, session, 'give', (
            '<div class="give-row"><label class="grow">Item<input type="text" name="item" required autocomplete="off" maxlength="64" '
            'spellcheck="false" placeholder="Search: diamond, elytra, cake…" data-item-search></label>'
            f'<label class="amount">Amount<input type="number" name="count" min="1" max="{catalog.GIVE_LIMIT}" value="1" '
            'inputmode="numeric"></label><button class="mc-btn primary">Give</button></div>'
            f'<div class="js-only quick-row"><span class="muted small">Quick</span>{counts}</div>'
            '<div class="item-results js-only" data-item-results aria-live="polite"></div>'
            f'<div class="js-only quick-row picks"><span class="muted small">Popular</span>{picks}</div>'), 'give-form')
        kits = ''.join(
            f'<div class="kit">{self.item_icon(icon, "kit-icon")}<div class="kit-text"><h4>{e(label)}</h4>'
            f'<p class="muted small">{e(text)}</p><div class="kit-items">'
            + ''.join(f'<span class="kit-item" title="{e(self.item_name(i))} × {c}">{self.item_icon(i)}'
                      + (f'<b>{c}</b>' if c > 1 else '') + '</span>' for i, c in contents)
            + f'</div></div>{self.act(p, session, "kit", "Give kit", "primary small", fields={"kit": key})}</div>'
            for key, (label, text, icon, contents) in catalog.KITS.items())
        xp = ''.join(self.act(p, session, 'xp', label, 'small' + (' danger' if value == 'reset' else ''),
                              f'Reset {name} to level 0?' if value == 'reset' else '', {'amount': value})
                     for value, label in catalog.XP_STEPS.items())
        items_panel = (section('Give an item', give, 'Search by English name or id. Without JavaScript, type the exact id, '
                               'for example <code>diamond_sword</code>. Up to 64 at a time.')
                       + section('Kits', f'<div class="kits">{kits}</div>')
                       + section('Experience levels', f'<div class="row">{xp}</div>')
                       + section('Inventory', self.act(p, session, 'clear', 'Clear inventory', 'danger',
                                                       f'Delete EVERYTHING in {name}\'s inventory? This cannot be undone.')))

        # Efectos
        effects = ''.join(f'<label class="effect-choice"><input type="radio" name="effect" value="{e(k)}"'
                          f'{" checked" if i == 0 else ""}>{self.texture(f"effect_{k}", "", "effect-icon")}'
                          f'<span>{e(v)}</span></label>' for i, (k, v) in enumerate(catalog.EFFECTS.items()))
        effect_form = self.action_form(p, session, 'effect', (
            f'<fieldset class="effect-grid"><legend>Effect</legend>{effects}</fieldset><div class="row">'
            + self.radios('duration', catalog.DURATIONS.items(), '300', legend='Duration')
            + self.radios('level', catalog.EFFECT_LEVELS.items(), '1', legend='Level')
            + '<button class="mc-btn primary">Apply effect</button></div>'))
        effects_panel = (section('Give an effect', effect_form)
                         + section('Health', '<div class="row">'
                                   + self.act(p, session, 'heal', 'Heal', 'primary', icon=self.texture('heart', '', 'icon'))
                                   + self.act(p, session, 'feed', 'Feed', '', icon=self.texture('food', '', 'icon'))
                                   + self.act(p, session, 'effects-clear', 'Remove all effects', 'danger',
                                              f'Remove every effect from {name}?')
                                   + '</div>'))

        # Teletransporte
        location = p['location']
        def spot(entry):
            if not entry or not entry.get('pos'):
                return 'not set'
            x, y, z = (round(v) for v in entry['pos'][:3])
            return f'{catalog.DIMENSIONS.get(entry.get("dimension") or "overworld", "?")} {x}, {y}, {z}'
        quick = ('<div class="tp-grid">'
                 + f'<div class="tp-spot">{self.item_icon("compass")}<div><b>World spawn</b><span class="muted mono small">'
                   f'{e(", ".join(str(v) for v in spawn or []) or "—")}</span></div>'
                   f'{self.act(p, session, "spawn", "Go", "small", f"Teleport {name} to the world spawn?")}</div>'
                 + f'<div class="tp-spot">{self.item_icon("red_bed")}<div><b>Bed / respawn point</b><span class="muted mono small">'
                   f'{e(spot(location.get("respawn")))}</span></div>'
                   f'{self.act(p, session, "tp-respawn", "Go", "small", f"Teleport {name} to their bed or respawn anchor?")}</div>'
                 + f'<div class="tp-spot">{self.item_icon("bone")}<div><b>Last death</b><span class="muted mono small">'
                   f'{e(spot(location.get("death")))}</span></div>'
                   + self.act(p, session, 'tp-death', 'Go', 'small',
                              f'Teleport {name} to where they last died? They get 30 seconds of protection '
                              '(no damage, no fire, slow falling) to pick up their things.') + '</div></div>')
        options = ''.join(f'<option value="{e(o)}">{e(o)}</option>' for o in others)
        if others:
            people = ('<div class="row">'
                      + self.action_form(p, session, 'tp-to', f'<label>Go to<select name="target">{options}</select></label>'
                                         '<button class="mc-btn">Teleport</button>', 'row')
                      + self.action_form(p, session, 'bring', f'<label>Bring<select name="target">{options}</select></label>'
                                         f'<button class="mc-btn">Bring to {e(name)}</button>', 'row')
                      + '</div>')
        else:
            people = '<p class="muted">No other players are online right now.</p>'
        dimensions = ''.join(f'<option value="{e(k)}">{e(v)}</option>' for k, v in catalog.DIMENSIONS.items())
        low, high = catalog.HEIGHT
        coords = self.action_form(p, session, 'tp-coords', (
            f'<div class="row coords"><label>World<select name="dimension">{dimensions}</select></label>'
            '<label>X<input type="text" name="x" required inputmode="numeric" pattern="-?[0-9]{1,8}" size="7"></label>'
            f'<label>Y<input type="text" name="y" inputmode="numeric" pattern="-?[0-9]{{1,3}}" size="5" placeholder="surface" '
            f'title="{low} to {high}, or empty for the surface"></label>'
            '<label>Z<input type="text" name="z" required inputmode="numeric" pattern="-?[0-9]{1,8}" size="7"></label>'
            '<button class="mc-btn primary">Teleport</button></div>'))
        teleport_panel = (section('Places', quick)
                          + section('Players', people)
                          + section('Coordinates', coords, 'Leave Y empty to land on the surface (Overworld only).'))

        # Diversión
        held = inventory.get(p.get('selected_slot')) if isinstance(p.get('selected_slot'), int) else None
        holding = (f'{self.item_icon(held["id"])} <b>{e(self.item_name(held["id"]))}</b>' if held else 'nothing')
        enchants = ''.join(f'<option value="{e(k)}" data-max="{m}">{e(label)} (max {roman[m]})</option>'
                           for k, (label, m) in catalog.ENCHANTMENTS.items())
        levels = ''.join(f'<option value="{n}">{roman[n]}</option>' for n in range(1, 6))
        enchant = self.action_form(p, session, 'enchant', (
            f'<div class="row"><label>Enchantment<select name="enchantment" data-enchant>{enchants}</select></label>'
            f'<label>Level<select name="level" data-enchant-level>{levels}</select></label>'
            '<button class="mc-btn primary">Enchant held item</button></div>'))
        pets = ''.join(self.act(p, session, 'pet', label, '', fields={'pet': key}, icon=self.item_icon(f'{key}_spawn_egg'))
                       for key, label in catalog.PETS.items())
        fun_panel = (section('Celebrate', '<div class="row">'
                             + self.act(p, session, 'fireworks', 'Fireworks', 'primary', icon=self.item_icon('firework_rocket'))
                             + self.act(p, session, 'thunder', 'Thunderclap', '',
                                        icon=self.item_icon('lightning_rod'), hint='Sound and sparks only: no fire, no damage')
                             + '</div>', 'The thunderclap is only sound and sparks: nothing burns and nobody gets hurt.')
                     + section('Give a tamed pet', f'<div class="row">{pets}</div>', 'It appears next to them, already tamed.')
                     + section('Enchant', f'<p>Holding (last save): {holding}</p>{enchant}',
                               'Works on the item in their main hand. Minecraft refuses enchantments that do not fit it.'))

        # Mensajes
        message = self.action_form(p, session, 'message', (
            self.radios('kind', catalog.MESSAGE_KINDS.items(), 'chat', legend='Show as')
            + f'<div class="give-row"><label class="grow">Message<input type="text" name="text" required maxlength="{catalog.MESSAGE_LIMIT}" '
              f'placeholder="Dinner in 5 minutes!" data-counter></label><button class="mc-btn primary">Send</button></div>'
            f'<p class="muted small"><span data-counter-out>0</span>/{catalog.MESSAGE_LIMIT} characters</p>'))
        message_panel = section(f'Message {name}', message,
                                'Chat messages appear as <b>[Server]</b> in gold. Only plain text; every message is logged on the Pi.')

        # Administración
        modes = ''.join(f'<option value="{m}"{" selected" if m == p["vitals"].get("gamemode") else ""}>{label}</option>'
                        for m, label in GAMEMODE_LABELS.items())
        admin_panel = (
            section('Permissions', '<div class="row">'
                    + (self.act(p, session, 'deop', 'Remove operator', 'danger', f'Remove operator rights from {name}?',
                                offline_ok=True) if p['op'] else
                       self.act(p, session, 'op', 'Make operator', '', f'Give {name} operator rights? They will be able '
                                'to run any command.', offline_ok=True))
                    + (self.button('/players', {'do': 'remove', 'name': name}, 'Remove from whitelist', session, 'danger',
                                   f'Remove {name} from the whitelist?') if p['whitelisted'] else '')
                    + (self.act(p, session, 'pardon', 'Unban', 'primary', offline_ok=True) if p['banned'] else
                       self.act(p, session, 'ban', 'Ban', 'danger', f'Ban {name}? They will be kicked and blocked.',
                                offline_ok=True))
                    + '</div>')
            + section('Game mode', self.action_form(p, session, 'gamemode',
                                                    f'<div class="row"><label>Mode<select name="mode">{modes}</select></label>'
                                                    '<button class="mc-btn">Apply</button></div>'))
            + section('Connection', self.act(p, session, 'kick', 'Kick', 'danger', f'Kick {name} from the server?')))

        tabs = (('items', 'Items', 'diamond', items_panel), ('effects', 'Effects', 'potion', effects_panel),
                ('teleport', 'Teleport', 'ender_pearl', teleport_panel), ('fun', 'Fun', 'firework_rocket', fun_panel),
                ('message', 'Message', 'writable_book', message_panel), ('admin', 'Admin', 'command_block', admin_panel))
        first = 'items' if online else 'admin'
        radios = ''.join(f'<input type="radio" name="pa-tab" id="pa-{k}" class="pa-radio"{" checked" if k == first else ""}>'
                         f'<label for="pa-{k}" class="pa-tab">{self.item_icon(icon, "tab-icon")}<span>{e(label)}</span></label>'
                         for k, label, icon, _ in tabs)
        panels = ''.join(f'<div class="pa-panel pa-{k}-panel">{body}</div>' for k, _, _, body in tabs)
        notice = ('' if online else
                  f'<div class="alert warn">{e(name)} is offline. Only the Admin tab works now; the rest unlocks when they join.</div>')
        return (f'<section class="panel wide player-actions"><h2>{self.texture("command_block_front", "", "icon")}Actions</h2>'
                f'{notice}<div class="pa-tabs" data-tabs>{radios}{panels}</div>'
                '<p class="muted small">Every action is logged on the Pi. The panel API is '
                f'<code>POST /api/players/{e(p["uuid"])}/action</code>.</p></section>')

    def players(self, status, session):
        enabled = status['properties']['white-list'] == 'true'
        rows = [(f'{self.texture("name_tag", "", "icon")}{e(p["name"])}', EDITIONS.get(p['edition'], e(p['edition'])),
                 f'<span class="muted mono">{e(p["uuid"])}</span>',
                 self.button('/players', {'do': 'remove', 'name': p['name']}, 'Remove', session, 'danger small',
                             f'Remove {p["name"]} from the whitelist?'))
                for p in status['whitelist']]
        rejected = [(e(r['name']), EDITIONS[r['edition']], f'<span class="muted">{e(r["last_seen"])}</span>',
                     self.button('/players', {'do': 'add', 'edition': r['edition'], 'name': r['name']}, 'Allow', session,
                                 'primary small')
                     + (self.button('/players', {'do': 'open', 'edition': 'bedrock', 'name': r['name'], 'minutes': '5'},
                                    'Open 5 min and wait', session, 'small', job=True) if r['edition'] == 'bedrock' else ''))
                    for r in status['rejected']]
        switch = self.button('/players', {'do': 'switch', 'value': 'off' if enabled else 'on'},
                             'Turn whitelist off' if enabled else 'Turn whitelist on', session,
                             'danger' if enabled else 'primary',
                             'Anyone with a valid account on your LAN will be able to join. Continue?' if enabled else '')
        options = ''.join(f'<option value="{k}">{v}</option>' for k, v in EDITIONS.items())
        state = (self.pill('ok', 'On') + ' Only listed players can join.' if enabled else
                 self.pill('bad', 'Off') + ' Any valid account that reaches the Pi can join.')
        return (
            '<div class="grid two">'
            + self.card('Whitelist', f'<p>{state}</p>{switch}', icon=self.texture('iron_door', '', 'icon'))
            + self.card('Add a player', f'<form method="post" action="/players">{self.csrf(session)}'
                        '<input type="hidden" name="do" value="add"><label for="ed">Edition</label>'
                        f'<select id="ed" name="edition">{options}</select><label for="nm">Name or XUID</label>'
                        '<input id="nm" type="text" name="name" maxlength="20" required pattern="[A-Za-z0-9_]{1,20}">'
                        '<p class="muted">Java: account name. Bedrock: gamertag without spaces (use _). If Floodgate '
                        'cannot find the gamertag, use the XUID or the temporary opening below.</p>'
                        '<p><button class="mc-btn primary">Add</button></p></form>', icon=self.texture('name_tag', '', 'icon'))
            + '</div>'
            + self.card('Allowed players', self.table(('Name', 'Edition', 'UUID', ''), rows, 'Nobody on the whitelist yet')
                        + '<p class="muted">Bedrock players show with a leading dot.</p>', icon=self.texture('totem_of_undying', '', 'icon'))
            + self.card('Rejected attempts', self.table(('Name', 'Edition', 'Last try (UTC)', ''), rejected,
                                                         'No recent rejections')
                        + '<p class="muted">Read from the Paper logs of the last 72 hours.</p>', icon=self.texture('barrier', '', 'icon'))
            + self.card('Temporary opening for one player', f'<form method="post" action="/players" data-async>{self.csrf(session)}'
                        '<input type="hidden" name="do" value="open"><label for="oe">Edition</label>'
                        '<select id="oe" name="edition"><option value="bedrock">Bedrock</option><option value="java">Java</option></select>'
                        '<label for="on">Gamertag or name</label><input id="on" type="text" name="name" maxlength="16" required '
                        'pattern="[A-Za-z0-9_]{1,16}"><label for="mi">Minutes (max 15)</label>'
                        '<input id="mi" type="number" name="minutes" min="1" max="15" value="5">'
                        '<p class="muted">Turns the whitelist off until that player joins, adds them and turns it back on. '
                        'Anyone else who joins meanwhile is kicked when it closes.</p>'
                        '<p><button class="mc-btn">Open and wait</button></p></form>', icon=self.texture('clock_00', '', 'icon')))

    def library(self, lib, session):
        """Biblioteca de mundos: hasta 50 guardados, uno activo en los puertos de siempre."""
        cards = ''
        for w in lib['worlds']:
            size = f'{w["size"] / 2**20:.0f} MiB' if isinstance(w.get('size'), (int, float)) else '—'
            source = {'existing': 'Original', 'new': 'New world', 'import-java': 'Imported (Java)',
                      'import-bedrock': 'Imported (Bedrock)', 'restored': 'Restored'}.get(w.get('source'), w.get('source') or '')
            state = (self.pill('ok', 'Active') if w['active'] else
                     self.pill('', 'Stored') if w['generated'] else self.pill('warn', 'Not generated yet'))
            actions = ''
            if not w['active']:
                actions += self.button('/world', {'do': 'world-activate', 'id': w['id']}, 'Activate', session, 'primary small',
                                       f'Switch the server to "{w["name"]}"? Players are disconnected for about a minute; '
                                       'a verified backup is taken first.', job=True)
            if w['generated']:
                actions += self.button('/world', {'do': 'world-export', 'id': w['id'], 'format': 'java'}, 'Export Java', session,
                                       'small', job=True)
                actions += self.button('/world', {'do': 'world-export', 'id': w['id'], 'format': 'bedrock'}, 'Export Bedrock',
                                       session, 'small', job=True)
            if not w['active']:
                actions += self.button('/world', {'do': 'world-delete', 'id': w['id']}, 'Delete', session, 'danger small',
                                       f'Delete "{w["name"]}"? It is archived to backups/worlds first.', job=True)
            seed = f' · seed <span class="mono">{e(w["seed"])}</span>' if w.get('seed') else ''
            relight = ' · light recalculated on first start' if w.get('needs_relight') else ''
            cards += (f'<article class="world-card{" active" if w["active"] else ""}">'
                      f'<div class="world-icon">{self.texture("grass_block_side" if w["generated"] else "bedrock", "", "icon")}</div>'
                      f'<div class="world-info"><h3>{e(w["name"])} {state}</h3>'
                      f'<p class="muted"><span class="mono">{e(w["id"])}</span> · {e(source)} · {e(size)}{seed}{relight}</p>'
                      f'<p class="muted">Created {e((w.get("created") or "—")[:10])} · last played {e((w.get("last_active") or "—")[:10])}</p>'
                      f'<div class="row">{actions}</div></div></article>')
        imports = ''.join(f'<option value="{e(f["name"])}">{e(f["name"])} ({f["size"] / 2**20:.0f} MiB)</option>' for f in lib['imports'])
        import_form = (f'<form method="post" action="/world" class="fields" data-async>{self.csrf(session)}'
                       '<input type="hidden" name="do" value="world-import">'
                       f'<div class="field"><label for="imf">File</label><select id="imf" name="file">{imports}</select></div>'
                       '<div class="field"><label for="imn">Name in the library</label><input id="imn" type="text" name="name" '
                       'maxlength="40" required></div><p><button class="mc-btn primary">Import</button></p></form>'
                       if imports else '<p class="muted">No files to import yet.</p>')
        exports = [(f'<a class="mc-btn small" href="/exports/{e(f["name"])}" download>Download</a> <span class="mono">{e(f["name"])}</span>',
                    f'{f["size"] / 2**20:.0f} MiB', time.strftime('%Y-%m-%d %H:%M', time.localtime(f['mtime'])))
                   for f in lib['exports']]
        return (
            self.card(f'World library ({len(lib["worlds"])} / {lib["limit"]})',
                      '<p class="muted">Only the active world is online, on the usual addresses: players never change '
                      'server address. Inventories belong to each world; whitelist and operators are shared.</p>'
                      f'<div class="worlds">{cards}</div>', 'wide', icon=self.texture('bookshelf', '', 'icon'))
            + '<div class="grid two">'
            + self.card('Create a world', f'<form method="post" action="/world">{self.csrf(session)}<input type="hidden" name="do" value="world-create">'
                        '<label for="wn">Name</label><input id="wn" type="text" name="name" maxlength="40" required>'
                        '<label for="ws">Seed (optional)</label><input id="ws" type="text" name="seed" inputmode="numeric" '
                        'pattern="-?[0-9]{1,19}" maxlength="20"><p class="muted">It is generated the first time you activate it. '
                        'Pre-generate it from Console with Chunky to keep the Pi smooth.</p>'
                        '<p><button class="mc-btn primary">Create</button></p></form>', icon=self.texture('crafting_table_front', '', 'icon'))
            + self.card('Import a world',
                        f'<form class="upload" data-upload method="post" action="/world">{self.csrf(session)}'
                        '<input type="hidden" name="do" value="world-import"><input type="hidden" name="file" value="">'
                        '<label for="upf">World file (.zip Java or .mcworld Bedrock, up to 2 GiB)</label>'
                        '<input id="upf" type="file" accept=".zip,.mcworld" required>'
                        '<label for="upn">Name in the library</label><input id="upn" type="text" name="name" maxlength="40" required>'
                        '<div class="bar xp" hidden data-upload-bar><div class="fill w-0"></div></div>'
                        '<p class="muted" data-upload-status></p>'
                        '<p><button class="mc-btn primary">Upload and import</button></p>'
                        '<noscript><p class="muted">Uploading needs JavaScript. You can still copy the file into '
                        'the imports folder and pick it below.</p></noscript></form>'
                        '<details><summary>Files already on the Pi</summary><p class="muted">Files copied into '
                        '<code>/opt/minecraft-lan-installer/imports/</code> (for example by dragging them into VS Code).</p>'
                        + import_form + '</details>'
                        '<p class="muted">Bedrock worlds are converted with Chunker in an isolated container; mobs and dropped '
                        'items do not survive the conversion.</p>', icon=self.texture('ender_pearl', '', 'icon'))
            + '</div>'
            + self.card('Exports', self.table(('File', 'Size', 'Created'), exports, 'No exports yet'),
                        icon=self.texture('chest_minecart', '', 'icon')))

    def world(self, status, session, map_link):
        bluemap = status.get('bluemap')
        module = '<p class="muted">BlueMap is not part of this release.</p>'
        if bluemap:
            render = {'idle': ('ok', 'Up to date'), 'working': ('warn', 'Rendering'),
                      'paused': ('warn', 'Paused')}.get(bluemap['render'], ('warn', 'No data'))
            if bluemap['enabled']:
                pause = 'off' if bluemap['render'] != 'paused' else 'on'
                module = (f'<p>{self.pill("ok", "Enabled")} {self.pill(*render)}</p><div class="row">'
                          f'<a class="mc-btn primary" href="{e(map_link)}" target="_blank" rel="noopener">Open live map</a>'
                          + self.button('/world', {'do': 'render', 'value': pause},
                                        'Pause rendering' if pause == 'off' else 'Resume rendering', session)
                          + self.button('/world', {'do': 'plugin', 'value': 'off'}, 'Disable map', session, 'danger',
                                        'Disable BlueMap? The server restarts and players are disconnected.', job=True)
                          + '</div>')
            else:
                module = (f'<p>{self.pill("bad", "Disabled")}</p>'
                          + self.button('/world', {'do': 'plugin', 'value': 'on'}, 'Enable map', session, 'primary',
                                        'Enable BlueMap? The server restarts and players are disconnected.', job=True))
            module += ('<p class="muted">Pausing frees CPU instantly and keeps the map online. Disabling removes the '
                       'plugin and its memory use. Lower view and simulation distance below to save more.</p>')
        world = status.get('world') or {}
        border = world.get('border') or 'Not available while the server is offline.'
        pregen = world.get('pregen')
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
            fields += f'<div class="field"><label for="{key}">{label}</label>{control}</div>'
        return (
            '<div class="grid two">'
            + self.card('Live map (BlueMap)', module, icon=self.texture('filled_map', '', 'icon'))
            + self.card('World border', f'<p class="mono">{e(border)}</p>'
                        + (f'<p class="mono muted">{e(pregen)}</p>' if pregen else '')
                        + '<p class="muted">Terrain inside the border is pre-generated, so exploring never makes the Pi '
                          'generate land while you play.</p>', icon=self.texture('spyglass', '', 'icon'))
            + '</div>'
            + self.card('Server settings', f'<form method="post" action="/world" data-async data-confirm="Save and restart the server? '
                        f'Players will be disconnected.">{self.csrf(session)}<input type="hidden" name="do" value="settings">'
                        f'<div class="fields">{fields}</div><p class="muted">Saving stops the server, writes server.properties '
                        'and starts it again. Authentication, ports and RCON are not editable here.</p>'
                        '<p><button class="mc-btn primary">Save and restart</button></p></form>',
                        icon=self.texture('crafting_table_front', '', 'icon')))

    def console(self, session, log):
        chips = ''.join(f'<button type="button" class="chip" data-command="{e(c)}">{e(c)}</button>' for c in CONSOLE_SHORTCUTS)
        return (
            '<section class="terminal" data-console>'
            '<div class="terminal-bar"><span class="lights"><i></i><i></i><i></i></span>'
            '<span class="terminal-title">paper@craft-pi: ~/server</span>'
            '<span class="terminal-status" data-console-status>live</span></div>'
            f'<pre class="terminal-log" data-console-log tabindex="0" aria-live="off">{e(log)}</pre>'
            f'<form class="terminal-input" method="post" action="/console" autocomplete="off">{self.csrf(session)}'
            '<label for="cmd" class="prompt">&gt;</label>'
            '<input id="cmd" name="command" type="text" maxlength="256" spellcheck="false" autocapitalize="off" '
            'placeholder="Type a command · ↑↓ history · Tab" required>'
            '<button class="mc-btn small">Run</button></form>'
            f'<div class="chips">{chips}</div>'
            '<p class="terminal-hint">Commands run as the server operator. stop, restart and reload are blocked: '
            'use the Dashboard buttons, which check for a clean shutdown.</p></section>')

    def backups(self, status, session):
        rows = [(f'{self.texture("chest_minecart", "", "icon")}<span class="mono">{e(b["name"])}</span>',
                 f'{b["size"] / 2**20:.0f} MiB', time.strftime('%Y-%m-%d %H:%M', time.localtime(b['mtime'])))
                for b in status['backups']]
        return (self.card('Create a backup', '<p class="muted">The server stops for about a minute, every file is hashed '
                          'and verified, and it starts again.</p>'
                          + self.button('/action', {'do': 'backup'}, 'Backup now', session, 'primary', job=True),
                          icon=self.texture('bundle', '', 'icon'))
                + self.card('Latest backups', self.table(('File', 'Size', 'Created'), rows, 'No backups yet')
                            + '<p class="muted">They live on the same microSD card: copy one off the Pi from time to time.</p>',
                            icon=self.texture('chest_minecart', '', 'icon')))

    def health(self, result):
        rows = []
        for line in (result.get('output') or '').splitlines():
            if line.startswith('== '):
                rows.append(f'<li class="section">{e(line[3:])}</li>')
                continue
            for prefix, kind, word in (('OK', 'ok', 'OK'), ('AVISO', 'warn', 'WARN'), ('FALLO', 'bad', 'FAIL')):
                if line.startswith(prefix):
                    rows.append(f'<li class="check {kind}"><span class="badge">{word}</span>{e(line[len(prefix):].strip())}</li>')
                    break
        summary = (self.pill('ok', 'All checks passed') if result.get('ok') else
                   self.pill('bad', 'Some checks failed'))
        return self.card('Server audit', f'<p>{summary}</p><ul class="checks">{"".join(rows)}</ul>'
                         '<p class="muted">Output of tools/audit.sh on the Pi (details in Spanish).</p>',
                         icon=self.texture('golden_apple', '', 'icon'))

    def account(self, session):
        return self.card('Change password', f'<form method="post" action="/account">{self.csrf(session)}'
                         '<label for="c">Current password</label><input id="c" type="password" name="current" '
                         'autocomplete="current-password" required><label for="n">New password (12+ characters)</label>'
                         '<input id="n" type="password" name="new" minlength="12" autocomplete="new-password" required>'
                         '<label for="r">Repeat new password</label><input id="r" type="password" name="repeat" minlength="12" '
                         'autocomplete="new-password" required><p><button class="mc-btn primary">Change password</button></p>'
                         '</form><p class="muted">Changing it signs out every other session.</p>', 'narrow',
                         icon=self.texture('writable_book', '', 'icon'))

    def job(self, job):
        elapsed = int(time.time() - job['started'])
        if job['state'] == 'running':
            return self.card(job['label'], f'<p class="big" data-job="{e(job["id"])}">Working… '
                             f'<span data-job-elapsed>{elapsed}</span> s</p>'
                             f'<div class="bar xp indeterminate"><div class="fill"></div></div>'
                             '<p class="muted">You can close this page: the operation keeps running on the Pi.</p>',
                             'narrow', icon=self.texture('clock_00', '', 'icon'))
        result = job['result'] or {}
        state = self.pill('ok', 'Finished') if result.get('ok') else self.pill('bad', 'Failed')
        return self.card(job['label'], f'<p>{state}</p><pre class="output">{e(result.get("output", ""))}</pre>'
                         '<p><a class="mc-btn" href="/" data-nav>Back to dashboard</a></p>',
                         icon=self.texture('clock_00', '', 'icon'))
