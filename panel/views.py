"""HTML del panel: componentes con estilo Minecraft y páginas. Sólo genera texto; no tiene estado.

Todo dato dinámico pasa por e(). No hay estilos ni scripts en línea: la CSP sólo permite
/static/app.css y /static/app.js. Las texturas de /assets/ se extraen en la Pi del cliente de
Minecraft (Mojang) y no forman parte del repositorio: sin ellas el panel usa equivalentes en CSS.
"""
import html
import random
import re
import time

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
    def __init__(self, assets=frozenset(), version=''):
        self.assets = set(assets)
        # Huella de app.css + app.js: cada despliegue cambia la URL y el navegador no usa la versión vieja.
        self.version = version

    # --- Componentes ------------------------------------------------------------------------
    def texture(self, name, alt='', cls='tex'):
        """Textura de Minecraft si está instalada; si no, nada (el CSS pone su equivalente)."""
        if f'{name}.png' not in self.assets:
            return ''
        return f'<img class="{cls}" src="/assets/{e(name)}.png" alt="{e(alt)}" width="16" height="16" loading="lazy">'

    def item_icon(self, identifier, cls='item-icon'):
        """Icono de un objeto o mob: textura de objeto, de bloque, o huevo de generación."""
        for name in (f'item_{identifier}', f'block_{identifier}', f'block_{identifier}_side', f'block_{identifier}_front',
                     f'block_{identifier}_top', f'item_{identifier}_spawn_egg'):
            if f'{name}.png' in self.assets:
                return f'<img class="{cls}" src="/assets/{e(name)}.png" alt="" width="16" height="16" loading="lazy">'
        return f'<span class="{cls} missing">{e(str(identifier)[:2].upper())}</span>'

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

    def button(self, action, fields, label, session, kind='', confirm=''):
        hidden = ''.join(f'<input type="hidden" name="{e(k)}" value="{e(v)}">' for k, v in fields.items())
        ask = f' data-confirm="{e(confirm)}"' if confirm else ''
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
            chip = '<a class="server-chip" href="/" data-nav data-live="chip"><span class="dot"></span><span class="chip-text">Server</span></a>'
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
            f'<main id="main" data-page="{e(slug)}" data-title="{e(title)} · Craft Pi Server">'
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
                                               'restart': 'Restart the server? Players will be disconnected.'}.get(verb, ''))
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

    def profile(self, p, session, map_base, spawn):
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
                                icon=self.texture('golden_apple', '', 'icon'))
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
            ('Last death', where(location.get('death')))], ''), icon=self.texture('compass_00', '', 'icon'))
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
                                      if p['online'] else ''), 'wide', icon=self.texture('chest_minecart', '', 'icon'))
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
        action = f'/players/{uuid}'
        modes = ''.join(f'<option value="{m}"{" selected" if m == vitals.get("gamemode") else ""}>{label}</option>'
                        for m, label in GAMEMODE_LABELS.items())
        online_only = '' if p['online'] else ' disabled'
        policy = (
            '<div class="row">'
            + (self.button(action, {'do': 'deop'}, 'Remove operator', session, 'danger', f'Remove operator rights from {name}?')
               if p['op'] else self.button(action, {'do': 'op'}, 'Make operator', session, '',
                                           f'Give {name} operator rights? They will be able to run any command.'))
            + (self.button('/players', {'do': 'remove', 'name': name}, 'Remove from whitelist', session, 'danger',
                           f'Remove {name} from the whitelist?') if p['whitelisted'] else '')
            + (self.button(action, {'do': 'pardon'}, 'Unban', session, 'primary') if p['banned'] else
               self.button(action, {'do': 'ban'}, 'Ban', session, 'danger', f'Ban {name}? They will be kicked and blocked.'))
            + '</div>'
            f'<form class="row gamemode" method="post" action="{e(action)}">{self.csrf(session)}<input type="hidden" name="do" value="gamemode">'
            f'<label for="gm">Game mode</label><select id="gm" name="mode">{modes}</select>'
            f'<button class="mc-btn"{online_only}>Apply</button></form>'
            '<div class="row">'
            + self.button(action, {'do': 'kick'}, 'Kick', session, 'danger', f'Kick {name} from the server?')
            + self.button(action, {'do': 'spawn'}, 'Teleport to spawn', session, '', f'Teleport {name} to the world spawn?')
            + self.button(action, {'do': 'heal'}, 'Heal & feed', session, 'primary')
            + '</div><p class="muted">Kick, teleport, heal and game mode need the player online. Every action is logged on the Pi.</p>')
        policy_card = self.card('Server policies', policy, icon=self.texture('command_block_front', '', 'icon'))
        return (header + '<div class="grid two">' + vitals_card + location_card + '</div>' + inventory_card + stats_card
                + '<div class="grid two">' + advancements_card + policy_card + '</div>'
                + '<p><a class="mc-btn" href="/players" data-nav>Back to players</a></p>')

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
                                    'Open 5 min and wait', session, 'small') if r['edition'] == 'bedrock' else ''))
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
            + self.card('Temporary opening for one player', f'<form method="post" action="/players">{self.csrf(session)}'
                        '<input type="hidden" name="do" value="open"><label for="oe">Edition</label>'
                        '<select id="oe" name="edition"><option value="bedrock">Bedrock</option><option value="java">Java</option></select>'
                        '<label for="on">Gamertag or name</label><input id="on" type="text" name="name" maxlength="16" required '
                        'pattern="[A-Za-z0-9_]{1,16}"><label for="mi">Minutes (max 15)</label>'
                        '<input id="mi" type="number" name="minutes" min="1" max="15" value="5">'
                        '<p class="muted">Turns the whitelist off until that player joins, adds them and turns it back on. '
                        'Anyone else who joins meanwhile is kicked when it closes.</p>'
                        '<p><button class="mc-btn">Open and wait</button></p></form>', icon=self.texture('clock_00', '', 'icon')))

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
                                        'Disable BlueMap? The server restarts and players are disconnected.')
                          + '</div>')
            else:
                module = (f'<p>{self.pill("bad", "Disabled")}</p>'
                          + self.button('/world', {'do': 'plugin', 'value': 'on'}, 'Enable map', session, 'primary',
                                        'Enable BlueMap? The server restarts and players are disconnected.'))
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
            + self.card('Server settings', f'<form method="post" action="/world" data-confirm="Save and restart the server? '
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
                          + self.button('/action', {'do': 'backup'}, 'Backup now', session, 'primary'),
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
