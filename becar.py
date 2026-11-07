#!/usr/bin/env python3
"""Becar - player audio bit-perfect (ALSA).

Un singur fișier, organizat pe module (secțiuni):

   1. CONSTANTE
   2. TRADUCERI            - TRANSLATIONS, I18n
   3. UTILITARE            - run_cmd, pcm_fmt, fmt_time, num, scan_directory
   4. INSTANȚĂ UNICĂ       - SingleInstance
   5. AUDIO                - AudioInfo, AudioProbe, AlsaDevices
   6. PERSISTENȚĂ          - ConfigStore
   7. EXPORT               - Exporter (WAV / MP3 / AAC prin FFmpeg)
   8. MODEL                - Playlist (date + logică next/prev/shuffle/sortare)
   9. REDARE               - PlaybackEngine (aplay / flac / ffmpeg, RAM, pauză)
  10. INTERFAȚĂ            - BitPerfectPlayer (Tk)
  11. PORNIRE              - main()

Regulă: modulele 3-9 nu depind de Tk; doar BitPerfectPlayer atinge interfața.
"""
import concurrent.futures
import fcntl
import json
import os
import random
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from contextlib import suppress
from tkinter import filedialog, messagebox, ttk
from typing import NamedTuple

try:
  from tkinterdnd2 import DND_FILES, TkinterDnD

  BaseClass, HAS_DND = TkinterDnD.Tk, True
except ImportError:
  BaseClass, HAS_DND = tk.Tk, False


# =============================================================================
# 1. CONSTANTE
# =============================================================================
SUPPORTED_EXTENSIONS = (
    ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".wav", ".alac", ".opus",
    ".wma", ".ac3", ".dts", ".ape", ".wv", ".m4b", ".oga",
)
CONFIG_FILE = os.path.expanduser("~/.becar_config.json")
LOCK_FILE = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(),
    f"becar-{os.getuid()}.lock",
)
APP_DIR = os.path.dirname(os.path.abspath(__file__))
# Iconița se caută în ordine: icon.png lângă script, apoi cea instalată în sistem.
ICON_FILES = (
    os.path.join(APP_DIR, "icon.png"),
    "/usr/share/icons/hicolor/256x256/apps/becar.png",
)
META_KEYS = ("path", "artist", "title", "album")
ROW_H = 20  # înălțimea unui rând din playlist (px), folosită la centrare

# extensie, etichetă dialog, argumente ffmpeg (None = PCM calculat din fișier)
EXPORTS = {
    "wav": (".wav", "Audio WAV", None),
    "mp3": (".mp3", "MP3 Audio", ["-codec:a", "libmp3lame", "-b:a", "320k"]),
    "aac": (".m4a", "AAC Audio",
            ["-codec:a", "aac", "-b:a", "256k", "-f", "ipod"]),
}


# =============================================================================
# 2. TRADUCERI
# =============================================================================
COMMON = {
    "alsa_hw": "ALSA HW:",
    "col_album": "Album",
    "btn_stop": "⏹ Stop",
    "shuffle_on": "🔀 Shuffle: ON",
    "shuffle_off": "🔀 Shuffle: OFF",
    "repeat_off": "🔁 Repeat: OFF",
    "repeat_all": "🔁 Repeat: All",
    "repeat_one": "🔁 Repeat: One",
}

TRANSLATIONS = {
    "Română": {
        "err_title": "Eroare",
        "title": "Becar",
        "access": "ACCES:",
        "no_track": "--- Nicio melodie în redare ---",
        "col_artist": "Artist",
        "col_title": "Melodie",
        "search": "🔍 Caută:",
        "btn_prev": "⏮ Înapoi",
        "btn_play": "▶ Redare",
        "btn_pause": "⏸ Pauză/Reluare",
        "btn_next": "⏭ Înainte",
        "btn_files": "➕ Fișiere",
        "btn_folder": "📁 Dosar",
        "btn_remove": "🗑 Șterge",
        "btn_clear": "🧹 Golește",
        "ctx_open": "📂 Deschide locație fișier",
        "ctx_scroll": "🎯 Scroll la melodia în redare",
        "ctx_wav": "🎵 Extrage în WAV (Bit-Perfect)",
        "ctx_mp3": "🎧 Convertește în MP3 (320 kbps)",
        "ctx_aac": "🎼 Convertește în AAC (256 kbps)",
        "ctx_del": "🗑️ Șterge fișier(e) sursă",
        "ctx_rem": "❌ Șterge din listă",
        "loading": "Se încarcă...",
        "unknown": "Necunoscut",
        "err_play": "Eroare la redare",
        "err_play_msg": "Nu am putut porni redarea:\n",
        "confirm_del": "Confirmă ștergerea",
        "del_msg_multi": "Ștergi definitiv {count} fișiere sursă de pe disc?",
        "del_msg_single": "Ștergi definitiv fișierul sursă de pe disc?\n\n{path}",
        "finished": "Finalizat",
        "wav_finished": (
            "Extragerea WAV s-a finalizat!\nAu fost procesate cu succes"
            " {success} din {total} fișiere."
        ),
        "conv_finished": (
            "Conversia {fmt} s-a finalizat!\nAu fost procesate cu succes"
            " {success} din {total} fișiere."
        ),
        "save_wav": "Salvează ca WAV (Bit-Perfect)",
        "sel_wav_dir": "Selectează dosarul pentru salvarea fișierelor WAV",
        "sel_conv_dir": "Selectează dosarul pentru salvarea fișierelor {fmt}",
        "supported_audio": "Fișiere Audio Suportate",
        "all_files": "Toate fișierele",
        "sel_folder": "Selectează dosarul cu muzică",
    },
    "English": {
        "err_title": "Error",
        "title": "Becar",
        "access": "ACCESS:",
        "no_track": "--- No track playing ---",
        "col_artist": "Artist",
        "col_title": "Title",
        "search": "🔍 Search:",
        "btn_prev": "⏮ Prev",
        "btn_play": "▶ Play",
        "btn_pause": "⏸ Pause/Resume",
        "btn_next": "⏭ Next",
        "btn_files": "➕ Files",
        "btn_folder": "📁 Folder",
        "btn_remove": "🗑 Remove",
        "btn_clear": "🧹 Clear",
        "ctx_open": "📂 Open file location",
        "ctx_scroll": "🎯 Scroll to playing track",
        "ctx_wav": "🎵 Extract to WAV (Bit-Perfect)",
        "ctx_mp3": "🎧 Convert to MP3 (320 kbps)",
        "ctx_aac": "🎼 Convert to AAC (256 kbps)",
        "ctx_del": "🗑️ Delete source file(s)",
        "ctx_rem": "❌ Remove from list",
        "loading": "Loading...",
        "unknown": "Unknown",
        "err_play": "Playback Error",
        "err_play_msg": "Could not start playback:\n",
        "confirm_del": "Confirm Deletion",
        "del_msg_multi": "Permanently delete {count} source files from disk?",
        "del_msg_single": "Permanently delete source file from disk?\n\n{path}",
        "finished": "Completed",
        "wav_finished": (
            "WAV extraction completed!\nSuccessfully processed {success} of"
            " {total} files."
        ),
        "conv_finished": (
            "{fmt} conversion completed!\nSuccessfully processed {success} of"
            " {total} files."
        ),
        "save_wav": "Save as WAV (Bit-Perfect)",
        "sel_wav_dir": "Select folder to save WAV files",
        "sel_conv_dir": "Select folder to save {fmt} files",
        "supported_audio": "Supported Audio Files",
        "all_files": "All Files",
        "sel_folder": "Select music folder",
    },
    "Italiano": {
        "err_title": "Errore",
        "title": "Becar",
        "access": "ACCESSO:",
        "no_track": "--- Nessun brano in riproduzione ---",
        "col_artist": "Artista",
        "col_title": "Titolo",
        "search": "🔍 Cerca:",
        "btn_prev": "⏮ Prec",
        "btn_play": "▶ Riproduci",
        "btn_pause": "⏸ Pausa/Riprendi",
        "btn_next": "⏭ Successivo",
        "btn_files": "➕ File",
        "btn_folder": "📁 Cartella",
        "btn_remove": "🗑 Rimuovi",
        "btn_clear": "🧹 Pulisci",
        "ctx_open": "📂 Apri posizione file",
        "ctx_scroll": "🎯 Vai al brano in riproduzione",
        "ctx_wav": "🎵 Estrai in WAV (Bit-Perfect)",
        "ctx_mp3": "🎧 Converti in MP3 (320 kbps)",
        "ctx_aac": "🎼 Converti in AAC (256 kbps)",
        "ctx_del": "🗑️ Elimina file sorgente",
        "ctx_rem": "❌ Rimuovi dalla lista",
        "loading": "Caricamento...",
        "unknown": "Sconosciuto",
        "err_play": "Errore di riproduzione",
        "err_play_msg": "Impossibile avviare la riproduzione:\n",
        "confirm_del": "Conferma eliminazione",
        "del_msg_multi": "Eliminare definitivamente {count} file sorgente dal disco?",
        "del_msg_single": (
            "Eliminare definitivamente il file sorgente dal disco?\n\n{path}"
        ),
        "finished": "Completato",
        "wav_finished": (
            "Estrazione WAV completata!\nElaborati con successo {success} su"
            " {total} file."
        ),
        "conv_finished": (
            "Conversione {fmt} completata!\nElaborati con successo {success} su"
            " {total} file."
        ),
        "save_wav": "Salva come WAV (Bit-Perfect)",
        "sel_wav_dir": "Seleziona la cartella per salvare i file WAV",
        "sel_conv_dir": "Seleziona la cartella per salvare i file {fmt}",
        "supported_audio": "File Audio Supportati",
        "all_files": "Tutti i file",
        "sel_folder": "Seleziona cartella musicale",
    },
}
TRANSLATIONS = {k: {**COMMON, **v} for k, v in TRANSLATIONS.items()}


