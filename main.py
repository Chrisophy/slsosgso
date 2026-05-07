# ==========================================
# 1. IMPORTS
# ==========================================
# Standard Libraries
import os
import re
import json
import sqlite3
import hashlib
import threading
import contextlib
import xml.etree.ElementTree as ET
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import random

# Drittanbieter
import requests
import urllib3

# Kivy Core
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.core.text import LabelBase
from kivy.utils import platform
from kivy.metrics import dp, Metrics
from kivy.cache import Cache
from kivy.animation import Animation
from kivy.graphics import Color, Rectangle, Line, RoundedRectangle
from kivy.properties import StringProperty, BooleanProperty, NumericProperty

# Kivy Widgets & Layouts
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.image import AsyncImage
from kivy.uix.modalview import ModalView
from kivy.uix.textinput import TextInput
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.recycleview import RecycleView
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.recyclegridlayout import RecycleGridLayout
from kivy.uix.image import Image
 
try:
    from jnius import autoclass, cast
except:
    autoclass = None
    cast = None

# ==========================================
# 2. KONFIGURATION & GLOBALE EINSTELLUNGEN
# ==========================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
Cache.register('kv.image', limit=600, timeout=7200)

# Pfade
BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, 'movie_metadata.db')
CACHE_DIR = os.path.join(BASE_DIR, 'img_cache')

if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

# UI Font & Style
try:
    LabelBase.register(
        name='Roboto',
        fn_regular='Roboto-ExtraBold.ttf',
        fn_bold='Roboto-Bold.ttf'
    )
    Label.font_name = "Roboto"
except:
    pass
    
Metrics.fontscale = 1.0
Window.clearcolor = (0.92, 0.92, 0.92, 1)

# Exit-Sperre 
Window.exit_on_escape = False