class I18n:
  """Traducător: `tr("cheie")` întoarce textul în limba curentă."""

  def __init__(self, lang="Română"):
    self.lang = lang

  @property
  def languages(self):
    return list(TRANSLATIONS)

  def __call__(self, key):
    return TRANSLATIONS[self.lang].get(key, key)


# =============================================================================
# 3. UTILITARE
# =============================================================================
def run_cmd(cmd, timeout):
  return subprocess.run(
      cmd, capture_output=True, text=True, check=True, timeout=timeout
  ).stdout


def pcm_fmt(bits):
  """(format aplay, format ffmpeg) pentru adâncimea de biți."""
  if bits >= 32:
    return "S32_LE", "s32le"
  return ("S24_3LE", "s24le") if bits >= 24 else ("S16_LE", "s16le")


def fmt_time(seconds):
  m, s = divmod(int(seconds), 60)
  return f"{m:02d}:{s:02d}"


def num(v):
  return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


def scan_directory(folder_path):
  """Toate fișierele audio suportate dintr-un dosar (recursiv, fără symlink-uri)."""
  return [
      path
      for root, _, files in os.walk(os.path.realpath(folder_path))
      for name in files
      if name.lower().endswith(SUPPORTED_EXTENSIONS)
      and not os.path.islink(path := os.path.join(root, name))
  ]


# =============================================================================
# 4. INSTANȚĂ UNICĂ
# =============================================================================
class SingleInstance:
  """Permite o singură instanță a aplicației (per utilizator).

  Folosește un lock exclusiv (flock) pe un fișier. Sistemul îl eliberează
  automat când procesul se termină, în orice fel (închidere, crash, kill),
  deci nu pot rămâne lock-uri „blocate". Dacă lock-ul nu se poate crea
  (ex. permisiuni), pornirea NU este blocată.
  """

  def __init__(self, path):
    self.path = path
    self._fd = None  # păstrat deschis cât rulează aplicația

  def acquire(self):
    """True = suntem singura instanță; False = mai rulează deja una."""
    try:
      fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
      return True
    try:
      fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
      os.close(fd)
      return False
    except OSError:
      os.close(fd)
      return True
    self._fd = fd
    return True


# =============================================================================
# 5. AUDIO: metadate, informații tehnice, dispozitive ALSA
# =============================================================================
class AudioInfo(NamedTuple):
  sample_rate: int
  bits: int
  duration: float
  channels: int
  bitrate: int


DEFAULT_INFO = AudioInfo(44100, 16, 0, 2, 0)


class AudioProbe:
  """Citește etichete (artist/titlu/album) și parametri tehnici din fișiere."""

  @staticmethod
  def read_metadata(filepath, unknown):
    artist, title, album = unknown, os.path.basename(filepath), unknown
    try:
      if filepath.lower().endswith(".flac"):
        out = run_cmd(["metaflac", "--show-tag=ARTIST", "--show-tag=ALBUM_ARTIST",
                       "--show-tag=TITLE", "--show-tag=ALBUM", filepath], 3)
        tags = {}
        for line in out.splitlines():
          key, sep, value = line.partition("=")
          if sep:
            tags.setdefault(key.upper(), value.strip())
      else:
        out = run_cmd(["ffprobe", "-v", "error", "-print_format", "json",
                       "-show_format", filepath], 5)
        raw = json.loads(out).get("format", {}).get("tags", {})
        tags = {k.upper(): str(v) for k, v in raw.items()}
      artist = tags.get("ARTIST", tags.get("ALBUM_ARTIST", artist))
      title, album = tags.get("TITLE", title), tags.get("ALBUM", album)
    except Exception:
      pass
    return {"path": filepath, "artist": artist, "title": title, "album": album}

  @staticmethod
  def get_info(filepath):
    """AudioInfo(sample_rate, bits, durată, canale, bitrate)."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".flac":
      with suppress(Exception):
        out = run_cmd(["metaflac", "--show-sample-rate", "--show-bps",
                       "--show-total-samples", "--show-channels", filepath], 3)
        nums = [int(v) for v in out.split() if v.isdigit()]
        if len(nums) >= 3:
          rate, bits, total = nums[:3]
          ch = max(1, nums[3] if len(nums) >= 4 else 2)
          dur = total / rate if rate else 0
          if bits not in (8, 16, 20, 24, 32):
            bits = 24 if bits > 16 else 16
          br = int(os.path.getsize(filepath) * 8 / dur) if dur > 0 else rate * ch * bits
          return AudioInfo(rate, bits, dur, ch, br)
    try:
      data = json.loads(run_cmd(
          ["ffprobe", "-v", "error", "-select_streams", "a:0", "-print_format",
           "json", "-show_streams", "-show_format", filepath], 5))
      s, fmt = data["streams"][0], data.get("format", {})
      rate = int(s.get("sample_rate") or 44100)
      dur = float(s.get("duration") or fmt.get("duration") or 0)
      ch = max(1, int(s.get("channels") or 2))
      br = int(fmt.get("bit_rate") or s.get("bit_rate") or 0)
      bits = int(s.get("bits_per_raw_sample") or s.get("bits_per_sample") or 0)
      if ext == ".wav":
        sfmt = str(s.get("sample_fmt") or "").lower()
        bits = bits or next(
            (b for k, b in (("s24", 24), ("s32", 32), ("flt", 32)) if k in sfmt), 16)
        bits = 16 if bits <= 16 else 24 if bits <= 24 else 32
        br = br if br > 0 else rate * ch * bits
      return AudioInfo(rate, bits or 16, dur, ch, br)
    except Exception:
      return AudioInfo(44100, 16, 0, 2, 128000)


class AlsaDevices:
  """Detectarea plăcilor de sunet (playback) din /proc/asound."""

  FALLBACK = {
      "hw:0,0 (Placă Integrată / Implicit)": "hw:0,0",
      "hw:1,0 (iFi Zen DAC V2 / USB Audio)": "hw:1,0",
  }

  @staticmethod
  def _read(path):
    with suppress(Exception), open(path, encoding="utf-8") as f:
      return f.read()
    return ""

  @classmethod
  def detect(cls):
    """{etichetă afișată: 'hw:CC,DD'}"""
    names = {
        m[1]: m[3].split("-")[0].strip()
        for m in re.finditer(
            r"^\s*(\d+)\s*\[(\w+)\s*\]:\s*(.+)$",
            cls._read("/proc/asound/cards"), re.M)
    }
    devs = {}
    for line in cls._read("/proc/asound/pcm").splitlines():
      if m := re.match(r"(\d+)-(\d+):.*playback", line, re.I):
        hw = f"hw:{int(m[1]):02d},{int(m[2]):02d}"
        card = names.get(m[1], f"Card {m[1]}")
        pcm = line.split(":", 1)[1].split(":", 1)[0].strip()
        label = (f"{hw} ({card} - {pcm})"
                 if pcm and pcm.lower() not in card.lower() else f"{hw} ({card})")
        devs[label] = hw
    return devs or dict(cls.FALLBACK)


# =============================================================================
# 6. PERSISTENȚĂ
# =============================================================================
class ConfigStore:
  """Citire/scriere a fișierului JSON de configurare."""

  def __init__(self, path):
    self.path = path

  def load(self):
    """Dicționarul salvat, sau {} dacă fișierul lipsește / e invalid."""
    try:
      with open(self.path, encoding="utf-8") as f:
        data = json.load(f)
      return data if isinstance(data, dict) else {}
    except Exception:
      return {}

  def save(self, data):
    # `data` e construit înainte de open(), ca o eroare să nu golească fișierul.
    with suppress(Exception), open(self.path, "w", encoding="utf-8") as f:
      json.dump(data, f, ensure_ascii=False, indent=2)


# =============================================================================
# 7. EXPORT (WAV / MP3 / AAC)
# =============================================================================
class Exporter:
  """Extrage/convertește fișiere cu FFmpeg (fără interfață)."""

  @staticmethod
  def convert(targets, fmt):
    """targets = [(sursă, destinație)]; întoarce (reușite, [erori])."""
    _, _, extra = EXPORTS[fmt]
    is_wav = fmt == "wav"
    ok, errors = 0, []
    for src, dest in targets:
      args = extra or ["-c:a", "pcm_" + pcm_fmt(AudioProbe.get_info(src).bits)[1],
                       "-f", "wav"]
      cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error", "-i",
             src, "-map", "0:a:0", "-vn", "-sn", "-dn", *args, dest]
      name = os.path.basename(src)
      try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, text=True)
        ok += os.path.isfile(dest) and os.path.getsize(dest) > (44 if is_wav else 0)
      except FileNotFoundError:
        errors.append("FFmpeg nu este instalat sau nu se află în PATH. "
                      "Comanda 'ffmpeg' nu a fost găsită.")
        break
      except subprocess.CalledProcessError as err:
        detail = (err.stderr or "").strip()[-1200:]
        errors.append(f"{name}: {detail or 'FFmpeg a returnat o eroare.'}")
      except Exception as err:
        errors.append(f"{name}: {err}")
    return ok, errors


# =============================================================================
# 8. MODEL: playlist
# =============================================================================
class Playlist:
  """Lista de piese + piesa curentă + istoric + sortare (fără Tk)."""

  def __init__(self):
    self.tracks = []        # [{"path","artist","title","album"}]
    self.history = []       # indecși redați (pentru shuffle)
    self.current = None     # indexul piesei curente
    self.sort_column = None
    self.sort_reverse = False

  def __len__(self):
    return len(self.tracks)

  def add(self, paths, loading_label):
    """Adaugă piesele noi (fără duplicate); întoarce lista celor adăugate."""
    existing = {t["path"] for t in self.tracks}
    new = [
        {"path": fp, "artist": loading_label,
         "title": os.path.basename(fp), "album": "..."}
        for fp in dict.fromkeys(paths)
        if fp not in existing and fp.lower().endswith(SUPPORTED_EXTENSIONS)
    ]
    self.tracks += new
    return new

  def clear(self):
    self.tracks.clear()
    self.history.clear()
    self.current = None

  def drop(self, indices):
    """Elimină piesele cu indecșii dați, păstrând coerente curentul/istoricul."""
    for i in sorted(indices, reverse=True):
      if self.current == i:
        self.current = None
      elif self.current is not None and self.current > i:
        self.current -= 1
      del self.tracks[i]
      self.history = [x - (x > i) for x in self.history if x != i]

  def mark_played(self, idx):
    if idx not in self.history:
      self.history.append(idx)

  def sort_by(self, col):
    """Sortează după coloană (a doua apăsare inversează ordinea)."""
    if self.sort_column == col:
      self.sort_reverse = not self.sort_reverse
    else:
      self.sort_column, self.sort_reverse = col, False
    old = self.tracks[:]
    self.tracks.sort(
        key=lambda x: str(x.get(col, "")).lower(), reverse=self.sort_reverse)
    pos = {id(t): i for i, t in enumerate(self.tracks)}  # vechi -> nou
    if self.current is not None:
      self.current = pos[id(old[self.current])]
    self.history = [pos[id(old[i])] for i in self.history]

  def next_index(self, shuffle, repeat):
    """Indexul următoarei piese, sau None dacă redarea trebuie oprită."""
    n, cur = len(self.tracks), self.current
    if repeat == "One" and cur is not None:
      return cur
    if shuffle:
      unplayed = [i for i in range(n) if i not in self.history]
      if not unplayed:
        self.history.clear()
        unplayed = list(range(n))
      return random.choice(unplayed)
    if cur is None:
      return 0
    if cur + 1 < n:
      return cur + 1
    return 0 if repeat == "All" else None

  def prev_index(self, repeat):
    n, cur = len(self.tracks), self.current
    if cur is None or cur > 0:
      return 0 if cur is None else cur - 1
    return n - 1 if repeat == "All" else 0


# =============================================================================
# 9. REDARE: motorul audio (fără Tk)
# =============================================================================
class PlaybackEngine:
  """Pornește/oprește lanțul decodor -> aplay și ține evidența timpului.

  Piesa curentă e încărcată integral în RAM (memfd) și refolosită la seek.
  """

  def __init__(self):
    self.process = self.ffmpeg_proc = None
    self.ram_fd = self.ram_path = None
    self.ram_info = None
    self.info = DEFAULT_INFO
    self.is_paused = False
    self.start_time = self.elapsed_before_pause = 0

  # --- parametri piesă curentă
  @property
  def sample_rate(self):
    return self.info.sample_rate

  @property
  def bits(self):
    return self.info.bits

  @property
  def channels(self):
    return self.info.channels

  @property
  def duration(self):
    return self.info.duration

  @property
  def bitrate(self):
    return self.info.bitrate

  @property
  def alive(self):
    """True dacă aplay rulează."""
    return self.process is not None and self.process.poll() is None

  # --- timp
  def elapsed(self):
    """Secunde redate din piesa curentă."""
    if self.is_paused:
      return self.elapsed_before_pause
    return self.elapsed_before_pause + time.time() - self.start_time

  def reset_clock(self):
    self.start_time = self.elapsed_before_pause = 0
    self.info = self.info._replace(duration=0)

  # --- RAM
  def load(self, path):
    """Încarcă piesa în RAM (dacă nu e deja) și întoarce AudioInfo."""
    if self.ram_path != path:
      self.free_ram()
      fd = os.memfd_create("becar")
      try:
        with open(path, "rb") as src, open(fd, "wb", closefd=False) as dst:
          shutil.copyfileobj(src, dst, 1 << 20)
      except BaseException:
        os.close(fd)
        raise
      self.ram_fd, self.ram_path = fd, path
      self.ram_info = AudioProbe.get_info(path)  # cache: fără disc la seek
    self.info = self.ram_info
    return self.info

  def free_ram(self):
    if self.ram_fd is not None:
      os.close(self.ram_fd)
    self.ram_fd = self.ram_path = None

  # --- procese
  def stop_processes(self):
    for proc in (self.process, self.ffmpeg_proc):
      if proc is None:
        continue
      with suppress(Exception):
        if proc.poll() is None:
          proc.kill()
        proc.wait(timeout=0.5)
      with suppress(Exception):
        proc.stdout.close()
    self.process = self.ffmpeg_proc = None

  def stop(self):
    """Oprește tot: procese, RAM, ceas, pauză."""
    self.stop_processes()
    self.free_ram()
    self.reset_clock()
    self.is_paused = False

  def play(self, start_seconds, device, mmap):
    """Pornește redarea piesei încărcate cu `load()`, de la `start_seconds`."""
    ext = os.path.splitext(self.ram_path)[1].lstrip(".").upper()
    rate, bits, channels = self.sample_rate, self.bits, self.channels
    alsa_fmt, pcm = pcm_fmt(bits)
    aplay_cmd = ["aplay", "-D", device, "-f", alsa_fmt, "-r", str(rate),
                 "-c", str(channels), "-v",
                 "--mmap" if mmap else "--buffer-size=65536"]
    quiet = {"stderr": subprocess.DEVNULL}
    fd, skip = self.ram_fd, start_seconds > 0 and rate > 0
    src = f"/proc/self/fd/{fd}"  # decodorul citește din RAM, nu de pe disc

    if ext == "WAV":
      os.lseek(fd, int(44 + start_seconds * rate * channels * (bits // 8))
               if skip else 0, 0)
      self.process = subprocess.Popen(aplay_cmd, stdin=fd, **quiet)
    else:
      if ext == "FLAC":
        decoder = ["flac", "--decode", "--stdout", "--force-raw-format",
                   "--endian=little", "--sign=signed"]
        if skip:
          decoder.append(f"--skip={int(float(start_seconds) * rate)}")
        decoder.append(src)
      else:
        decoder = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error"]
        if start_seconds > 0:
          decoder += ["-ss", str(start_seconds)]
        decoder += ["-i", src, "-map", "0:a:0", "-vn", "-sn", "-dn",
                    "-acodec", f"pcm_{pcm}", "-f", pcm, "-"]
      self.ffmpeg_proc = subprocess.Popen(
          decoder, stdout=subprocess.PIPE, pass_fds=(fd,), **quiet)
      try:
        self.process = subprocess.Popen(
            aplay_cmd, stdin=self.ffmpeg_proc.stdout, **quiet)
      finally:
        self.ffmpeg_proc.stdout.close()

    self.elapsed_before_pause = float(start_seconds)
    self.start_time = time.time()
    self.is_paused = False

  def toggle_pause(self):
    """Pauză/reluare prin SIGSTOP/SIGCONT; ridică excepție dacă semnalul eșuează."""
    if self.is_paused:
      self.process.send_signal(signal.SIGCONT)
      self.start_time = time.time()
    else:
      self.process.send_signal(signal.SIGSTOP)
      self.elapsed_before_pause += time.time() - self.start_time
    self.is_paused = not self.is_paused


# =============================================================================
# 10. INTERFAȚĂ (Tk)
# =============================================================================
class BitPerfectPlayer:
  """Controller: construiește fereastra și leagă UI-ul de model și motor."""

  # ------------------------------------------------------------ inițializare
  def __init__(self, root):
    self.root = root
    self.tr = I18n("Română")
    self.playlist = Playlist()
    self.engine = PlaybackEngine()
    self.config = ConfigStore(CONFIG_FILE)

    self.current_file = None
    self.is_shuffle = self.is_seeking = False
    self.repeat_mode = "Off"
    self._refresh_id = None
    self._texts = {}  # cheie traducere -> widget
    self.settings_visible = True  # setările (limbă/ALSA/acces) sunt afișate?
    self.seekbar_shown = False  # seekbar vizibil (doar cu o piesă încărcată)
    self._seek_place = None  # None / "top" / "bottom" (poziția curentă)
    self.access_modes_map = {"Memory mapping": "mmap", "Read-Write": "rw_interleaved"}
    self.alsa_devices_dict = AlsaDevices.detect()

    self._setup_window()
    self._build_top_bar()
    self._build_now_playing()
    self._build_playlist_view()
    self._build_context_menu()
    self._build_search_bar()
    self._build_seekbar()
    self._build_controls()
    self._build_footer()

    self.setup_hotkeys()
    self.load_config()
    self.update_ui_texts()
    self.apply_settings_visibility(self.settings_visible)
    self.tick()

  # ------------------------------------------------------ construcție widgeturi
  def _setup_window(self):
    root = self.root
    root.geometry("980x640")
    root.minsize(980, 620)
    self._load_icon()
    root.protocol("WM_DELETE_WINDOW", self.on_close)

  def _load_icon(self):
    """Setează iconița ferestrei (bara de titlu, taskbar, dialoguri).

    `True` o aplică și ferestrelor create ulterior (mesaje, selectoare de fișiere).
    """
    for path in ICON_FILES:
      if not os.path.isfile(path):
        continue
      with suppress(Exception):
        # referința e păstrată în self ca imaginea să nu fie ștearsă de GC
        self.window_icon = tk.PhotoImage(file=path)
        self.root.iconphoto(True, self.window_icon)
        return

  def _frame(self, pady, **pack):
    f = tk.Frame(self.root)
    f.pack(padx=10, pady=pady, **({"fill": "x"} | pack))
    return f

  def _label(self, parent, key=None, **kw):
    w = tk.Label(parent, **kw)
    if key:
      self._texts[key] = w
    return w

  def _button(self, parent, key, cmd, side="left"):
    b = ttk.Button(parent, command=cmd)
    b.pack(side=side, padx=2)
    self._texts[key] = b
    return b

  def _build_top_bar(self):
    """Rând sus: buton setări, setări (ascunse la cerere) și tip fișier."""
    self.top_frame = f = self._frame(4)
    self.btn_settings = ttk.Button(
        f, text="⚙", width=3, command=self.toggle_settings)
    self.btn_settings.pack(side="left", padx=(0, 10))

    # Limba, HW și Access stau într-un frame separat, ca să poată fi ascunse.
    self.settings_frame = sf = tk.Frame(f)
    sf.pack(side="left")
    self.lang_combo = ttk.Combobox(
        sf, values=self.tr.languages, width=10, state="readonly")
    self.lang_combo.set(self.tr.lang)
    self.lang_combo.pack(side="left", padx=(0, 15))
    self.lang_combo.bind("<<ComboboxSelected>>", self.change_language)

    self._label(sf, "alsa_hw", font=("Sans", 9, "bold")).pack(
        side="left", padx=(0, 4))
    opts = list(self.alsa_devices_dict)
    self.alsa_dev_combo = ttk.Combobox(sf, values=opts, width=32, state="readonly")
    self.alsa_dev_combo.set(next(
        (o for o in opts
         if any(k in o for k in ("hw:1,0", "hw:01,00", "hw:1,00", "hw:01,0"))),
        opts[0],
    ))
    self.alsa_dev_combo.pack(side="left", padx=(0, 15))

    self._label(sf, "access", font=("Sans", 9, "bold")).pack(
        side="left", padx=(0, 4))
    self.access_mode_combo = ttk.Combobox(
        sf, values=list(self.access_modes_map), width=16, state="readonly")
    self.access_mode_combo.set("Memory mapping")
    self.access_mode_combo.pack(side="left", padx=(0, 15))

    self.lbl_file_type = self._label(
        f, font=("Sans", 16, "bold"), fg="#C5C5C5", anchor="e")
    self.lbl_file_type.pack(side="right", padx=(10, 15))

  def _build_now_playing(self):
    """Titlu melodie (mărimea 14, centrat)."""
    self.lbl_now_playing = self._label(
        self._frame(2), font=("Sans", 14, "bold"), fg="#777777", anchor="center")
    self.lbl_now_playing.pack(fill="x", expand=True)

  def _build_playlist_view(self):
    f = self._frame(3, fill="both", expand=True)
    style = ttk.Style()
    style.theme_use("default")
    style.configure("Custom.Treeview", background="#E3E3E3",
                    fieldbackground="#E3E3E3", foreground="#000000",
                    rowheight=ROW_H)
    style.configure("Custom.Treeview.Heading", background="#E3E3E3",
                    foreground="#000000")
    self.tree = ttk.Treeview(
        f, columns=META_KEYS[1:], show="headings", selectmode="extended",
        style="Custom.Treeview")
    for col, width in zip(META_KEYS[1:], (240, 380, 240)):
      self.tree.heading(col, command=lambda c=col: self.sort_by_column(c))
      self.tree.column(col, width=width, anchor="w")
    self.tree.tag_configure("playing", background="#999999", foreground="#ffffff")
    scrollbar = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
    self.tree.configure(yscrollcommand=scrollbar.set)
    self.tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    self.tree.bind("<Double-Button-1>", lambda e: self.play_selected())
    self.tree.bind("<Button-3>", self.show_context_menu)

  def _build_context_menu(self):
    self.context_menu = tk.Menu(self.root, tearoff=0)
    self._menu_keys = {}
    items = [
        ("ctx_open", self.open_file_location),
        ("ctx_scroll", self.scroll_to_playing), None,
        ("ctx_wav", lambda: self.export("wav")),
        ("ctx_mp3", lambda: self.export("mp3")),
        ("ctx_aac", lambda: self.export("aac")), None,
        ("ctx_del", self.delete_source_file),
        ("ctx_rem", self.remove_selected),
    ]
    for i, item in enumerate(items):
      if item:
        self._menu_keys[i] = item[0]
        self.context_menu.add_command(command=item[1])
      else:
        self.context_menu.add_separator()
    self.root.bind("<Button-1>", lambda e: self.context_menu.unpost(), add="+")

  def _build_search_bar(self):
    f = self._frame(3)
    self._label(f, "search", font=("Sans", 9, "bold")).pack(
        side="left", padx=(0, 5))
    self.search_var = tk.StringVar()
    self.search_var.trace_add("write", self.filter_playlist)
    self.search_entry = tk.Entry(
        f, textvariable=self.search_var, bg="#E3E3E3", fg="#000000",
        insertbackground="#000000")
    self.search_entry.pack(side="left", fill="x", expand=True)

    if HAS_DND:
      with suppress(Exception):
        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind("<<Drop>>", self.on_drop)

  def _build_seekbar(self):
    # Un singur frame, fără pack aici: update_seek_layout() îl pune fie jos
    # (setări vizibile), fie în rândul de sus (setări ascunse), fie îl ascunde.
    f = self.seek_frame = tk.Frame(self.root)
    time_kw = {"text": "00:00", "width": 6, "font": ("Courier New", 10),
               "fg": "black"}
    self.lbl_time_curr = self._label(f, **time_kw)
    self.lbl_time_curr.pack(side="left")
    self.seek_slider = ttk.Scale(
        f, from_=0, to=100, orient="horizontal", command=self.on_seek_move)
    self.seek_slider.pack(side="left", fill="x", expand=True, padx=5)
    # Click = salt direct la poziție; glisare = urmărește cursorul.
    # "break" oprește comportamentul implicit ttk (pași mici la click).
    self.seek_slider.bind("<ButtonPress-1>", self.on_seek_press)
    self.seek_slider.bind("<B1-Motion>", self.on_seek_drag)
    self.seek_slider.bind("<ButtonRelease-1>", self.on_seek_release)
    self.lbl_time_dur = self._label(f, **time_kw)
    self.lbl_time_dur.pack(side="right")

  def _build_controls(self):
    f = self.ctrl_frame = self._frame(3)
    f.grid_columnconfigure(0, weight=1, uniform="group_ctrl")
    f.grid_columnconfigure(1, weight=0)
    f.grid_columnconfigure(2, weight=1, uniform="group_ctrl")
    self.lbl_info_left = self._label(
        f, font=("Sans", 9, "bold"), fg="#777777", anchor="w")
    self.lbl_info_left.grid(row=0, column=0, sticky="w")
    pb = tk.Frame(f)
    pb.grid(row=0, column=1)
    for key, cmd in (
        ("btn_prev", self.play_prev_track), ("btn_play", self.play_selected),
        ("btn_pause", self.toggle_pause), ("btn_stop", self.stop),
        ("btn_next", self.play_next_track),
    ):
      self._button(pb, key, cmd)
    self.lbl_info_right = self._label(
        f, font=("Sans", 10, "bold"), fg="#888888", anchor="e")
    self.lbl_info_right.grid(row=0, column=2, sticky="e")

  def _build_footer(self):
    """Fișiere, dosar, shuffle, repeat."""
    f = self._frame((3, 8))
    for key, cmd in (
        ("btn_files", self.add_files), ("btn_folder", self.add_folder),
        ("btn_remove", self.remove_selected), ("btn_clear", self.clear_playlist),
    ):
      self._button(f, key, cmd)
    self._label(f, text="BECAR", font=("Sans", 16, "bold"), fg="#C5C5C5",
                anchor="center").pack(side="left", fill="x", expand=True, padx=10)
    self.btn_shuffle = ttk.Button(f, command=self.toggle_shuffle)
    self.btn_shuffle.pack(side="right", padx=2)
    self.btn_repeat = ttk.Button(f, command=self.toggle_repeat)
    self.btn_repeat.pack(side="right", padx=2)

  # ------------------------------------------------------------------- texte
  def change_language(self, event=None):
    self.tr.lang = self.lang_combo.get()
    self.update_ui_texts()

  def _update_headings(self):
    pl = self.playlist
    for col in META_KEYS[1:]:
      arrow = (" ▼" if pl.sort_reverse else " ▲") if pl.sort_column == col else ""
      self.tree.heading(col, text=self.tr("col_" + col) + arrow)

  def _update_toggle_texts(self):
    self.btn_shuffle.config(
        text=self.tr("shuffle_on" if self.is_shuffle else "shuffle_off"))
    self.btn_repeat.config(text=self.tr("repeat_" + self.repeat_mode.lower()))

  def update_ui_texts(self):
    self.root.title(self.tr("title"))
    for key, widget in self._texts.items():
      widget.config(text=self.tr(key))
    if not self.current_file:
      self.lbl_now_playing.config(text=self.tr("no_track"))
    for i, key in self._menu_keys.items():
      self.context_menu.entryconfig(i, label=self.tr(key))
    self._update_headings()
    self._update_toggle_texts()

  # --------------------------------------------------------- setări / seekbar
  def toggle_settings(self):
    self.apply_settings_visibility(not self.settings_visible)

  def apply_settings_visibility(self, visible):
    """Afișează/ascunde setările; seekbar-ul se mută în funcție de ele."""
    self.settings_visible = bool(visible)
    if self.settings_visible:
      self.settings_frame.pack(side="left", after=self.btn_settings)
    else:
      self.settings_frame.pack_forget()
    self.update_seek_layout()

  def update_seek_layout(self):
    """Seekbar: jos cu timeri (setări vizibile), sus fără timeri (setări
    ascunse) sau ascuns.

    Se refolosește același frame, deci nu se pierde nimic la mutare. Nu face
    nimic dacă poziția nu s-a schimbat, ca să nu pâlpâie la fiecare seek.
    """
    if not self.seekbar_shown:
      place = None
    else:
      place = "bottom" if self.settings_visible else "top"
    if place == self._seek_place:
      return
    self._seek_place = place
    self.seek_frame.pack_forget()
    if place == "bottom":
      # Jos: seekbar cu timeri (curent în stânga, durată în dreapta).
      self.lbl_time_curr.pack(side="left", before=self.seek_slider)
      self.lbl_time_dur.pack(side="right", before=self.seek_slider)
      self.seek_frame.pack(before=self.ctrl_frame, fill="x", padx=10, pady=3)
    elif place == "top":
      # Sus: doar slider-ul, fără timeri.
      self.lbl_time_curr.pack_forget()
      self.lbl_time_dur.pack_forget()
      self.seek_frame.pack(
          in_=self.top_frame, side="left", fill="x", expand=True, padx=(0, 5))

  def show_track_length(self, path):
    """Pregătește seekbar-ul (durata) pentru o piesă marcată, fără redare."""
    with suppress(Exception):
      self.engine.info = AudioProbe.get_info(path)
      if self.engine.duration > 0:
        self.seek_slider.config(to=self.engine.duration)
        self.lbl_time_dur.config(text=fmt_time(self.engine.duration))

  def reset_seekbar(self):
    self.seek_slider.config(to=100)
    self.show_position(0)
    self.lbl_time_dur.config(text="00:00")

  def show_position(self, seconds):
    self.lbl_time_curr.config(text=fmt_time(seconds))
    self.seek_slider.set(seconds)

  # -------------------------------------------------------------------- ALSA
  def get_selected_hw_device(self):
    sel = self.alsa_dev_combo.get()
    return self.alsa_devices_dict.get(sel, sel.split()[0] if sel else "hw:0,0")

  def get_selected_access_mode(self):
    return self.access_modes_map.get(self.access_mode_combo.get(), "mmap")

  # ----------------------------------------------------------------- hotkeys
  def setup_hotkeys(self):
    """Doar aceste scurtături: Ctrl+P, Ctrl+N, Ctrl+Space, Ctrl+R, Ctrl+S.
    Nu funcționează cât timp cursorul e într-un câmp de text (ex. căutare)."""
    shortcuts = {
        "p": self.play_prev_track,   # previous
        "n": self.play_next_track,   # next
        "space": self.toggle_pause,  # play / pause
        "r": self.toggle_repeat,     # repeat
        "s": self.stop,              # stop
    }
    for key, fn in shortcuts.items():
      variants = {key, key.upper()} if len(key) == 1 else {key}  # și cu Caps Lock
      for k in variants:
        self.root.bind_all(f"<Control-{k}>", lambda e, f=fn: self.run_hotkey(f))

  def is_typing(self):
    try:
      w = self.root.focus_get()
    except KeyError:  # focus pe un popup (ex. lista combobox)
      return False
    return isinstance(w, (tk.Entry, ttk.Entry, tk.Text))

  def run_hotkey(self, fn):
    if self.is_typing():
      return  # cursorul e în search: nu executăm comenzi
    fn()
    return "break"

  # ---------------------------------------------------- toggle-uri shuffle/repeat
  def toggle_shuffle(self):
    self.is_shuffle = not self.is_shuffle
    self._update_toggle_texts()

  def toggle_repeat(self):
    modes = ["Off", "All", "One"]
    self.repeat_mode = modes[(modes.index(self.repeat_mode) + 1) % 3]
    self._update_toggle_texts()

  # ---------------------------------------------------------- vizualizare listă
  def sort_by_column(self, col):
    self.playlist.sort_by(col)
    self._update_headings()
    self.filter_playlist()

  def filter_playlist(self, *args):
    query = self.search_var.get().casefold().strip()
    current = self.playlist.current
    self.tree.delete(*self.tree.get_children())
    for i, t in enumerate(self.playlist.tracks):
      if query and query not in " ".join(str(t[k]) for k in META_KEYS).casefold():
        continue
      self.tree.insert(
          "", tk.END, iid=str(i), values=[t[k] for k in META_KEYS[1:]],
          tags=("playing",) if i == current else ())

  def update_tree_highlights(self):
    for iid in self.tree.tag_has("playing"):
      self.tree.item(iid, tags=())
    if iid := self._playing_iid():
      self.tree.item(iid, tags=("playing",))

  def _refresh_soon(self):
    """Reîmprospătează lista o singură dată pentru un grup de actualizări."""
    if not self._refresh_id:
      self._refresh_id = self.root.after(150, self._refresh_now)

  def _refresh_now(self):
    self._refresh_id = None
    self._refresh_keep_view()

  def _refresh_keep_view(self):
    """Reconstruiește lista păstrând selecția și poziția de scroll."""
    sel, top = self.tree.selection(), self.tree.yview()[0]
    self.filter_playlist()
    if sel := [s for s in sel if self.tree.exists(s)]:
      self.tree.selection_set(sel)
    self.tree.yview_moveto(top)

  def _get_selected_tracks(self):
    # iid-ul fiecărui rând = indexul real în playlist.tracks
    return [(i, self.playlist.tracks[i]) for i in map(int, self.tree.selection())]

  def _playing_iid(self):
    """iid-ul piesei în redare dacă e vizibilă în listă (altfel None)."""
    iid = str(self.playlist.current)  # "None" nu există niciodată în tree
    return iid if self.tree.exists(iid) else None

  def center_item(self, iid):
    """Derulează playlistul astfel încât rândul să fie pe mijloc."""
    if not self.tree.exists(iid):
      return
    rows = max(1, self.tree.winfo_height() // ROW_H - 1)  # -1 = antetul
    top = max(0, self.tree.index(iid) - rows // 2)
    self.tree.yview_moveto((top + 0.5) / len(self.tree.get_children()))

  def scroll_to_playing(self):
    if iid := self._playing_iid():
      self.center_item(iid)

  # ------------------------------------------------------------ meniu contextual
  def show_context_menu(self, event):
    if not (item := self.tree.identify_row(event.y)):
      return
    if item not in self.tree.selection():
      self.tree.selection_set(item)
    # intrarea 1 = "Scroll la melodia în redare"
    self.context_menu.entryconfig(
        1, state="normal" if self._playing_iid() else "disabled")
    try:
      self.context_menu.tk_popup(event.x_root, event.y_root)
    finally:
      self.context_menu.grab_release()
    self._watch_menu()

  def _watch_menu(self):
    """Închide meniul când aplicația pierde focusul (click în afara ferestrei).

    Click-urile din afara programului nu ajung la Tk, deci nu există
    eveniment <Button-1>; verificăm periodic dacă focusul a părăsit aplicația.
    """
    if not self.context_menu.winfo_ismapped():
      return
    if not str(self.root.tk.call("focus", "-displayof", ".")):
      self.context_menu.unpost()
    else:
      self.root.after(100, self._watch_menu)

  def open_file_location(self):
    dirs = {os.path.dirname(os.path.abspath(t["path"]))
            for _, t in self._get_selected_tracks() if os.path.exists(t["path"])}
    for d in dirs:
      subprocess.Popen(["xdg-open", d])

  # -------------------------------------------------------- editare playlist
  def _add_files_paths(self, file_paths):
    new = self.playlist.add(file_paths, self.tr("loading"))
    if not new:
      return
    self._refresh_keep_view()
    unknown = self.tr("unknown")

    def work(track):
      track.update(AudioProbe.read_metadata(track["path"], unknown))
      self.root.after(0, self._refresh_soon)

    def load():
      with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        ex.map(work, new)

    threading.Thread(target=load, daemon=True).start()

  def on_drop(self, event):
    with suppress(Exception):
      files = []
      for p in self.root.tk.splitlist(event.data):
        if os.path.isfile(p):
          files.append(p)
        elif os.path.isdir(p):
          files += scan_directory(p)
      self._add_files_paths(files)

  def add_files(self):
    ext_filter = " ".join(f"*{e}" for e in SUPPORTED_EXTENSIONS)
    if files := filedialog.askopenfilenames(filetypes=[
        (self.tr("supported_audio"), ext_filter), (self.tr("all_files"), "*.*")]):
      self._add_files_paths(files)

  def add_folder(self):
    if folder := filedialog.askdirectory(title=self.tr("sel_folder")):
      self._add_files_paths(scan_directory(folder))

  def clear_playlist(self):
    self.playlist.clear()
    self.filter_playlist()

  def _drop_tracks(self, indices):
    """Elimină din playlist piesele cu indecșii reali dați."""
    if self.playlist.current in indices:
      self.stop()
    self.playlist.drop(indices)
    self.filter_playlist()

  def remove_selected(self):
    self._drop_tracks([i for i, _ in self._get_selected_tracks()])

  def delete_source_file(self):
    tracks = self._get_selected_tracks()
    if not tracks:
      return
    msg = (self.tr("del_msg_multi").format(count=len(tracks)) if len(tracks) > 1
           else self.tr("del_msg_single").format(path=tracks[0][1]["path"]))
    if not messagebox.askyesno(self.tr("confirm_del"), msg, icon="warning"):
      return
    removed = []
    for i, track in tracks:
      try:
        if self.playlist.current == i:
          self.stop()
        if os.path.exists(track["path"]):
          os.remove(track["path"])
        removed.append(i)
      except Exception as err:
        messagebox.showerror(
            self.tr("err_title"), f"Nu am putut șterge fișierul:\n{err}")
    self._drop_tracks(removed)

  # ------------------------------------------------------------------ export
  def export(self, fmt):
    """Extrage în WAV sau convertește în MP3/AAC (dialoguri + fir de lucru)."""
    tracks = self._get_selected_tracks()
    if not tracks:
      return
    ext, type_label, _ = EXPORTS[fmt]
    up, is_wav = fmt.upper(), fmt == "wav"
    first = tracks[0][1]["path"]
    if len(tracks) == 1:
      dest = filedialog.asksaveasfilename(
          initialdir=os.path.dirname(first),
          initialfile=os.path.splitext(os.path.basename(first))[0] + ext,
          filetypes=[(type_label, "*" + ext)],
          title=self.tr("save_wav") if is_wav else f"Salvează ca {up}",
      )
      if not dest:
        return
      targets = [(first, dest)]
    else:
      folder = filedialog.askdirectory(
          initialdir=os.path.dirname(first),
          title=(self.tr("sel_wav_dir") if is_wav
                 else self.tr("sel_conv_dir").format(fmt=up)),
      )
      if not folder:
        return
      targets = [
          (t["path"], os.path.join(
              folder, os.path.splitext(os.path.basename(t["path"]))[0] + ext))
          for _, t in tracks
      ]

    def run():
      ok, errors = Exporter.convert(targets, fmt)
      self.root.after(
          0, lambda: self._show_export_result(fmt, ok, len(targets), errors))

    threading.Thread(target=run, daemon=True).start()

  def _show_export_result(self, fmt, ok, total, errors):
    is_wav = fmt == "wav"
    msg = self.tr("wav_finished" if is_wav else "conv_finished").format(
        fmt=fmt.upper(), success=ok, total=total)
    if errors:
      msg += "\n\nErori:\n" + "\n\n".join(errors[:5])
      if len(errors) > 5:
        msg += f"\n\n... și încă {len(errors) - 5} erori."
    (messagebox.showwarning if errors else messagebox.showinfo)(
        self.tr("finished"), msg)

  # ------------------------------------------------------------------ redare
  def _stop_processes(self):
    self.engine.stop_processes()
    self.lbl_info_left.config(text="")

  def stop(self):
    self.engine.stop()
    self.reset_seekbar()
    self.playlist.current = None
    self.update_tree_highlights()
    self.lbl_now_playing.config(text=self.tr("no_track"))
    self.lbl_file_type.config(text="")
    self.seekbar_shown = False
    self.update_seek_layout()
    self.update_status_display()

  def update_status_display(self):
    eng = self.engine
    if not eng.alive or eng.is_paused:
      self.lbl_info_left.config(text="")
      self.lbl_info_right.config(text="")
      return
    ext = os.path.splitext(self.current_file or "")[1].lstrip(".").upper()
    mmap = self.get_selected_access_mode() == "mmap"
    chain = ["APLAY", "ALSA HW", "MMAP" if mmap else "RW"]
    if ext != "WAV":
      chain.insert(0, "FLAC" if ext == "FLAC" else "FFMPEG")
    self.lbl_info_left.config(text=" - ".join(chain))
    if not self.current_file:
      self.lbl_info_right.config(text="")
      return
    br = eng.bitrate
    bitrate = f"{num(br / 1e6)} Mbit/s" if br >= 1e6 else f"{num(br / 1e3)} kbit/s"
    self.lbl_info_right.config(text=(
        f"{num(eng.sample_rate / 1000)} KHz - "
        f"{pcm_fmt(eng.bits)[0]} - {bitrate}"))

  def start_pipeline(self, filepath, start_seconds=0):
    eng = self.engine
    self._stop_processes()
    self.current_file = filepath
    try:
      info = eng.load(filepath)
      ext = os.path.splitext(filepath)[1].lstrip(".").upper()
      self.lbl_file_type.config(text=ext or "AUDIO")
      self.seek_slider.config(to=info.duration)
      self.lbl_time_dur.config(text=fmt_time(info.duration))
      self.seekbar_shown = True
      self.update_seek_layout()

      eng.play(start_seconds, self.get_selected_hw_device(),
               self.get_selected_access_mode() == "mmap")
      self.show_position(start_seconds)
      self.update_status_display()
    except Exception as err:
      self._stop_processes()
      messagebox.showerror(self.tr("err_play"), f"{self.tr('err_play_msg')}{err}")

  def play_index(self, real_idx):
    pl = self.playlist
    if not 0 <= real_idx < len(pl):
      self.stop()
      return
    pl.current = real_idx
    pl.mark_played(real_idx)
    t = pl.tracks[real_idx]
    self.lbl_now_playing.config(
        text=f"{t['artist']} - {t['title']} [{t['album']}]")
    self.update_tree_highlights()
    self.start_pipeline(t["path"])

  def play_selected(self):
    if rows := self.tree.selection() or self.tree.get_children():
      self.play_index(int(rows[0]))

  def toggle_pause(self):
    if not self.engine.alive:
      return
    with suppress(Exception):
      self.engine.toggle_pause()
      self.update_status_display()

  def play_next_track(self):
    if not len(self.playlist):
      return
    nxt = self.playlist.next_index(self.is_shuffle, self.repeat_mode)
    if nxt is None:
      self.stop()
    else:
      self.play_index(nxt)

  def play_prev_track(self):
    if not len(self.playlist):
      return
    self.play_index(self.playlist.prev_index(self.repeat_mode))

  def seek_relative(self, seconds):
    eng = self.engine
    if eng.process and eng.duration > 0:
      self.start_pipeline(
          self.current_file,
          start_seconds=max(0, min(eng.duration, eng.elapsed() + seconds)))

  # -------------------------------------------------------------------- seek
  def _seek_value(self, x):
    """Poziția (secunde) corespunzătoare coordonatei x din seekbar."""
    dur = self.engine.duration
    x0 = float(self.seek_slider.coords(0)[0])
    x1 = float(self.seek_slider.coords(dur)[0])
    frac = (x - x0) / (x1 - x0) if x1 > x0 else 0
    return max(0, min(1, frac)) * dur

  def on_seek_press(self, event):
    if self.engine.duration > 0:
      self.is_seeking = True
      self.seek_slider.set(self._seek_value(event.x))
    return "break"

  def on_seek_drag(self, event):
    if self.is_seeking:
      self.seek_slider.set(self._seek_value(event.x))
    return "break"

  def on_seek_release(self, event):
    if self.is_seeking:
      self.is_seeking = False
      self.start_pipeline(self.current_file, start_seconds=self.seek_slider.get())
    return "break"

  def on_seek_move(self, value):
    if self.is_seeking:
      self.lbl_time_curr.config(text=fmt_time(float(value)))

  def tick(self):
    """Actualizează timpul/seekbar-ul (1 s) și trece la piesa următoare."""
    eng, delay = self.engine, 1000
    if eng.process and not eng.is_paused and not self.is_seeking:
      if eng.process.poll() is not None:
        self._stop_processes()
        with suppress(Exception):
          self.play_next_track()
      elif eng.duration > 0:
        curr = min(eng.elapsed(), eng.duration)
        self.show_position(curr)
        if eng.duration - curr < 2:
          delay = 200  # final de piesă: trecere rapidă la următoarea
    self.root.after(delay, self.tick)

  # ------------------------------------------------------------------ config
  def load_config(self):
    data = self.config.load()
    if not data:
      return
    try:
      pl = self.playlist
      self.settings_visible = bool(data.get("settings_visible", True))
      if data.get("language") in TRANSLATIONS:
        self.tr.lang = data["language"]
        self.lang_combo.set(self.tr.lang)
      if data.get("alsa_device") in self.alsa_devices_dict:
        self.alsa_dev_combo.set(data["alsa_device"])
      if data.get("access_mode") in self.access_modes_map:
        self.access_mode_combo.set(data["access_mode"])
      if geometry := data.get("window_geometry"):
        with suppress(Exception):
          self.root.geometry(str(geometry))
      # Playlistul se restaurează direct din config (fără ffprobe/metaflac).
      unknown = self.tr("unknown")
      pl.tracks = [
          {"path": p, "artist": t.get("artist", unknown),
           "title": t.get("title", os.path.basename(p)),
           "album": t.get("album", unknown)}
          for t in data.get("playlist", [])
          if isinstance(t, dict) and (p := t.get("path")) and os.path.exists(p)
      ]
      # Starea vizuală a listei: sortare, căutare, selecție, scroll, piesa curentă.
      col = data.get("sort_column")
      pl.sort_column = col if col in META_KEYS[1:] else None
      pl.sort_reverse = bool(data.get("sort_reverse"))
      self.search_var.set(str(data.get("search", "")))
      by_path = {t["path"]: i for i, t in enumerate(pl.tracks)}
      cur = by_path.get(data.get("current_track"))
      pl.current = cur
      if cur is not None:
        # Doar marcăm piesa (fără redare); butonul Play / Next pornesc de aici.
        t = pl.tracks[cur]
        self.current_file = t["path"]
        self.lbl_now_playing.config(
            text=f"{t['artist']} - {t['title']} [{t['album']}]")
        self.lbl_file_type.config(
            text=os.path.splitext(t["path"])[1].lstrip(".").upper())
        self.show_track_length(t["path"])
        self.seekbar_shown = bool(data.get("seekbar_visible", True))
      self.filter_playlist()
      self._restore_view(by_path, data.get("selected", []))
    except Exception:
      pass

  def _restore_view(self, by_path, selected):
    """Reface selecția și centrează lista pe piesa curentă (sau pe cea selectată)."""
    sel = [str(i) for p in selected
           if isinstance(p, str) and (i := by_path.get(p)) is not None
           and self.tree.exists(str(i))]
    playing = self._playing_iid()
    if not sel and playing:
      sel = [playing]
    if sel:
      self.tree.selection_set(sel)
      self.tree.focus(sel[0])
    # Prioritate: piesa hașurată/în redare; altfel prima piesă selectată.
    if anchor := playing or (sel[0] if sel else None):
      center = lambda: self.center_item(anchor)
      # Fereastra nu e încă desenată la pornire: centrăm și după ce se așază.
      self.root.after_idle(center)
      self.root.after(150, center)

  def save_config(self):
    pl = self.playlist
    self.config.save({
        "language": self.tr.lang,
        "alsa_device": self.alsa_dev_combo.get(),
        "access_mode": self.access_mode_combo.get(),
        "window_geometry": self.root.geometry(),
        "playlist": [{k: t[k] for k in META_KEYS} for t in pl.tracks],
        "current_track": (
            pl.tracks[pl.current]["path"] if pl.current is not None else None),
        "selected": [t["path"] for _, t in self._get_selected_tracks()],
        "search": self.search_var.get(),
        "sort_column": pl.sort_column,
        "sort_reverse": pl.sort_reverse,
        "settings_visible": self.settings_visible,
        "seekbar_visible": self.seekbar_shown,
    })

  def on_close(self, event=None):
    self.save_config()
    self.engine.stop_processes()
    self.engine.free_ram()
    with suppress(Exception):
      self.root.destroy()


# =============================================================================
# 11. PORNIRE
# =============================================================================
def main():
  # O singură instanță: dacă mai rulează una, ieșim imediat, fără niciun mesaj.
  # Verificarea se face înainte de crearea ferestrei Tk.
  instance = SingleInstance(LOCK_FILE)
  if not instance.acquire():
    return
  app = BaseClass(baseName="becar", className="Becar")
  BitPerfectPlayer(app)
  app.mainloop()


if __name__ == "__main__":
  main()