# ==========================================
# 3. DATENBANK & HILFSFUNKTIONEN
# ==========================================
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS movie_cache
                     (search_title TEXT PRIMARY KEY, plot TEXT, rating TEXT, 
                      poster_url TEXT, local_poster TEXT, genres_json TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS favorites (title TEXT PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS film_list 
                     (hash_id TEXT PRIMARY KEY, title TEXT, video_url TEXT, 
                      search_name TEXT, year TEXT, genres_json TEXT)''')
        conn.commit()

init_db()

@contextlib.contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()

# ==========================================
# 4. CUSTOM WIDGETS
# ==========================================

class KittScanner(Widget):
    active = BooleanProperty(False)
    scanner_x = NumericProperty(0)
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint_y = None
        self.height = dp(4)
        with self.canvas:
            Color(0.85, 0.85, 0.85, 1)
            self.bg_rect = Rectangle(pos=self.pos, size=self.size)
            self.glow_color = Color(0.1, 0.1, 1, 1)
            self.scanner_rect = Rectangle(pos=self.pos, size=(dp(120), self.height))
        self.bind(pos=self.update_canvas, size=self.update_canvas)
        self.bind(scanner_x=self._update_scanner_pos)
        
    def _update_scanner_pos(self, instance, value):
        self.scanner_rect.pos = (self.x + value, self.y)
        
    def update_canvas(self, *args):
        self.bg_rect.pos = self.pos
        self.bg_rect.size = self.size
        self._update_scanner_pos(None, self.scanner_x)
        
    def on_active(self, inst, value):
        if value:
            self.glow_color.a = 1
            self.start_animation()
        else:
            Animation.stop_all(self)
            self.glow_color.a = 0
            
    def start_animation(self, *args):
        if not self.active:
            return None
        else:
            limit = self.width - dp(120)
            if limit < 0:
                limit = 100
            anim = Animation(scanner_x=limit, duration=0.7,
            t='in_out_quad') + Animation(scanner_x=0, duration=0.7, t='in_out_quad')
            anim.bind(on_complete=self.start_animation)
            anim.start(self)

class MovieItem(ButtonBehavior, BoxLayout):
    title = StringProperty('')
    thumb = StringProperty('')
    video_url = ''
    plot = StringProperty('Lade Infos...')
    rating = StringProperty('N/A')
    is_focused = BooleanProperty(False)
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        self.padding = dp(5)
        self.spacing = dp(2)

        with self.canvas.before:
            self.bg_color_instr = Color(0.9, 0.9, 0.9, 1) 
            self.bg_rect = Rectangle(pos=self.pos, size=self.size)
            self.focus_color = Color(0.2, 0.5, 0.9, 0)
            self.focus_line = Line(rectangle=(self.x, self.y, self.width, self.height), width=dp(2))

        self.bind(pos=self.update_canvas, size=self.update_canvas)        
        
        self.img = AsyncImage(allow_stretch=True, keep_ratio=True, size_hint=(1, 0.8))
        self.title_label = Label(text='', bold=True, font_size=dp(12), 
                                 color=(0, 0.28, 0.74, 1), halign='center', size_hint=(1, 0.2))
        self.title_label.bind(size=lambda l, s: setattr(l, 'text_size', (s[0], None)))        
        
        self.add_widget(self.img)
        self.add_widget(self.title_label)        
        self.bind(title=self.title_label.setter('text'))

    def update_canvas(self, *args):
        self.bg_rect.pos = self.pos
        self.bg_rect.size = self.size
        self.focus_line.rectangle = (self.x, self.y, self.width, self.height)

    def on_is_focused(self, inst, value):
        self.focus_color.a = 1 if value else 0

    def on_thumb(self, instance, value):
        if value: self.img.source = value

    def update_bg(self, *args):
        self.bg_color_instr.rgba = (1, 1, 1, 1)
        self.shadow_color_instr.a = 0.03
        
        self.bg_rect.pos = self.pos
        self.bg_rect.size = self.size
        self.shadow.pos = (self.x+2, self.y-2)
        self.shadow.size = self.size
        if hasattr(self, 'focus_line'):
            self.focus_line.rectangle = (self.x, self.y, self.width, self.height)

    def refresh_view_attrs(self, rv, index, data):
        new_thumb = data.get('thumb', '')
        if self.img.source != new_thumb:
            self.img.source = new_thumb
        self.is_focused = data.get('is_focused', False)
        self.title = data.get('title', '')
        self.thumb = data.get('thumb', '')
        self.video_url = data.get('video_url', '')
        self.plot = data.get('plot', 'Keine Beschreibung verfügbar.')
        self.rating = str(data.get('rating', 'N/A'))

        return super().refresh_view_attrs(rv, index, data)

    def on_release(self):
        p = self.parent
        while p:
            if hasattr(p, 'show_details'):
                p.show_details(self)
                break
            p = p.parent
            
# ==========================================
# 5. HAUPT-WIDGET (MEDIAWIDGET)
# ==========================================

class MediaWidget(BoxLayout):
    TMDB_API_KEY = '60b3801a9e76b5706ee2a432f06423e6'
    GENRES_MAP = {28: 'Action', 12: 'Abenteuer',
    16: 'Animation', 35: 'Komödie', 80: 'Krimi', 99: 'Doku',
    18: 'Drama', 10751: 'Familie', 14: 'Fantasy', 36: 'Historie',
    27: 'Horror', 10402: 'Musik', 9648: 'Mystery',
    10749: 'Romanze', 878: 'Sci-Fi', 10770: 'TV-Film',
    53: 'Thriller', 10752: 'Krieg', 37: 'Western'}
    all_movies = []
    current_genre = 'Alle'
    focus_index = (-1)

    EPG_MAP = {
        "DasErste.de": "71",
        "ZDF.de": "37",
        "3sat.de": "56",
        "ARTE.de": "58",
        "Kika.de": "57",
        "phoenix.de": "194",
        "One.de": "146",
        "ZDFneo.de": "659",
        "ZDFinfo.de": "276",
        "tagesschau24.de": "100",
        "ARD-alpha.de": "104",
        "BRFernsehen.de": "51",
        "BR.de": "51", 
        "HR.de": "49",
        "MDRSachsen.de": "48",
        "MDRS-Anhalt.de": "48",
        "MDRThueringen.de": "48",
        "NDRFernsehen.de": "47",
        "ndr.de": "47", 
        "RadioBremen.de": "53",
        "RBBBerlin.de": "52",
        "SWRFernsehen.de": "50",
        "SWRFernsehen-rp.de": "50",
        "WDRFernsehen.de": "46",
        "DF1.de": "12190",
        "WELT.de": "175",
        "BILD.de": "12191",
        "euronews.com": "68",
        "Anixe.de": "537",
        "DeLuxeMusic.de": "291",
        "DeLuxeMusicDance.de": "12201",
        "DeLuxeMusicRap.de": "12204",
        "SchlagerDeluxe.de": "12181",
        "DeutschesMusikFernsehen.de": "657",
        "One1MusicTV.de": "12214",
        "Zwei2MusicTV.de": "12215",
        "FolxTV.de": "12101",
        "Nickelodeon.de": "107",
        "RiC.de": "875",
        "Dokusat.de": "12218",
        "WeltderWunder.de": "4005",
        "morethansports.de": "259",
        "daznfastplus.de": "12199",
        "SPORTBILD.de": "12197",
        "AUTOBILD.de": "12198",
        "123.tv.de": "563",
        "Pearl.tv.de": "1032",
        "QVC.de": "311",
        "QVCPlus.de": "551",
        "sonnenklar.tv": "513",
        "BibelTV.de": "233",
        "BibelTVImpuls.de": "12165",
        "BibelTVMusik.de": "12166",
        "KTV.de": "553",
        "EWTN.de": "1111",
        "ERFeins.de": "1103",
        "wir24.tv": "12192",
        "swr3.de": "12035",
        "Dasding.de": "12028",
        "NDR1RadioMV.de": "12015",
        "oe3.at": "12093"
    }
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        self.pending_loads = 0
        self.detail_popup = None
        self.main_popup = None
        self.executor = ThreadPoolExecutor(max_workers=12)
        self.session = requests.Session()
        
        self.tmdb_cache = {}

        self.favorites = []
        self.epg_data = {}
        self.load_favorites()
        
        self.update_queue = []
        self.update_scheduled = False
        self.setup_ui()
                
        # EPG zeitversetzt laden
        Clock.schedule_once(self.fetch_epg, 2)

    def _download_image(self, url, path):
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                with open(path, "wb") as f:
                    f.write(r.content)

                def apply(dt):
                    if self._requested_source == url:
                        self._load_texture(path)
                Clock.schedule_once(apply)
        except:
            pass

    def _block_close(self, *args, **kwargs):
        return True

    def load_favorites(self):
        try:
            with get_db_connection() as conn:
                self.favorites = [row[0] for row in conn.execute("SELECT title FROM favorites")]
        except: pass

    def fetch_epg(self, *args):
        def _bg_epg():
            url = "https://raw.githubusercontent.com/Chrisophy/VLTE/refs/heads/main/guide.xml"
            try:
                headers = {'User-Agent': 'Mozilla/5.0'}
                r = requests.get(url, headers=headers, timeout=15)
                if r.status_code == 200:
                    root = ET.fromstring(r.content)
                    new_epg_data = {}
                    
                    for prog in root.findall('.//programme'):
                        channel_id = prog.get('channel')
                        start_str = prog.get('start')
                        title_node = prog.find('title')
                        desc_node = prog.find('desc')
                        
                        if channel_id and start_str and title_node is not None:
                            start_time = start_str.replace(' ', '').replace('+', '')[:14]
                            
                            if channel_id not in new_epg_data:
                                new_epg_data[channel_id] = []
                            
                            new_epg_data[channel_id].append({
                                'start': start_time,
                                'title': title_node.text,
                                'desc': desc_node.text if desc_node is not None else "" 
                            })
                    
                    self.epg_data = new_epg_data
                    print(f"EPG GELADEN: {len(self.epg_data)} Kanäle mit Details.")
            except Exception as e:
                print(f"EPG FEHLER: {e}")
    
        threading.Thread(target=_bg_epg, daemon=True).start()

    def get_cached_data(self, key):
        try:
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("SELECT plot, rating, local_poster, genres_json FROM movie_cache WHERE search_title=?", (key,))
                res = c.fetchone()
                if res:
                    return {
                        "plot": res[0], 
                        "rating": res[1], 
                        "local_path": res[2], 
                        "genres_list": json.loads(res[3])
                    }
        except Exception as e:
            print(f"Cache-Lese-Fehler: {e}")
        return None

    def save_to_db(self, key, data, local_path):
        try:
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("INSERT OR REPLACE INTO movie_cache VALUES (?, ?, ?, ?, ?, ?)",
                          (key, data['plot'], data['rating'], data.get('poster',''),
                           local_path, json.dumps(data.get('genres_list', []))))
                conn.commit()
        except Exception as e:
            print(f"Cache-Schreib-Fehler: {e}")
        
    def setup_ui(self):
        header = BoxLayout(orientation='vertical',
        size_hint_y=None, height=230, padding=10, spacing=5)
        
        with header.canvas.before:
            self.header_bg_color = Color(0.92, 0.92, 0.92, 1)
            self.h_rect = Rectangle(size=header.size, pos=header.pos)
            ## Optionaler Schatten für den Header (korrigiert):
            #Color(0, 0, 0, 0.01)
            #self.h_shadow = Rectangle(pos=(header.x+2, header.y-2), size=header.size)

        header.bind(pos=self._update_header_bg, size=self._update_header_bg)

        self.scanner = KittScanner()
        header.add_widget(self.scanner)
        top_row = BoxLayout(size_hint_y=None, height=80, spacing=15)
        
        self.close_btn = Button(text='[b]X[/b]', markup=True,
        size_hint=(None, None), size=(dp(30), dp(30)),
        pos_hint={'center_y': 0.5}, background_normal='',
        background_color=(0.2, 0.5, 0.9, 1), font_size = dp(12))
        self.close_btn.bind(on_release=self.close_app)
        
        logo = Label(
            text='  [color=000000][b]&bl;[/b][/color][color=0048ba][b]G[/b][/color][color=0040a7][b]E[/b][/color][color=003994][b]Z[/b][/color][color=000000][b]&br;[/b][/color] [color=003282][b]K[/b][/color][color=002b6f][b]i[/b][/color][color=00245d][b]N[/b][/color][color=001c4a][b]O[/b][/color]',
            markup=True,
            font_size=dp(24),
            size_hint_x=None,
            width=dp(150),
            halign='left',
            valign='middle'
        )
        logo.bind(size=logo.setter('text_size'))        
        
        self.search_bar = TextInput(hint_text='Film suchen...',
        multiline=False, size_hint_x=0.6,
        background_color=(1, 1, 1, 1),
        foreground_color=(0, 0, 0, 1),
        hint_text_color=(0, 0, 0.4, 1),
        cursor_color=(0, 0, 0.4, 1),
        font_size = dp(20),
        padding_y=(12, 12), keyboard_suggestions=False)
        self.search_bar.bind(text=self.filter_results)
        top_row.add_widget(self.close_btn)
        top_row.add_widget(logo)
        top_row.add_widget(self.search_bar)
        self.genre_scroll = ScrollView(size_hint_y=None,
        height=110, do_scroll_y=False, scroll_type=['bars', 'content'])
        self.genre_layout = BoxLayout(size_hint_x=None, spacing=15, padding=(10, 5))
        self.genre_layout.bind(minimum_width=self.genre_layout.setter('width'))
        
        s_genres = sorted(self.GENRES_MAP.values())
        genres = ['Alle', 'Favoriten', 'Live-TV'] + [g for g in
        s_genres if g not in ['Alle', 'Live-TV']] + ['Sonstige']
        
        self.genre_buttons = []
        for g in genres:
            class GenreButton(ButtonBehavior, BoxLayout):
                pass

            btn = GenreButton(
                orientation='vertical',
                size_hint=(None, 1),
                width=dp(140),
                padding=[dp(10), dp(5)],
                spacing=dp(2)
            )
            
            with btn.canvas.before:
                btn.bg_color = Color(0.92, 0.94, 0.96, 1)
                btn.bg_rect = Rectangle(pos=btn.pos, size=btn.size)
            
            btn.bind(pos=self._update_btn_bg, size=self._update_btn_bg)
            
            lbl_name = Label(text=f"[b]{g}[/b]", markup=True, halign='center')
            lbl_name.bind(size=lbl_name.setter('text_size'))
            lbl_count = Label(text="(0)", font_size = dp(11),
            color=(0.7, 0.7, 0.7, 1), halign='center')
            lbl_count.bind(size=lbl_count.setter('text_size'))
            
            btn.add_widget(lbl_name)
            btn.add_widget(lbl_count)
            btn.name_label = lbl_name
            btn.count_label = lbl_count
            btn.bind(on_release=self.select_genre)
            self.genre_layout.add_widget(btn)
            self.genre_buttons.append(btn)
            
        self.genre_scroll.add_widget(self.genre_layout)
        header.add_widget(top_row)
        header.add_widget(self.genre_scroll)
        self.add_widget(header)
        container = AnchorLayout(anchor_x='center', anchor_y='top')

        self.rv = RecycleView(size_hint_x=None, width=Window.width)
        self.layout_manager = RecycleGridLayout(cols=3,
        spacing=10, padding=[15, 15, 15, 15],
        default_size=(349, 400), default_size_hint=(None, None), size_hint_y=None)
        self.layout_manager.bind(minimum_height=self.layout_manager.setter('height'))
        self.rv.add_widget(self.layout_manager)
        self.rv.viewclass = 'MovieItem'
        container.add_widget(self.rv)
        self.add_widget(container)
        self.bind(on_parent=self._manage_keyboard)
        Window.bind(on_resize=self._on_window_resize)
        Window.bind(on_key_down=self._on_key_down)
        Window.bind(on_joy_button_down=self._on_joy_button_down)
        self._on_window_resize(Window, Window.width, Window.height)
        self.current_genre = 'Alle'
 
        Clock.schedule_once(lambda dt: self._update_visual_focus(self.focus_index), 0.1)
        
        Clock.schedule_once(self.load_local_films, 0.2)
                
    def _manage_keyboard(self, instance, parent):
        if parent:
            Window.bind(on_key_down=self._on_key_down)
            Window.bind(on_joy_button_down=self._on_joy_button_down)
        else:
            Window.unbind(on_key_down=self._on_key_down)
            Window.unbind(on_joy_button_down=self._on_joy_button_down)
            
    def _on_joy_button_down(self, window, stick_id, button_id):
        if button_id in [11, 12, 13, 14, 0]:
            key_map = {11: 273, 12: 274, 13: 276, 14: 275, 0: 13}
            simulated_key = key_map.get(button_id)
            if simulated_key:
                return self._on_key_down(window, simulated_key, None, None, None)
        if button_id == 4:
            #self.close_app()
            return True
        else:
            return True
            
    def _on_window_resize(self, instance, width, height):
        self.rv.width = width
        new_cols = max(1, int((width - 40) / 330))
        self.layout_manager.cols = new_cols
        
    def _update_header_bg(self, instance, value):
        self.h_rect.pos = instance.pos
        self.h_rect.size = instance.size

        if hasattr(self, 'h_shadow'):
            self.h_shadow.pos = (instance.x + 2, instance.y - 2)
            self.h_shadow.size = instance.size

    def _update_btn_bg(self, instance, value):
        instance.bg_rect.pos = instance.pos
        instance.bg_rect.size = instance.size
        
    def _on_key_down(self, window, key, *args):
        if key == 27:
            return True

        if self.search_bar.focus and key not in [273, 274, 275, 276]:
            return False

        cols = self.layout_manager.cols
        old_idx = self.focus_index
        total = len(self.rv.data)
        num_genres = len(self.genre_buttons)

        if key == 273:
            if self.focus_index >= cols: self.focus_index -= cols
            elif self.focus_index >= 0: self.focus_index = -3 # Zu Genres
            elif self.focus_index <= -3: self.focus_index = -1 # Zur Suche

        elif key == 274:
            if self.focus_index in [-1, -2]: self.focus_index = -3
            elif self.focus_index <= -3: 
                for item in self.rv.data: item['is_focused'] = False
                self.focus_index = 0

            elif self.focus_index >= 0 and self.focus_index + cols < total:
                self.focus_index += cols

        elif key == 275:
            if self.focus_index == -2: self.focus_index = -1 # Vom X zur Suche
            elif self.focus_index == -1: self.focus_index = -3 # Von Suche zu Genres
            elif self.focus_index <= -3: # Navigation in Genres
                curr_pos = abs(self.focus_index) - 3
                if curr_pos < num_genres - 1: self.focus_index -= 1
            elif self.focus_index >= 0 and self.focus_index < total - 1:
                self.focus_index += 1

        elif key == 276:
            if self.focus_index == -1: self.focus_index = -2 # Von Suche zum X
            elif self.focus_index < -3: self.focus_index += 1 # In Genres nach links
            elif self.focus_index == -3: self.focus_index = -1 # Vom ersten Genre zur Suche
            elif self.focus_index > 0: self.focus_index -= 1

        elif key in (13, 32):
            if self.focus_index == -2: self.close_app()
            elif self.focus_index == -1: self.search_bar.focus = True
            else: self._activate_focus()
            return True

        if old_idx != self.focus_index:
            self._update_visual_focus(old_idx)
            return True
        return False

    def _update_visual_focus(self, old_idx):
        self.close_btn.background_color = (0, 0, 0, 1) if self.focus_index == -2 else (0.2, 0.5, 0.9, 1)
        self.search_bar.background_color = (0.2, 0.5, 0.9, 1) if self.focus_index == -1 else (0.2, 0.2, 0.2, 1)
        
        for i, btn in enumerate(self.genre_buttons):
            btn.canvas.after.clear()
            target_idx = -3 - i

            btn_name = btn.name_label.text.replace('[b]', '').replace('[/b]', '')

            if btn_name == self.current_genre:
                btn.bg_color.rgba = (0.2, 0.5, 0.9, 1)
            else:
                btn.bg_color.rgba = (0.12, 0.12, 0.12, 1)
            
            if self.focus_index == target_idx:
                with btn.canvas.after:
                    Color(1, 1, 1, 0.8)
                    Line(rectangle=(btn.x, btn.y, btn.width, btn.height), width=dp(2))
                self.genre_scroll.scroll_to(btn)

        for i, item in enumerate(self.rv.data):
            should_be_focused = (i == self.focus_index)
            if item.get('is_focused', False) != should_be_focused:
                item['is_focused'] = should_be_focused

        if 0 <= self.focus_index < len(self.rv.data):
            self.scroll_to_index(self.focus_index)

    def select_genre(self, btn):
        self.current_genre = btn.name_label.text.replace('[b]', '').replace('[/b]', '')
        self.filter_results() 
        self._update_visual_focus(self.focus_index)
                
    def _activate_focus(self):
        if self.focus_index == (-2):
            self.search_bar.focus = True
        else:
            if self.focus_index <= (-3):
                index = abs(self.focus_index) - 3
                if 0 <= index < len(self.genre_buttons):
                    self.select_genre(self.genre_buttons[index])
            else:
                if 0 <= self.focus_index < len(self.rv.data):
                    class Obj:
                        pass
                    m = Obj()
                    data = self.rv.data[self.focus_index]
                    m.title = data['title']
                    m.video_url = data['video_url']
                    m.rating = data['rating']
                    m.plot = data['plot']
                    self.show_details(m)
                    
    def scroll_to_index(self, index):
        if index < 0:
            return None
        else:
            total_items = len(self.rv.data)
            if total_items <= 1:
                return None
            else:
                cols = self.layout_manager.cols
                rows = (total_items + cols - 1) // cols
                current_row = index // cols
                if rows > 1:
                    target_scroll = 1.0 - current_row / float(rows - 1)
                    self.rv.scroll_y = max(0, min(1, target_scroll))
        
    def filter_results(self, *args):
        query = self.search_bar.text.lower().strip()
        genre = self.current_genre
        
        filtered = []
        for m in self.all_movies:
            is_tv = 'Live-TV' in m.get('genres_list', [])
            
            if query and query not in m.get('title', '').lower():
                continue

            if genre == 'Favoriten':
                if m.get('title') not in self.favorites: continue
            elif genre == 'Live-TV':
                if not is_tv: continue
            elif genre == 'Alle':
                if is_tv: continue
            elif genre == 'Sonstige':
                g_list = m.get('genres_list', [])
                if is_tv or (g_list and g_list != ['Mediathek']): continue
            else:
                if genre not in m.get('genres_list', []): continue
            
            filtered.append(m)
        
        self.rv.data = filtered
        self.update_genre_counts()
        
    def fetch_data(self, *args):
        threading.Thread(target=self._do_fetch, daemon=True).start()
        
    def _apply_data(self, movies):
        """Initiales Laden der Daten."""
        self.all_movies = movies
        self.filter_results()
        self.load_extra_info(movies)
        
    def load_extra_info(self, movies):
        self.pending_loads = len(movies)
        if self.pending_loads > 0:
            self.scanner.active = True        
        visible_titles = {m['search'] for m in self.rv.data}
        sorted_load = [m for m in movies if m['search'] in visible_titles] + \
                      [m for m in movies if m['search'] not in visible_titles]
        for m in sorted_load:
            try:
                self.executor.submit(self._fetch_single_movie_info, m)
            except:
                break
                
    def clean_title(self, title):
        year_match = re.search(r'(\d{4})', title)
        year = year_match.group(1) if year_match else None
        
        clean = title
        markers = [' - Spielfilm', ' – Spielfilm', ' - Spiellfilm',
        ' – Spiellfilm', ', Spielfilm', ' Österreich', ', Deutschland',
        ', Schweiz', ', Belgien', ', Frankreich', ', Spanien',
        ', Niederlande', ', Irland', ', Luxemburg', ', Italien', ', USA',
        ', Kosovo', ', Großbritannien', ', Tschechische Republik',
        ', Norwegen', ', BRD', ', Dänemark', ', Italien', ', Australien',
        ', Schweden', ', Video:', ', Präsentiert:', ', Kurzfilm',
        ' Fernsehfilm', ' Heimatfilm', ' - Thriller', ' - Drama',
        ' - Aufstand der Pferdefreunde Spielfilm', '«', '»']
        for marker in markers:
            clean = re.split(marker, clean, flags=re.I)[0]

        clean = re.sub(r'^(Spielfilm|Spiellfilm):\s*', '', clean, flags=re.I)
        clean = clean.replace('–', '-').replace('—', '-')
        clean = re.sub(r'\(.*?\)', '', clean) # Entfernt (2024)
        # Entferne die Endung
        clean = clean.strip().rstrip('( ,.-_–—»')
        return (clean, year)
        
    def get_tmdb_data(self, title, year=None):
        cache_key = f"{title}_{year}"
        if cache_key in self.tmdb_cache:
            return self.tmdb_cache[cache_key]
        try:
            import time
            time.sleep(0.03)
            params = {
                "api_key": self.TMDB_API_KEY,
                "query": title,
                "language": "de-DE",
                "include_adult": "false"
            }
            if year:
                params["year"] = year
            res = self.session.get(
                "https://api.themoviedb.org/3/search/movie",
                params=params,
                timeout=5
            ).json()
            results = res.get('results', [])
            if not results and year:
                params.pop("year")
                res = self.session.get(
                    "https://api.themoviedb.org/3/search/movie",
                    params=params,
                    timeout=5
                ).json()
                results = res.get('results', [])
            if results:
                movie = results[0]
                path = movie.get('poster_path')
                g_ids = movie.get('genre_ids', [])
                genres_list = [
                    self.GENRES_MAP[gid]
                    for gid in g_ids
                    if gid in self.GENRES_MAP
                ]
                result = {
                    "poster": f"https://image.tmdb.org/t/p/w154{path}" if path else "",
                    "plot": movie.get('overview', 'Keine Beschreibung verfügbar.'),
                    "rating": str(movie.get('vote_average', 'N/A')),
                    "genres_list": genres_list,
                    "genres_str": ", ".join(genres_list[:2])
                }
                self.tmdb_cache[cache_key] = result
                return result
        except Exception as e:
            print("TMDB Fehler:", e)
        return None
        
    def _do_fetch(self):
        movies = list(self.all_movies)
    
        api_url = 'https://mediathekviewweb.de/api/query'
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Accept-Encoding': 'gzip, deflate'
        }
    
        queries = [
            'Spielfilm',
            'Spielfilm - Highlights',
            'Film',
            'Filme',
            'Kino - Filme'
        ]
    
        results_map = {}  # key = video_url (oder fallback title)
    
        try:
            # ==========================================
            # 1. QUERIES NACHEINANDER ABARBEITEN
            # ==========================================
            for q in queries:
    
                payload = {
                    'queries': [
                        {'fields': ['topic'], 'query': q}
                    ],
                    'size': 2000,
                    'sortBy': 'timestamp',
                    'sortOrder': 'desc'
                }
    
                r = self.session.post(api_url, json=payload, headers=headers, timeout=10)
    
                if r.status_code != 200:
                    continue
    
                results = r.json().get('result', {}).get('results', [])
    
                # ==========================================
                # 2. ERGEBNISSE SAMMELN + DEDUPE
                # ==========================================
                for m in results:
                    url = m.get('url_video', '')
                    title = m.get('title', '')
    
                    if not url:
                        continue
    
                    # Dedupe-Key
                    key = url
    
                    if key in results_map:
                        continue
    
                    clean_name, prod_year = self.clean_title(title)
    
                    if not clean_name:
                        continue
    
                    # EINZIGES FILTERKRITERIUM (optional minimal)
                    if m.get('duration', 0) < 4680:
                        continue
    
                    results_map[key] = {
                        'title': clean_name,
                        'orig': title,
                        'year': prod_year,
                        'thumb': '',
                        'video_url': url,
                        'search': clean_name,
                        'plot': 'Lade Infos...',
                        'rating': 'N/A',
                        'genres_list': ['Mediathek']
                    }
    
        except Exception as e:
            print(f'Web-API Fehler: {e}')
    
        # ==========================================
        # 3. IN LISTE ÜBERFÜHREN + DB SPEICHERN
        # ==========================================
        new_movies = []
    
        try:
            with get_db_connection() as conn:
                for key, movie in results_map.items():
    
                    movie_clean = movie.copy()
                    movie_clean.pop('orig', None)
    
                    new_movies.append(movie_clean)
    
                    conn.execute(
                        "INSERT OR REPLACE INTO film_list VALUES (?,?,?,?,?,?)",
                        (
                            hashlib.md5(movie['video_url'].encode()).hexdigest(),
                            movie['title'],
                            movie['video_url'],
                            movie['search'],
                            str(movie['year']),
                            json.dumps(movie['genres_list'])
                        )
                    )
    
                conn.commit()
    
        except Exception as e:
            print(f'DB Fehler: {e}')
    
        # ==========================================
        # 4. IPTV (UNVERÄNDERT)
        # ==========================================
        iptv_url = "https://raw.githubusercontent.com/jnk22/kodinerds-iptv/master/iptv/clean/clean_tv_main.m3u"
    
        try:
            response = self.session.get(iptv_url, timeout=5)
    
            if response.status_code == 200:
                lines = response.text.splitlines()
    
                for i in range(len(lines)):
                    if lines[i].startswith("#EXTINF"):
    
                        logo_match = re.search(r'tvg-logo="([^"]+)"', lines[i])
                        epg_match = re.search(r'tvg-id="([^"]+)"', lines[i])
                        name = lines[i].split(',')[-1].strip()
    
                        if i + 1 < len(lines) and lines[i+1].startswith("http"):
                            url = lines[i+1].strip()
    
                            if not any(m['video_url'] == url for m in new_movies):
    
                                new_movies.append({
                                    'title': f"[TV] {name}",
                                    'thumb': logo_match.group(1) if logo_match else "",
                                    'video_url': url,
                                    'search': name,
                                    'epg_id': epg_match.group(1) if epg_match else "",
                                    'plot': 'Live-TV Sender',
                                    'rating': 'LIVE',
                                    'genres_list': ['Live-TV']
                                })
    
        except Exception as e:
            print(f'IPTV Fehler: {e}')
    
        # ==========================================
        # 5. SORT + UI UPDATE
        # ==========================================
        new_movies.sort(key=lambda x: x['title'].lower())
    
        Clock.schedule_once(lambda dt: self._apply_data(new_movies))
        
    def _preload_tv_logo(self, url):
        try:
            filename = hashlib.md5(url.encode()).hexdigest() + ".jpg"
            path = os.path.join(CACHE_DIR, filename)
            if not os.path.exists(path):
                r = self.session.get(url, timeout=5)
                if r.status_code == 200:
                    with open(path, "wb") as f:
                        f.write(r.content)
        except:
            pass

    def _finish_load(self):
        self.pending_loads -= 1
        if self.pending_loads <= 0:
            self.scanner.active = False
        self.queue_update()

    def _fetch_single_movie_info(self, movie_dict):
        try:
            if 'Live-TV' in movie_dict.get('genres_list', []):
                Clock.schedule_once(lambda dt: self._finish_load())
                return

            target_title = movie_dict['search']
            year = movie_dict.get('year')
            cache_key = f"{target_title}_{year}"
        
            cached = self.get_cached_data(cache_key)
            if cached:
                data = cached
                path_to_use = cached['local_path']
            else:
                data = self.get_tmdb_data(target_title, year)
                path_to_use = os.path.join(os.path.dirname(__file__), 'placeholder.png')
                if data and data.get('poster'):
                    path_to_use = self._download_poster_sync(data['poster'])
                    self.save_to_db(cache_key, data, path_to_use)

            def update_ui_item(dt):
                if data:
                    movie_dict.update({
                        'thumb': path_to_use,
                        'plot': data.get('plot', 'Keine Info.'),
                        'rating': data.get('rating', 'N/A'),
                        'genres_list': data.get('genres_list', [])
                    })
                else:
                    movie_dict['thumb'] = path_to_use
                
                self._finish_load()

            Clock.schedule_once(update_ui_item)

        except Exception as e:
            print("THREAD ERROR:", e)
            Clock.schedule_once(lambda dt: self._finish_load())

    def queue_update(self):
        if self.update_scheduled:
            return
        self.update_scheduled = True
        Clock.schedule_once(self.apply_updates, 0.5)

    def _download_poster_sync(self, url):
        try:
            filename = hashlib.md5(url.encode()).hexdigest() + ".jpg"
            path = os.path.join(CACHE_DIR, filename)
            if not os.path.exists(path):
                r = self.session.get(url, timeout=5)
                if r.status_code == 200:
                    with open(path, "wb") as f:
                        f.write(r.content)
            return path
        except Exception as e:
            print(f"Poster Download Fehler: {e}")
            return os.path.join(os.path.dirname(__file__), 'placeholder.png')

    def update_genre_counts(self):
        counts = {"Alle": 0, "Sonstige": 0, "Live-TV": 0, "Favoriten": len(self.favorites)}
        for g in self.GENRES_MAP.values(): counts[g] = 0

        for m in self.all_movies:
            genres = m.get('genres_list', [])
            if 'Live-TV' in genres:
                counts["Live-TV"] += 1
                continue
            
            counts["Alle"] += 1
            if not genres or genres == ['Mediathek']:
                counts["Sonstige"] += 1
            else:
                for g in genres:
                    if g in counts: counts[g] += 1
 
        for btn in self.genre_buttons:
            name = btn.name_label.text.replace('[b]', '').replace('[/b]', '')
            if name in counts:
                btn.count_label.text = f"({counts[name]})"

    def toggle_favorite(self, title):
        try:
            with get_db_connection() as conn:
                if title in self.favorites:
                    conn.execute("DELETE FROM favorites WHERE title=?", (title,))
                    self.favorites.remove(title)
                else:
                    conn.execute("INSERT INTO favorites VALUES (?)", (title,))
                    self.favorites.append(title)

            self.update_genre_counts() 
            self.filter_results()
        except Exception as e: 
            print(f"Favoriten-Fehler: {e}")

    def apply_updates(self, dt):
        """Aktualisiert nur die UI, ohne die Datenstruktur zu zerstören."""
        self.update_scheduled = False

        self.rv.refresh_from_data()
        self.update_genre_counts()
        
    def show_details(self, movie):
        if not movie: return

        # 1. Basis-Daten sammeln
        genres_text = ""
        for item in self.all_movies:
            if item['title'] == movie.title:
                if item.get('genres_list'):
                    genres_text = ", ".join(item['genres_list'])
                break
        
        is_fav = movie.title in self.favorites
        current_program = "Keine Programminfos verfügbar"
        epg_plot = movie.plot

        # 2. EPG-Speziallogik für Live-TV
        if "[TV]" in movie.title:
            current_movie_data = next((m for m in self.all_movies if m['title'] == movie.title), None)
            if current_movie_data and 'epg_id' in current_movie_data:
                m3u_id = current_movie_data['epg_id']
                xml_id = self.EPG_MAP.get(m3u_id)
                
                if xml_id and xml_id in self.epg_data:
                    now_str = datetime.now().strftime('%Y%m%d%H%M%S')
                    past_and_current = [s for s in self.epg_data[xml_id] if s['start'] <= now_str]
                    if past_and_current:
                        current_s = max(past_and_current, key=lambda x: x['start'])
                        st = current_s['start']
                        current_program = f"{st[8:10]}:{st[10:12]} Uhr: {current_s['title']}"
                        epg_plot = current_s.get('desc', "Keine Beschreibung verfügbar.")

        # 3. Popup Initialisierung
        self.detail_popup = ModalView(size_hint=(1, 1), background_color=(0, 0, 0, 0.9))
        self.popup_focus_idx = 0 
        
        content = BoxLayout(orientation='vertical', padding=dp(20), spacing=dp(15))
        
        # --- HEADER (Titel) ---
        title_label = Label(text=f"[b]{movie.title}[/b]", markup=True, font_size=dp(22), 
                            halign='center', size_hint_y=None, height=dp(50))
        title_label.bind(size=title_label.setter('text_size'))
        content.add_widget(title_label)

        # --- EPG INFO BOX (Nur bei Live-TV) ---
        if "[TV]" in movie.title:
            epg_box = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(70))
            epg_box.add_widget(Label(text="AKTUELL IM TV:", color=(1, 0.8, 0, 1), bold=True, font_size=dp(12)))
            epg_info = Label(text=current_program, italic=True, font_size=dp(14), halign='center')
            epg_info.bind(size=epg_info.setter('text_size'))
            epg_box.add_widget(epg_info)
            content.add_widget(epg_box)

        # --- SCROLLBAR FÜR TEXT ---
        scroll = ScrollView(do_scroll_x=False)
        
        # Formatierung der Info-Texte
        display_genres = f"\n[color=aaaaaa]Genre: {genres_text}[/color]" if genres_text else ""
        rating_color = "ffff00" if "[TV]" not in movie.title else "00ff00"
        rating_label = "Bewertung" if "[TV]" not in movie.title else "Status"
        
        full_text = (f"[color={rating_color}]{rating_label}: {movie.rating}[/color]"
                     f"{display_genres}\n\n"
                     f"{epg_plot}")
        
        plot_label = Label(text=full_text, markup=True, size_hint_y=None, 
                           halign='left', valign='top', font_size=dp(16))
        plot_label.bind(size=lambda l, s: setattr(l, 'text_size', (s[0], None)))
        plot_label.bind(texture_size=lambda l, s: setattr(l, 'height', s[1]))
        
        scroll.add_widget(plot_label)
        content.add_widget(scroll)

        # --- BUTTON ROW ---
        btn_box = BoxLayout(size_hint_y=None, height=dp(70), spacing=dp(15), padding=[0, dp(10)])
        
        play_btn = Button(text='ABSPIELEN', bold=True, background_color=(0,0,0,0), background_normal='')
        fav_btn = Button(text='VORMERKEN' if not is_fav else 'ENTFERNEN', background_color=(0,0,0,0), background_normal='')
        close_btn = Button(text='ZURÜCK', background_color=(0,0,0,0), background_normal='')

        def apply_round_style(btn, color, radius=dp(25)):
            with btn.canvas.before:
                btn.bg_color_instr = Color(*color)
                btn.bg_rect = RoundedRectangle(pos=btn.pos, size=btn.size, radius=[radius])

            btn.bind(pos=lambda inst, val: setattr(inst.bg_rect, 'pos', val),
                     size=lambda inst, val: setattr(inst.bg_rect, 'size', val))

        apply_round_style(play_btn, (0.4, 0, 0, 1))
        apply_round_style(fav_btn, (0.4, 0.3, 0, 1))
        apply_round_style(close_btn, (0.15, 0.15, 0.15, 1))
        
        btn_box.add_widget(play_btn)
        btn_box.add_widget(fav_btn)
        btn_box.add_widget(close_btn)
        content.add_widget(btn_box)

        # --- LOGIK & EVENT HANDLER ---
        def play_stream(*args):
            url = movie.video_url
            if platform == 'android':
                try:
                    PythonActivity = autoclass('org.kivy.android.PythonActivity')
                    Intent = autoclass('android.content.Intent')
                    Uri = autoclass('android.net.Uri')
                    intent = Intent(Intent.ACTION_VIEW)
                    intent.setDataAndType(Uri.parse(url), "video/*")
                    cast('android.app.Activity', PythonActivity.mActivity).startActivity(intent)
                except: pass
            else:
                import webbrowser
                webbrowser.open(url)
            self.detail_popup.dismiss()

        def toggle_fav_local(inst):
            self.toggle_favorite(movie.title)
            inst.text = 'VORMERKEN' if movie.title not in self.favorites else 'ENTFERNEN'
            update_selection()

        def update_selection():
            play_btn.bg_color_instr.rgba = (0.6, 1, 0.3, 1) if self.popup_focus_idx == 0 else (0.6, 1, 0.3, 0.8)
            fav_btn.bg_color_instr.rgba = (1, 0.9, 0.35, 1) if self.popup_focus_idx == 1 else (1, 0.9, 0.35, 0.8)
            close_btn.bg_color_instr.rgba = (0.3, 0.3, 0.3, 1) if self.popup_focus_idx == 2 else (0.3, 0.3, 0.3, 0.8)

        play_btn.bind(on_release=play_stream)
        fav_btn.bind(on_release=toggle_fav_local)
        close_btn.bind(on_release=self.detail_popup.dismiss)

        # Keyboard Handler
        def popup_key_handler(window, key, *args):
            if key == 275: # Rechts
                self.popup_focus_idx = min(2, self.popup_focus_idx + 1)
                update_selection()
                return True
            elif key == 276: # Links
                self.popup_focus_idx = max(0, self.popup_focus_idx - 1)
                update_selection()
                return True
            elif key in (13, 32): # Enter / Bestätigen
                if self.popup_focus_idx == 0: play_stream()
                elif self.popup_focus_idx == 1: toggle_fav_local(fav_btn)
                else: self.detail_popup.dismiss()
                return True
            elif key == 27: # Zurück / ESC
                self.detail_popup.dismiss()
                return True            
            return True

        def popup_joy_handler(window, stick_id, button_id):
            key_map = {13: 276, 14: 275, 0: 13}
            sim_key = key_map.get(button_id)
            if sim_key: 
                return popup_key_handler(window, sim_key, None, None, None)
            return True            

        self.detail_popup.bind(on_open=lambda x: Window.bind(on_key_down=popup_key_handler))
        self.detail_popup.bind(on_dismiss=lambda x: Window.unbind(on_key_down=popup_key_handler))

        self.detail_popup.bind(on_open=lambda x: Window.bind(on_joy_button_down=popup_joy_handler))        
        self.detail_popup.bind(on_dismiss=lambda x: Window.unbind(on_joy_button_down=popup_joy_handler))
        
        self.detail_popup.add_widget(content)
        self.detail_popup.open()
        update_selection()

    def close_media_player(self):
        if hasattr(self, 'popup'):
            self.popup.dismiss()
            
    def stop_all_tasks(self):
        self.scanner.active = False
        if hasattr(self, 'executor'):
            try:
                self.executor.shutdown(wait=False, cancel_futures=True)
            except:
                pass
        Animation.stop_all(self)
        
    # --- APP CONTROL ---
    def close_app(self, *args):
        self.scanner.active = False
        self.executor.shutdown(wait=False)
        App.get_running_app().stop()

    def load_local_films(self, *args, update_from_web=True):
        try:
            with get_db_connection() as conn:

                query = """
                    SELECT f.title, f.video_url, f.search_name, f.year, f.genres_json, c.local_poster 
                    FROM film_list f
                    LEFT JOIN movie_cache c ON f.search_name = c.search_title
                    ORDER BY f.title ASC
                """
                rows = conn.execute(query).fetchall()
            
            if rows:
                new_movies = []
                for r in rows:

                    img_path = r[5] if r[5] and os.path.exists(r[5]) else ''
                    
                    new_movies.append({
                        'title': r[0], 'video_url': r[1], 'search': r[2], 
                        'year': r[3], 'genres_list': json.loads(r[4]), 
                        'thumb': img_path,
                        'plot': 'Details verfügbar', 'rating': 'N/A'
                    })
                
                self.all_movies = new_movies
                Clock.schedule_once(lambda dt: self.filter_results())
                
                missing = [m for m in self.all_movies if not m['thumb']]
                if missing:
                    Clock.schedule_once(lambda dt: self.load_extra_info(missing[:500]), 0.5)

            if update_from_web:
                
                self.fetch_data()

        except Exception as e:
            print(f"Fehler beim DB-Laden: {e}")

# ==========================================
# 6. APP START
# ==========================================

class MediaPlayer(App):
    def build(self):
        return MediaWidget()

if __name__ == '__main__':
    MediaPlayer().run()