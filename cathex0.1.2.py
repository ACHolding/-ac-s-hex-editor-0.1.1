#!/usr/bin/env python3.14
# files=off | cathex / ac's hex editor 0.1a — optimized single-file ROM hex editor (Atari->PS5)
# Python 3.14 ready • vibe-coded for AC Holdings retro dev • efficient render, fixed SNES header edgecase
import os
import re
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field
from tkinter import filedialog, messagebox, simpledialog, ttk


BYTES_PER_LINE = 16
VISIBLE_LINES = 48


# --- header helpers ---------------------------------------------------------
def _txt(data: bytes, start: int, length: int) -> str:
    return data[start : start + length].decode("ascii", errors="replace").strip("\x00 \t\r\n")


def _sz(data: bytes) -> str:
    return f"{len(data):,} bytes"


# --- per-console parsers (bytes -> human description) -----------------------
def p_atari2600(d: bytes) -> str:
    hit = [f"{b // 1024}K" for b in (2048, 4096, 8192, 16384, 32768) if len(d) == b]
    note = f" | likely {', '.join(hit)} cart" if hit else ""
    return f"{_sz(d)}{note} | no standard header"


def p_atari7800(d: bytes) -> str:
    if len(d) >= 0x40 and d[1:10] == b"ATARI7800":
        return f"{_sz(d)} | A78 header | Title: {_txt(d, 0x11, 32) or 'Unknown'}"
    return f"{_sz(d)} | raw 7800 cart (no A78 header)"


def p_lynx(d: bytes) -> str:
    if d[:4] == b"LYNX":
        return f"{_sz(d)} | Lynx (.lnx) | Title: {_txt(d, 6, 32) or 'Unknown'}"
    return f"{_sz(d)} | raw Lynx cart"


def p_jaguar(d: bytes) -> str:
    if len(d) >= 0x404 and d[0x400:0x405] == b"ATARI":
        return f"{_sz(d)} | Jaguar header at $400"
    return f"{_sz(d)} | Jaguar ROM (no standard header)"


def p_nes(d: bytes) -> str:
    if len(d) >= 16 and d[:4] == b"NES\x1a":
        if (d[7] & 0x0C) == 0x08:
            return f"{_sz(d)} | NES 2.0 header"
        prg, chr_ = d[4] * 16, d[5] * 8
        mapper = (d[6] >> 4) | (d[7] & 0xF0)
        mirror = "Vertical" if d[6] & 1 else "Horizontal"
        return f"iNES | PRG {prg}K | CHR {chr_}K | Mapper {mapper} | {mirror} mirroring"
    return f"{_sz(d)} | no iNES header at $00"


def p_fds(d: bytes) -> str:
    if d[:4] == b"FDS\x1a":
        return f"{_sz(d)} | fwNES-headered FDS | {d[4]} side(s)"
    if len(d) >= 15 and d[1:15] == b"*NINTENDO-HVC*":
        return f"{_sz(d)} | raw FDS disk image"
    return f"{_sz(d)} | FDS image (unrecognized header)"


def p_snes(d: bytes) -> str:
    body = d[512:] if (len(d) % 1024) == 512 else d
    if len(body) < 0x8000:
        return f"{_sz(d)} | too small for SNES header scan"
    best = None
    for base, mode in ((0x7FC0, "LoROM"), (0xFFC0, "HiROM")):
        if len(body) < base + 0x20:
            continue
        chk = int.from_bytes(body[base + 0x1C : base + 0x1E], "little")
        cmp = int.from_bytes(body[base + 0x1E : base + 0x20], "little")
        score = 2 if (chk ^ cmp) == 0xFFFF else 0
        title = _txt(body, base, 21)
        if title.isprintable():
            score += 1
        if best is None or score > best[0]:
            best = (score, base, mode, title)
    if best is None:
        return f"{_sz(d)} | no valid SNES header found in LoROM/HiROM scan"
    _, base, mode, title = best
    size_code = body[base + 0x17] if len(body) > base + 0x17 else 0
    size_kb = 32 << size_code if size_code < 16 else 0
    hdr = " (+512 copier header)" if body is not d else ""
    return f"{_sz(d)}{hdr} | {mode} | Title: {title or 'Unknown'} | declared ~{size_kb}K"


def p_gb(d: bytes) -> str:
    if len(d) < 0x150:
        return f"{_sz(d)} | too small for GB header"
    logo = d[0x104:0x108] == b"\xce\xed\x66\x66"
    cgb = d[0x143]
    sys = "GBC" if cgb in (0x80, 0xC0) else "GB"
    title = _txt(d, 0x134, 15)
    cart = d[0x147]
    logo_n = "logo OK" if logo else "BAD logo"
    return f"{_sz(d)} | {sys} | Title: {title or 'Unknown'} | cart ${cart:02X} | {logo_n}"


def p_gba(d: bytes) -> str:
    if len(d) >= 0xC0 and d[0xB2] == 0x96:
        return f"{_sz(d)} | GBA | Title: {_txt(d, 0xA0, 12) or 'Unknown'} | Code: {_txt(d, 0xAC, 4)}"
    return f"{_sz(d)} | GBA ROM (fixed byte $B2 != $96)"


def p_virtualboy(d: bytes) -> str:
    if len(d) >= 0x220:
        return f"{_sz(d)} | Virtual Boy | Title: {_txt(d, len(d) - 0x220, 20) or 'Unknown'}"
    return f"{_sz(d)} | Virtual Boy ROM"


def p_n64(d: bytes) -> str:
    fmt = {
        b"\x80\x37\x12\x40": "z64 (big-endian)",
        b"\x37\x80\x40\x12": "v64 (byteswapped)",
        b"\x40\x12\x37\x80": "n64 (little-endian)",
    }.get(bytes(d[:4]), "unknown byte order")
    swapped = fmt.startswith("z64")
    title = _txt(d, 0x20, 20) if swapped and len(d) >= 0x34 else ""
    code = _txt(d, 0x3B, 3) if len(d) >= 0x3E else ""
    extra = f" | Title: {title}" if title else ""
    extra += f" | ID: {code}" if code else ""
    return f"{_sz(d)} | {fmt}{extra}"


def p_gamecube(d: bytes) -> str:
    if len(d) >= 0x60 and d[0x1C:0x20] == b"\xc2\x33\x9f\x3d":
        return f"{_sz(d)} | GameCube | ID: {_txt(d, 0, 6)} | {_txt(d, 0x20, 0x40)}"
    return f"{_sz(d)} | GC disc/dump (no GCN magic)"


def p_wii(d: bytes) -> str:
    if d[:4] == b"WBFS":
        return f"{_sz(d)} | WBFS container"
    if len(d) >= 0x60 and d[0x18:0x1C] == b"\x5d\x1c\x9e\xa3":
        return f"{_sz(d)} | Wii | ID: {_txt(d, 0, 6)} | {_txt(d, 0x20, 0x40)}"
    return f"{_sz(d)} | Wii disc/dump (no magic)"


def p_nds(d: bytes) -> str:
    if len(d) >= 0x20:
        return f"{_sz(d)} | NDS | Title: {_txt(d, 0, 12) or 'Unknown'} | Code: {_txt(d, 0x0C, 4)}"
    return f"{_sz(d)} | NDS ROM"


def p_3ds(d: bytes) -> str:
    if len(d) >= 0x104 and d[0x100:0x104] == b"NCSD":
        return f"{_sz(d)} | 3DS NCSD (.3ds/.cci)"
    if len(d) >= 0x104 and d[0x100:0x104] == b"NCCH":
        return f"{_sz(d)} | 3DS NCCH"
    return f"{_sz(d)} | 3DS dump (no NCSD/NCCH magic)"


def p_sms_gg(d: bytes) -> str:
    for base in (0x1FF0, 0x3FF0, 0x7FF0):
        if len(d) >= base + 16 and d[base : base + 8] == b"TMR SEGA":
            region = d[base + 0x0F] >> 4
            tag = {3: "SMS export", 4: "SMS jp", 5: "GG jp", 6: "GG export", 7: "GG intl"}.get(region, "SMS/GG")
            return f"{_sz(d)} | TMR SEGA @ ${base:04X} | {tag}"
    return f"{_sz(d)} | SMS/GG (no TMR SEGA header)"


def p_megadrive(d: bytes) -> str:
    head = d[:0x20]
    if b"SEGADISCSYSTEM" in head or b"SEGABOOTDISC" in head:
        return f"{_sz(d)} | Sega/Mega-CD boot sector"
    if len(d) >= 0x104 and d[0x100:0x104] == b"SEGA":
        system = _txt(d, 0x100, 16)
        title = _txt(d, 0x120, 48) or _txt(d, 0x150, 48)
        tag = "Sega 32X" if "32X" in system.upper() else "Genesis/Mega Drive"
        return f"{_sz(d)} | {tag} | {system} | Title: {title or 'Unknown'}"
    return f"{_sz(d)} | no SEGA header at $100"


def p_saturn(d: bytes) -> str:
    if d[:15] == b"SEGA SEGASATURN" or d[:4] == b"SEGA":
        return f"{_sz(d)} | Saturn | Product: {_txt(d, 0x20, 10) or 'Unknown'} | {_txt(d, 0x60, 0x70)}"
    return f"{_sz(d)} | raw Saturn ROM / dump"


def p_dreamcast(d: bytes) -> str:
    if d[:16] == b"SEGA SEGAKATANA ":
        return f"{_sz(d)} | Dreamcast IP.BIN | Title: {_txt(d, 0x80, 0x80) or 'Unknown'}"
    if len(d) >= 0x178:
        title = _txt(d, 0x128, 0x50)
        if title:
            return f"{_sz(d)} | Title: {title}"
    return f"{_sz(d)} | Dreamcast image / GDI slice"


def p_pcengine(d: bytes) -> str:
    return f"{_sz(d)} | {'512-byte header present' if len(d) % 1024 == 512 else 'HuCard (headerless)'}"


def p_neogeopocket(d: bytes) -> str:
    head = d[:0x20]
    if b"COPYRIGHT BY SNK" in head or b"LICENSED BY SNK" in head:
        color = "C" if len(d) > 0x23 and d[0x23] == 0x10 else ""
        return f"{_sz(d)} | NGP{color} | Title: {_txt(d, 0x24, 12) or 'Unknown'}"
    return f"{_sz(d)} | Neo Geo Pocket (no SNK copyright string)"


def p_ps1(d: bytes) -> str:
    if len(d) >= 0x9340:
        system = _txt(d, 0x9320, 16)
        if system.startswith("PLAYSTATION"):
            return f"{_sz(d)} | {system} | Title: {_txt(d, 0x9330, 0x110) or 'Unknown'}"
    sectors = len(d) // 2048
    return f"{_sz(d)} | ~{sectors:,} CD sectors (2048B)" if sectors else f"{_sz(d)} | PS1 binary"


def p_ps2(d: bytes) -> str:
    if len(d) >= 0x8000:
        system = _txt(d, 0x4, 12)
        if "PLAYSTATION" in system:
            return f"{_sz(d)} | {system} | Title: {_txt(d, 0x28, 0x20) or 'Unknown'}"
    if d[:4] == b"\x7fELF":
        return f"{_sz(d)} | PS2 ELF executable"
    return f"{_sz(d)} | PS2 ISO / ELF slice"


def p_psp(d: bytes) -> str:
    if d[:4] == b"\x00PSF":
        return f"{_sz(d)} | PARAM.SFO"
    if d[:4] == b"CISO":
        return f"{_sz(d)} | CSO compressed UMD"
    if b"PSP GAME" in d[:0x10000]:
        return f"{_sz(d)} | UMD ISO (PSP GAME present)"
    return f"{_sz(d)} | PSP ISO/CSO"


def p_vita(d: bytes) -> str:
    if d[:4] == b"PK\x03\x04":
        return f"{_sz(d)} | VPK (zip container)"
    if d[:4] == b"\x00PSF":
        return f"{_sz(d)} | PARAM.SFO"
    return f"{_sz(d)} | Vita dump"


def p_ps3(d: bytes) -> str:
    if d[:4] == b"\x7fPKG":
        return f"{_sz(d)} | PS3 PKG"
    if d[:4] == b"SCE\x00":
        return f"{_sz(d)} | SELF (signed ELF)"
    if d[:4] == b"\x7fELF":
        return f"{_sz(d)} | ELF (PS3 ELF/SELF dump)"
    return f"{_sz(d)} | PS3 dump / filesystem image"


def p_ps4(d: bytes) -> str:
    if d[:4] == b"\x7fCNT":
        return f"{_sz(d)} | PS4 PKG (CNT)"
    if d[:7] == b"\x7fELF\x02\x01\x01":
        return f"{_sz(d)} | ELF64 — typical PS4 binary"
    return f"{_sz(d)} | PS4 PKG / dump slice"


def p_ps5(d: bytes) -> str:
    if d[:4] == b"\x7fCNT":
        return f"{_sz(d)} | PS5 PKG (CNT)"
    if d[:7] == b"\x7fELF\x02\x01\x01":
        return f"{_sz(d)} | ELF64 — typical PS5 binary"
    return f"{_sz(d)} | PS5 PKG / dump slice"


@dataclass
class ConsoleProfile:
    name: str
    extensions: tuple[str, ...]
    filetypes: tuple[tuple[str, str], ...]
    parser: Callable[[bytes], str] | None = None
    typical_sizes: tuple[int, ...] = ()
    notes: str = ""

    def describe(self, data: bytes) -> str:
        if not data:
            return "Empty file"
        if self.parser is not None:
            try:
                return self.parser(bytes(data))
            except Exception:
                return f"{len(data):,} bytes | header parse error"
        return f"{len(data):,} bytes"


def _ft(label: str, globs: str) -> tuple[tuple[str, object], ...]:
    # macOS Aqua Tk greys out files when patterns are a single space-joined
    # string and when "All files" is "*.*". Pass a tuple of globs and a bare "*".
    patterns = tuple(globs.split())
    return ((label, patterns), ("All files", "*"))


# Left -> right tab order: Atari era through PS5.
CONSOLE_PROFILES: dict[str, ConsoleProfile] = {
    "Atari 2600": ConsoleProfile("Atari 2600", (".a26", ".bin", ".rom"), _ft("Atari 2600", "*.a26 *.bin *.rom"), p_atari2600),
    "Atari 7800": ConsoleProfile("Atari 7800", (".a78", ".bin"), _ft("Atari 7800", "*.a78 *.bin"), p_atari7800),
    "Atari Lynx": ConsoleProfile("Atari Lynx", (".lnx", ".bin"), _ft("Atari Lynx", "*.lnx *.bin"), p_lynx),
    "Atari Jaguar": ConsoleProfile("Atari Jaguar", (".j64", ".jag", ".bin"), _ft("Atari Jaguar", "*.j64 *.jag *.bin"), p_jaguar),
    "NES": ConsoleProfile("NES", (".nes", ".bin"), _ft("NES ROM", "*.nes *.bin"), p_nes),
    "Famicom Disk": ConsoleProfile("Famicom Disk", (".fds",), _ft("FDS image", "*.fds"), p_fds),
    "SNES": ConsoleProfile("SNES", (".sfc", ".smc", ".fig", ".bin"), _ft("SNES ROM", "*.sfc *.smc *.fig *.bin"), p_snes),
    "Game Boy": ConsoleProfile("Game Boy", (".gb", ".bin"), _ft("Game Boy", "*.gb *.bin"), p_gb),
    "GB Color": ConsoleProfile("GB Color", (".gbc", ".gb", ".bin"), _ft("Game Boy Color", "*.gbc *.gb *.bin"), p_gb),
    "GBA": ConsoleProfile("GBA", (".gba", ".bin"), _ft("Game Boy Advance", "*.gba *.bin"), p_gba),
    "Virtual Boy": ConsoleProfile("Virtual Boy", (".vb", ".vboy", ".bin"), _ft("Virtual Boy", "*.vb *.vboy *.bin"), p_virtualboy),
    "N64": ConsoleProfile("N64", (".z64", ".n64", ".v64", ".bin"), _ft("N64 ROM", "*.z64 *.n64 *.v64 *.bin"), p_n64),
    "GameCube": ConsoleProfile("GameCube", (".iso", ".gcm", ".gcz"), _ft("GameCube image", "*.iso *.gcm *.gcz"), p_gamecube),
    "Wii": ConsoleProfile("Wii", (".iso", ".wbfs", ".wad"), _ft("Wii image", "*.iso *.wbfs *.wad"), p_wii),
    "DS": ConsoleProfile("DS", (".nds", ".bin"), _ft("Nintendo DS", "*.nds *.bin"), p_nds),
    "3DS": ConsoleProfile("3DS", (".3ds", ".cci", ".cia"), _ft("Nintendo 3DS", "*.3ds *.cci *.cia"), p_3ds),
    "SMS / GG": ConsoleProfile("SMS / GG", (".sms", ".gg", ".bin"), _ft("Master System / Game Gear", "*.sms *.gg *.bin"), p_sms_gg),
    "Genesis": ConsoleProfile("Genesis", (".md", ".gen", ".smd", ".bin"), _ft("Genesis / Mega Drive", "*.md *.gen *.smd *.bin"), p_megadrive),
    "Sega CD": ConsoleProfile("Sega CD", (".iso", ".bin", ".cue", ".chd"), _ft("Sega CD image", "*.iso *.bin *.cue *.chd"), p_megadrive),
    "32X": ConsoleProfile("32X", (".32x", ".bin", ".md"), _ft("Sega 32X", "*.32x *.bin *.md"), p_megadrive),
    "Saturn": ConsoleProfile("Saturn", (".bin", ".iso", ".cue", ".chd"), _ft("Saturn image", "*.bin *.iso *.cue *.chd"), p_saturn),
    "Dreamcast": ConsoleProfile("Dreamcast", (".gdi", ".cdi", ".bin", ".iso", ".chd"), _ft("Dreamcast image", "*.gdi *.cdi *.bin *.iso *.chd"), p_dreamcast),
    "PC Engine": ConsoleProfile("PC Engine", (".pce", ".bin"), _ft("PC Engine / TG16", "*.pce *.bin"), p_pcengine),
    "Neo Geo Pocket": ConsoleProfile("Neo Geo Pocket", (".ngp", ".ngc", ".bin"), _ft("Neo Geo Pocket", "*.ngp *.ngc *.bin"), p_neogeopocket),
    "PS1": ConsoleProfile("PS1", (".bin", ".img", ".iso", ".cue", ".chd"), _ft("PlayStation image", "*.bin *.img *.iso *.cue *.chd"), p_ps1),
    "PS2": ConsoleProfile("PS2", (".iso", ".bin", ".elf", ".chd"), _ft("PlayStation 2 image", "*.iso *.bin *.elf *.chd"), p_ps2),
    "PSP": ConsoleProfile("PSP", (".iso", ".cso", ".pbp"), _ft("PSP image", "*.iso *.cso *.pbp"), p_psp),
    "PS Vita": ConsoleProfile("PS Vita", (".vpk", ".bin"), _ft("PS Vita", "*.vpk *.bin"), p_vita),
    "PS3": ConsoleProfile("PS3", (".pkg", ".self", ".elf", ".bin"), _ft("PlayStation 3 dump", "*.pkg *.self *.elf *.bin"), p_ps3),
    "PS4": ConsoleProfile("PS4", (".pkg", ".bin", ".elf"), _ft("PlayStation 4 dump", "*.pkg *.bin *.elf"), p_ps4),
    "PS5": ConsoleProfile("PS5", (".pkg", ".bin", ".elf"), _ft("PlayStation 5 dump", "*.pkg *.bin *.elf"), p_ps5),
}


class HexTab:
    """One console tab with a real editable hex view."""

    def __init__(self, parent: tk.Frame, profile: ConsoleProfile, app: "ACSHexEditor"):
        self.parent = parent
        self.profile = profile
        self.app = app

        self.data = bytearray()
        self.path: str | None = None
        self.modified = False
        self.cursor = 0
        self.nibble = 0
        self.sel_anchor: int | None = None
        self.sel_end: int | None = None
        self.insert_mode = False
        self.show_offsets = True
        self.show_ascii = True
        self.first_line = 0
        self.bookmarks: list[int] = []
        self.bookmark_index = -1
        self.undo_stack: list[tuple[bytearray, int]] = []
        self.redo_stack: list[tuple[bytearray, int]] = []
        self._rendering = False

        self.info_label = tk.Label(
            parent,
            text=f"{profile.name} — open a ROM to begin",
            bg=app.BG_COLOR,
            fg="#88ccff",
            font=("Consolas", 10),
            anchor="w",
            padx=10,
            pady=4,
        )
        self.info_label.pack(fill=tk.X)

        frame = tk.Frame(parent, bg=app.BG_COLOR)
        frame.pack(fill=tk.BOTH, expand=True)

        self.text = tk.Text(
            frame,
            bg="#0a0a0a",
            fg=app.TEXT_COLOR,
            font=("Courier New", 11, "bold"),
            insertbackground=app.TEXT_COLOR,
            selectbackground=app.SELECT_BG,
            inactiveselectbackground=app.SELECT_BG,
            wrap=tk.NONE,
            padx=10,
            pady=10,
            borderwidth=0,
            highlightthickness=0,
            undo=False,
            maxundo=0,
            tabs="",
        )
        scroll = tk.Scrollbar(frame, command=self._on_scrollbar)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.text.tag_configure("offset", foreground="#666666")
        self.text.tag_configure("hex", foreground=app.TEXT_COLOR)
        self.text.tag_configure("ascii", foreground="#00ddff")
        self.text.tag_configure("cursor", background="#004488")
        self.text.tag_configure("sel", background=app.SELECT_BG)
        self.text.tag_configure("bookmark", background="#333300")

        self.text.bind("<Button-1>", self._on_click)
        self.text.bind("<B1-Motion>", self._on_drag)
        self.text.bind("<Key>", self._on_key)
        self.text.bind("<Control-c>", self._copy)
        self.text.bind("<Control-x>", self._cut)
        self.text.bind("<Control-v>", self._paste)
        self.text.bind("<Control-a>", self._select_all)
        self.text.bind("<Control-z>", self._undo)
        self.text.bind("<Control-y>", self._redo)
        self.text.bind("<Control-Shift-Z>", self._redo)
        self.text.bind("<MouseWheel>", self._on_mousewheel)
        self.text.bind("<Button-4>", self._on_mousewheel)
        self.text.bind("<Button-5>", self._on_mousewheel)

        self.render()

    @property
    def selection(self) -> tuple[int, int] | None:
        if self.sel_anchor is None or self.sel_end is None:
            return None
        start = min(self.sel_anchor, self.sel_end)
        end = max(self.sel_anchor, self.sel_end)
        if start == end:
            return None
        return start, end

    def is_active(self) -> bool:
        return self.app.active_tab() is self

    def push_undo(self) -> None:
        self.undo_stack.append((bytearray(self.data), self.cursor))
        self.redo_stack.clear()
        if len(self.undo_stack) > 100:
            self.undo_stack.pop(0)

    def mark_modified(self) -> None:
        self.modified = True
        self.app.update_title()

    def new_file(self) -> None:
        self.data = bytearray()
        self.path = None
        self.modified = False
        self.cursor = 0
        self.nibble = 0
        self.clear_selection()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.bookmarks.clear()
        self.bookmark_index = -1
        self.first_line = 0
        self._update_info()
        self.render()
        self.app.update_status()

    def load_file(self, path: str) -> None:
        with open(path, "rb") as fh:
            blob = fh.read()
        self.data = bytearray(blob)
        self.path = path
        self.modified = False
        self.cursor = 0
        self.nibble = 0
        self.clear_selection()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.bookmarks.clear()
        self.bookmark_index = -1
        self.first_line = 0
        self._update_info()
        self.render()
        self.app.update_title()
        self.app.update_status()

    def save_file(self, path: str | None = None) -> bool:
        target = path or self.path
        if not target:
            return self.save_file_as()
        with open(target, "wb") as fh:
            fh.write(self.data)
        self.path = target
        self.modified = False
        self._update_info()
        self.app.update_title()
        return True

    def save_file_as(self) -> bool:
        path = filedialog.asksaveasfilename(
            title=f"Save {self.profile.name} ROM",
            defaultextension=self.profile.extensions[0],
            filetypes=list(self.profile.filetypes),
        )
        if not path:
            return False
        return self.save_file(path)

    def _update_info(self) -> None:
        name = os.path.basename(self.path) if self.path else "Untitled"
        dirty = " *" if self.modified else ""
        meta = self.profile.describe(bytes(self.data))
        self.info_label.config(text=f"{self.profile.name} | {name}{dirty} | {meta}")

    def clear_selection(self) -> None:
        self.sel_anchor = None
        self.sel_end = None

    def set_cursor(self, offset: int, nibble: int = 0) -> None:
        self.cursor = max(0, min(offset, max(len(self.data) - 1, 0)))
        if len(self.data) == 0:
            self.cursor = 0
        self.nibble = nibble & 1
        self._ensure_cursor_visible()
        self.render()
        self.app.update_status()

    def _ensure_cursor_visible(self) -> None:
        line = self.cursor // BYTES_PER_LINE
        if line < self.first_line:
            self.first_line = line
        elif line >= self.first_line + VISIBLE_LINES:
            self.first_line = max(0, line - VISIBLE_LINES + 1)

    def total_lines(self) -> int:
        if not self.data:
            return 1
        return (len(self.data) + BYTES_PER_LINE - 1) // BYTES_PER_LINE

    def _line_layout(self) -> tuple[int, int, int]:
        offset_width = 10 if self.show_offsets else 0
        hex_width = 49
        ascii_width = 20 if self.show_ascii else 0
        return offset_width, hex_width, ascii_width

    def _hex_index_to_col(self, byte_index: int) -> int:
        if byte_index < 8:
            return byte_index * 3
        return byte_index * 3 + 1

    def _col_to_hex_index(self, col: int) -> int:
        if col <= 23:
            return min(7, col // 3)
        adjusted = col - 1
        return min(15, max(8, adjusted // 3))

    def byte_pos(self, line_idx: int, byte_idx: int, nibble: int = 0) -> str:
        offset_w, _, ascii_w = self._line_layout()
        hex_col = self._hex_index_to_col(byte_idx) + nibble
        if self.show_ascii:
            ascii_start = offset_w + 49
            ascii_col = ascii_start + 2 + byte_idx
            return f"{line_idx + 1}.{ascii_col}"
        return f"{line_idx + 1}.{offset_w + hex_col}"

    def pos_to_offset(self, index: str) -> tuple[int, int, str]:
        line_s, col_s = index.split(".")
        line = int(line_s) - 1
        col = int(col_s)
        offset_w, _, _ = self._line_layout()

        if self.show_offsets and col < offset_w:
            byte_idx = 0
            area = "offset"
            nibble = 0
        elif col < offset_w + 49:
            rel = col - offset_w
            byte_idx = self._col_to_hex_index(rel)
            nibble = rel % 3
            if nibble > 1:
                nibble = 1
            area = "hex"
        elif self.show_ascii:
            ascii_start = offset_w + 49
            byte_idx = max(0, min(15, col - ascii_start - 2))
            area = "ascii"
            nibble = 0
        else:
            byte_idx = 0
            area = "hex"
            nibble = 0

        offset = line * BYTES_PER_LINE + byte_idx
        return offset, nibble, area

    def render(self) -> None:
        if self._rendering:
            return
        self._rendering = True
        try:
            self.text.config(state=tk.NORMAL)
            self.text.delete("1.0", tk.END)

            total_lines = self.total_lines()
            end_line = min(self.first_line + VISIBLE_LINES, total_lines)
            sel = self.selection

            # Optimized: build lines with efficient list+join, single insert for content
            lines: list[str] = []
            for line_no in range(self.first_line, end_line):
                line_base = line_no * BYTES_PER_LINE
                chunk = self.data[line_base : line_base + BYTES_PER_LINE]
                if not chunk and line_no > 0:
                    break

                line_parts: list[str] = []
                if self.show_offsets:
                    line_parts.append(f"{line_base:08X}  ")

                # Efficient hex build
                hex_chunks: list[str] = []
                for j in range(BYTES_PER_LINE):
                    if j < len(chunk):
                        hex_chunks.append(f"{chunk[j]:02X}")
                    else:
                        hex_chunks.append("  ")
                    if j == 7:
                        hex_chunks.append(" ")
                hex_part = " ".join(hex_chunks)
                line_parts.append(f"{hex_part:<49}")

                if self.show_ascii:
                    ascii_chars = [chr(b) if 32 <= b <= 126 else "." for b in chunk]
                    ascii_part = "".join(ascii_chars).ljust(BYTES_PER_LINE)
                    line_parts.append(f" |{ascii_part}|")

                lines.append("".join(line_parts))

            if lines:
                self.text.insert(tk.END, "\n".join(lines) + "\n", "hex")  # base tag, specific tags override

            if sel:
                start, end = sel
                for off in range(start, end):
                    self._tag_byte(off, "sel")

            for mark in self.bookmarks:
                self._tag_byte(mark, "bookmark")

            if self.data:
                self._tag_byte(self.cursor, "cursor", nibble=self.nibble)
            else:
                self.text.mark_set("insert", "1.0")

            total_lines = max(1, self.total_lines())
            self.text.yview_moveto(self.first_line / total_lines)
        finally:
            self.text.config(state=tk.DISABLED)
            self._rendering = False

    def _tag_byte(self, offset: int, tag: str, nibble: int = 0) -> None:
        if offset < 0 or offset >= len(self.data):
            return
        line = offset // BYTES_PER_LINE - self.first_line
        if line < 0 or line >= VISIBLE_LINES:
            return
        byte_idx = offset % BYTES_PER_LINE
        offset_w, _, _ = self._line_layout()
        hex_start_col = offset_w + self._hex_index_to_col(byte_idx)
        start = f"{line + 1}.{hex_start_col + nibble}"
        end = f"{line + 1}.{hex_start_col + nibble + 1}"
        self.text.tag_add(tag, start, end)

    def _on_scrollbar(self, *args) -> None:
        self.text.yview(*args)
        self._sync_scroll_from_view()

    def _sync_scroll_from_view(self) -> None:
        try:
            top = float(self.text.yview()[0])
        except tk.TclError:
            return
        total = max(1, self.total_lines())
        self.first_line = int(top * total)
        self.render()

    def _on_mousewheel(self, event) -> str:
        delta = 0
        if hasattr(event, "delta") and event.delta:
            delta = -1 if event.delta > 0 else 1
        elif event.num == 4:
            delta = -1
        elif event.num == 5:
            delta = 1
        total = max(1, self.total_lines())
        self.first_line = max(0, min(self.first_line + delta, total - 1))
        self.render()
        return "break"

    def _on_click(self, event) -> None:
        self.text.focus_set()
        index = self.text.index(f"@{event.x},{event.y}")
        offset, nibble, area = self.pos_to_offset(index)
        if offset >= len(self.data) and self.data:
            offset = len(self.data) - 1
        self.cursor = offset
        self.nibble = nibble if area == "hex" else 0
        self.sel_anchor = offset
        self.sel_end = offset
        self.render()
        self.app.update_status()

    def _on_drag(self, event) -> None:
        index = self.text.index(f"@{event.x},{event.y}")
        offset, _, _ = self.pos_to_offset(index)
        offset = max(0, min(offset, max(len(self.data) - 1, 0)))
        self.sel_end = offset
        self.cursor = offset
        self.render()
        self.app.update_status()

    def _on_key(self, event) -> str:
        if not self.is_active():
            return "break"

        keysym = event.keysym
        if keysym in ("Left", "Right", "Up", "Down", "Prior", "Next", "Home", "End"):
            self._move_cursor(keysym, event.state & 0x1)
            return "break"
        if keysym in ("BackSpace", "Delete"):
            self._delete(keysym == "BackSpace")
            return "break"
        if keysym in ("Insert",):
            self.insert_mode = not self.insert_mode
            self.app.update_status()
            return "break"
        if len(event.char) == 1 and event.char in "0123456789abcdefABCDEF":
            self._edit_hex(event.char)
            return "break"
        if len(event.char) == 1 and self.show_ascii and 32 <= ord(event.char) <= 126:
            self._edit_ascii(event.char)
            return "break"
        return "break"

    def _move_cursor(self, keysym: str, shift: bool) -> None:
        if not self.data and keysym not in ("Down", "Right"):
            return
        if not shift:
            self.clear_selection()

        if keysym == "Left":
            if self.nibble == 1:
                self.nibble = 0
            elif self.cursor > 0:
                self.cursor -= 1
                self.nibble = 1
        elif keysym == "Right":
            if self.nibble == 0:
                self.nibble = 1
            elif self.cursor < len(self.data) - 1:
                self.cursor += 1
                self.nibble = 0
        elif keysym == "Up":
            self.cursor = max(0, self.cursor - BYTES_PER_LINE)
        elif keysym == "Down":
            self.cursor = min(max(len(self.data) - 1, 0), self.cursor + BYTES_PER_LINE)
        elif keysym == "Prior":
            self.first_line = max(0, self.first_line - VISIBLE_LINES)
        elif keysym == "Next":
            self.first_line = min(max(0, self.total_lines() - 1), self.first_line + VISIBLE_LINES)
        elif keysym == "Home":
            self.cursor = (self.cursor // BYTES_PER_LINE) * BYTES_PER_LINE
            self.nibble = 0
        elif keysym == "End":
            line_end = min(len(self.data), (self.cursor // BYTES_PER_LINE + 1) * BYTES_PER_LINE) - 1
            self.cursor = max(0, line_end)
            self.nibble = 1

        if shift:
            if self.sel_anchor is None:
                self.sel_anchor = self.cursor
            self.sel_end = self.cursor
        self._ensure_cursor_visible()
        self.render()
        self.app.update_status()

    def _edit_hex(self, char: str) -> None:
        if not self.data:
            return
        value = int(char, 16)
        self.push_undo()
        byte = self.data[self.cursor]
        if self.nibble == 0:
            self.data[self.cursor] = (byte & 0x0F) | (value << 4)
            self.nibble = 1
        else:
            self.data[self.cursor] = (byte & 0xF0) | value
            self.nibble = 0
            if self.cursor < len(self.data) - 1:
                self.cursor += 1
        self.mark_modified()
        self._update_info()
        self.render()
        self.app.update_status()

    def _edit_ascii(self, char: str) -> None:
        if not self.data:
            return
        self.push_undo()
        self.data[self.cursor] = ord(char)
        if self.cursor < len(self.data) - 1:
            self.cursor += 1
        self.nibble = 0
        self.mark_modified()
        self._update_info()
        self.render()
        self.app.update_status()

    def _delete(self, backspace: bool) -> None:
        sel = self.selection
        if sel:
            self.push_undo()
            start, end = sel
            del self.data[start:end]
            self.cursor = start
            self.clear_selection()
        elif self.data:
            self.push_undo()
            if backspace and self.cursor > 0:
                del self.data[self.cursor - 1]
                self.cursor -= 1
            elif not backspace and self.cursor < len(self.data):
                del self.data[self.cursor]
            else:
                self.undo_stack.pop()
                return
        else:
            return
        self.nibble = 0
        self.mark_modified()
        self._update_info()
        self.render()
        self.app.update_status()

    def _copy(self, _event=None) -> str:
        sel = self.selection
        if sel:
            chunk = self.data[sel[0] : sel[1]]
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(chunk.hex().upper())
        return "break"

    def _cut(self, _event=None) -> str:
        sel = self.selection
        if sel:
            self._copy()
            self.push_undo()
            start, end = sel
            del self.data[start:end]
            self.cursor = start
            self.clear_selection()
            self.mark_modified()
            self._update_info()
            self.render()
            self.app.update_status()
        return "break"

    def _paste(self, _event=None) -> str:
        try:
            raw = self.app.root.clipboard_get()
        except tk.TclError:
            return "break"
        cleaned = re.sub(r"[^0-9a-fA-F]", "", raw)
        if not cleaned:
            return "break"
        if len(cleaned) % 2:
            cleaned = "0" + cleaned
        blob = bytearray.fromhex(cleaned)
        self.push_undo()
        sel = self.selection
        if sel:
            start, end = sel
            self.data[start:end] = blob
            self.cursor = start + len(blob)
            self.clear_selection()
        else:
            pos = self.cursor + 1 if self.nibble == 1 and self.data else self.cursor
            self.data[pos:pos] = blob
            self.cursor = pos + len(blob) - 1
        self.nibble = 0
        self.mark_modified()
        self._update_info()
        self.render()
        self.app.update_status()
        return "break"

    def _select_all(self, _event=None) -> str:
        if self.data:
            self.sel_anchor = 0
            self.sel_end = len(self.data) - 1
            self.render()
            self.app.update_status()
        return "break"

    def _undo(self, _event=None) -> str:
        if not self.undo_stack:
            return "break"
        self.redo_stack.append((bytearray(self.data), self.cursor))
        data, cursor = self.undo_stack.pop()
        self.data = bytearray(data)
        self.cursor = cursor
        self.nibble = 0
        self.modified = True
        self._update_info()
        self.render()
        self.app.update_status()
        return "break"

    def _redo(self, _event=None) -> str:
        if not self.redo_stack:
            return "break"
        self.undo_stack.append((bytearray(self.data), self.cursor))
        data, cursor = self.redo_stack.pop()
        self.data = bytearray(data)
        self.cursor = cursor
        self.nibble = 0
        self.modified = True
        self._update_info()
        self.render()
        self.app.update_status()
        return "break"

    def find_bytes(self, query: str, start: int | None = None) -> int | None:
        cleaned = re.sub(r"[^0-9a-fA-F]", "", query)
        if not cleaned:
            text = query.encode("latin-1", errors="ignore")
            if not text:
                return None
            pattern = text
        else:
            if len(cleaned) % 2:
                cleaned = "0" + cleaned
            pattern = bytes.fromhex(cleaned)
        begin = 0 if start is None else start + 1
        idx = bytes(self.data).find(pattern, begin)
        if idx == -1 and start is not None:
            idx = bytes(self.data).find(pattern, 0)
        return idx if idx != -1 else None

    def replace_bytes(self, query: str, replacement: str) -> int:
        cleaned = re.sub(r"[^0-9a-fA-F]", "", query)
        if not cleaned:
            return 0
        if len(cleaned) % 2:
            cleaned = "0" + cleaned
        pattern = bytes.fromhex(cleaned)
        repl_clean = re.sub(r"[^0-9a-fA-F]", "", replacement)
        if len(repl_clean) % 2:
            repl_clean = "0" + repl_clean
        repl = bytes.fromhex(repl_clean) if repl_clean else b""
        hay = bytes(self.data)
        count = hay.count(pattern)
        if not count:
            return 0
        self.push_undo()
        self.data = bytearray(hay.replace(pattern, repl))
        self.mark_modified()
        self._update_info()
        self.render()
        return count

    def jump_to(self, offset: int) -> None:
        if not self.data:
            return
        self.cursor = max(0, min(offset, len(self.data) - 1))
        self.nibble = 0
        self.clear_selection()
        self._ensure_cursor_visible()
        self.render()
        self.app.update_status()

    def add_bookmark(self) -> None:
        if self.cursor not in self.bookmarks:
            self.bookmarks.append(self.cursor)
            self.bookmarks.sort()
        self.render()

    def next_bookmark(self) -> None:
        if not self.bookmarks:
            return
        for mark in self.bookmarks:
            if mark > self.cursor:
                self.jump_to(mark)
                self.bookmark_index = self.bookmarks.index(mark)
                return
        self.jump_to(self.bookmarks[0])
        self.bookmark_index = 0

    def prev_bookmark(self) -> None:
        if not self.bookmarks:
            return
        for mark in reversed(self.bookmarks):
            if mark < self.cursor:
                self.jump_to(mark)
                self.bookmark_index = self.bookmarks.index(mark)
                return
        self.jump_to(self.bookmarks[-1])
        self.bookmark_index = len(self.bookmarks) - 1


class ACSHexEditor:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ac's hex editor 0.1a")
        self.root.geometry("1100x700")
        self.root.configure(bg="#121212")

        self.BG_COLOR = "#121212"
        self.TEXT_COLOR = "#00aaff"
        self.BUTTON_BG = "#000000"
        self.SELECT_BG = "#0044aa"

        self.tabs: dict[str, HexTab] = {}
        self._find_start: int | None = None

        self.create_menu()
        self.create_toolbar()
        self.create_tabs()
        self.create_statusbar()
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self.update_status())
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_title()
        self.update_status()

    def active_tab(self) -> HexTab | None:
        try:
            idx = self.notebook.index(self.notebook.select())
            name = self.notebook.tab(idx, "text").strip()
            return self.tabs.get(name)
        except tk.TclError:
            return None

    def update_title(self) -> None:
        tab = self.active_tab()
        base = "ac's hex editor 0.1a"
        if tab and tab.path:
            name = os.path.basename(tab.path)
            star = " *" if tab.modified else ""
            self.root.title(f"{name}{star} — {base}")
        else:
            self.root.title(base)

    def update_status(self) -> None:
        tab = self.active_tab()
        if not tab:
            return
        sel = tab.selection
        if sel:
            sel_text = f"0x{sel[0]:08X}–0x{sel[1]:08X} ({sel[1]-sel[0]} bytes)"
        else:
            sel_text = "None"
        mode = "Insert" if tab.insert_mode else "Overwrite"
        endian = "Little"
        console = tab.profile.name
        offset = tab.cursor
        self.status_label.config(
            text=(
                f"{console} | Offset: 0x{offset:08X} | Selection: {sel_text} | "
                f"{mode} Mode | Endianness: {endian} | Size: {len(tab.data):,} bytes"
            )
        )

    def on_close(self) -> None:
        dirty = [t for t in self.tabs.values() if t.modified]
        if dirty:
            names = ", ".join(t.profile.name for t in dirty)
            if not messagebox.askyesno("Unsaved changes", f"Save changes before exit?\n{names}"):
                if not messagebox.askokcancel("Exit", "Discard unsaved changes and exit?"):
                    return
            else:
                for tab in dirty:
                    if not tab.save_file():
                        return
        self.root.quit()

    def create_menu(self) -> None:
        menubar = tk.Menu(
            self.root,
            bg=self.BG_COLOR,
            fg=self.TEXT_COLOR,
            activebackground=self.SELECT_BG,
            activeforeground="white",
        )

        file_menu = tk.Menu(menubar, tearoff=0, bg=self.BG_COLOR, fg=self.TEXT_COLOR, activebackground=self.SELECT_BG)
        file_menu.add_command(label="Open...", command=self.open_file, accelerator="Ctrl+O")
        file_menu.add_command(label="Save", command=self.save_file, accelerator="Ctrl+S")
        file_menu.add_command(label="Save As...", command=self.save_file_as)
        file_menu.add_separator()
        file_menu.add_command(label="New", command=self.new_file)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0, bg=self.BG_COLOR, fg=self.TEXT_COLOR, activebackground=self.SELECT_BG)
        edit_menu.add_command(label="Undo", command=lambda: self.active_tab() and self.active_tab()._undo(), accelerator="Ctrl+Z")
        edit_menu.add_command(label="Redo", command=lambda: self.active_tab() and self.active_tab()._redo(), accelerator="Ctrl+Y")
        edit_menu.add_separator()
        edit_menu.add_command(label="Find...", command=self.find_dialog, accelerator="Ctrl+F")
        edit_menu.add_command(label="Replace...", command=self.replace_dialog)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=0, bg=self.BG_COLOR, fg=self.TEXT_COLOR, activebackground=self.SELECT_BG)
        self.show_offsets_var = tk.BooleanVar(value=True)
        self.show_ascii_var = tk.BooleanVar(value=True)
        view_menu.add_checkbutton(label="Show Offsets", variable=self.show_offsets_var, command=self.toggle_offsets)
        view_menu.add_checkbutton(label="Show ASCII", variable=self.show_ascii_var, command=self.toggle_ascii)
        menubar.add_cascade(label="View", menu=view_menu)

        bookmarks_menu = tk.Menu(menubar, tearoff=0, bg=self.BG_COLOR, fg=self.TEXT_COLOR, activebackground=self.SELECT_BG)
        bookmarks_menu.add_command(label="Add Bookmark...", command=self.add_bookmark)
        bookmarks_menu.add_command(label="Manage Bookmarks", command=self.manage_bookmarks)
        menubar.add_cascade(label="Bookmarks", menu=bookmarks_menu)

        go_menu = tk.Menu(menubar, tearoff=0, bg=self.BG_COLOR, fg=self.TEXT_COLOR, activebackground=self.SELECT_BG)
        go_menu.add_command(label="Jump to Offset...", command=self.jump_dialog)
        go_menu.add_command(label="Previous Bookmark", command=self.prev_bookmark)
        go_menu.add_command(label="Next Bookmark", command=self.next_bookmark)
        menubar.add_cascade(label="Go", menu=go_menu)

        help_menu = tk.Menu(menubar, tearoff=0, bg=self.BG_COLOR, fg=self.TEXT_COLOR, activebackground=self.SELECT_BG)
        help_menu.add_command(label="About ac's hex editor 0.1a", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)
        self.root.bind_all("<Control-o>", lambda _e: self.open_file())
        self.root.bind_all("<Control-s>", lambda _e: self.save_file())
        self.root.bind_all("<Control-f>", lambda _e: self.find_dialog())

    def create_toolbar(self) -> None:
        toolbar = tk.Frame(self.root, bg=self.BG_COLOR, pady=5)
        toolbar.pack(fill=tk.X)
        actions = {
            "Open": self.open_file,
            "Save": self.save_file,
            "Cut": lambda: self.active_tab() and self.active_tab()._cut(),
            "Copy": lambda: self.active_tab() and self.active_tab()._copy(),
            "Paste": lambda: self.active_tab() and self.active_tab()._paste(),
            "Find": self.find_dialog,
            "Jump to Offset": self.jump_dialog,
        }
        for label, command in actions.items():
            btn = tk.Button(
                toolbar,
                text=label,
                command=command,
                bg=self.BUTTON_BG,
                fg=self.TEXT_COLOR,
                activebackground=self.SELECT_BG,
                activeforeground="white",
                relief=tk.FLAT,
                padx=10,
                pady=5,
                font=("Arial", 10, "bold"),
                borderwidth=1,
                highlightbackground=self.BG_COLOR,
            )
            btn.pack(side=tk.LEFT, padx=2)

    def create_tabs(self) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=self.BG_COLOR, borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=self.BUTTON_BG,
            foreground=self.TEXT_COLOR,
            padding=[12, 6],
            font=("Consolas", 10, "bold"),
        )
        style.map("TNotebook.Tab", background=[("selected", self.SELECT_BG)])

        for name, profile in CONSOLE_PROFILES.items():
            frame = tk.Frame(self.notebook, bg=self.BG_COLOR)
            self.notebook.add(frame, text=f" {name} ")
            self.tabs[name] = HexTab(frame, profile, self)

    def create_statusbar(self) -> None:
        status_bar = tk.Frame(self.root, bg=self.BUTTON_BG, pady=5)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_label = tk.Label(
            status_bar,
            text="Offset: 0x00000000 | Selection: None | Overwrite Mode | Endianness: Little",
            bg=self.BUTTON_BG,
            fg=self.TEXT_COLOR,
            font=("Consolas", 10),
            anchor="w",
            padx=10,
        )
        self.status_label.pack(fill=tk.X)

    def open_file(self) -> None:
        tab = self.active_tab()
        if not tab:
            return
        path = filedialog.askopenfilename(
            title=f"Open {tab.profile.name} ROM",
            filetypes=list(tab.profile.filetypes),
        )
        if path:
            try:
                tab.load_file(path)
            except Exception as exc:
                messagebox.showerror("Open failed", f"{type(exc).__name__}: {exc}")

    def save_file(self) -> None:
        tab = self.active_tab()
        if tab:
            try:
                if not tab.path:
                    tab.save_file_as()
                else:
                    tab.save_file()
            except OSError as exc:
                messagebox.showerror("Save failed", str(exc))

    def save_file_as(self) -> None:
        tab = self.active_tab()
        if tab:
            try:
                tab.save_file_as()
            except OSError as exc:
                messagebox.showerror("Save failed", str(exc))

    def new_file(self) -> None:
        tab = self.active_tab()
        if tab and messagebox.askyesno("New file", f"Clear current {tab.profile.name} document?"):
            tab.new_file()

    def toggle_offsets(self) -> None:
        tab = self.active_tab()
        if tab:
            tab.show_offsets = self.show_offsets_var.get()
            tab.render()

    def toggle_ascii(self) -> None:
        tab = self.active_tab()
        if tab:
            tab.show_ascii = self.show_ascii_var.get()
            tab.render()

    def find_dialog(self) -> None:
        tab = self.active_tab()
        if not tab:
            return
        query = simpledialog.askstring("Find", "Hex (FF00A1) or text:", parent=self.root)
        if not query:
            return
        idx = tab.find_bytes(query, self._find_start)
        if idx is None:
            messagebox.showinfo("Find", "Pattern not found.")
            self._find_start = None
            return
        self._find_start = idx
        tab.sel_anchor = idx
        end = idx + max(1, len(re.sub(r"[^0-9a-fA-F]", "", query)) // 2)
        tab.sel_end = min(end, len(tab.data))
        tab.jump_to(idx)

    def replace_dialog(self) -> None:
        tab = self.active_tab()
        if not tab:
            return
        query = simpledialog.askstring("Replace", "Find hex pattern:", parent=self.root)
        if not query:
            return
        repl = simpledialog.askstring("Replace", "Replace with hex:", parent=self.root)
        if repl is None:
            return
        count = tab.replace_bytes(query, repl)
        messagebox.showinfo("Replace", f"Replaced {count} occurrence(s).")

    def jump_dialog(self) -> None:
        tab = self.active_tab()
        if not tab:
            return
        raw = simpledialog.askstring("Jump to Offset", "Offset (hex or decimal):", parent=self.root)
        if not raw:
            return
        try:
            offset = int(raw, 0)
        except ValueError:
            messagebox.showerror("Jump", "Invalid offset.")
            return
        tab.jump_to(offset)

    def add_bookmark(self) -> None:
        tab = self.active_tab()
        if tab:
            tab.add_bookmark()

    def manage_bookmarks(self) -> None:
        tab = self.active_tab()
        if not tab:
            return
        if not tab.bookmarks:
            messagebox.showinfo("Bookmarks", "No bookmarks yet.")
            return
        lines = "\n".join(f"0x{b:08X}" for b in tab.bookmarks)
        messagebox.showinfo("Bookmarks", lines)

    def next_bookmark(self) -> None:
        tab = self.active_tab()
        if tab:
            tab.next_bookmark()

    def prev_bookmark(self) -> None:
        tab = self.active_tab()
        if tab:
            tab.prev_bookmark()

    def show_about(self) -> None:
        consoles = ", ".join(CONSOLE_PROFILES)
        messagebox.showinfo(
            "About",
            "ac's hex editor 0.1a\n\n"
            "Optimized single-file ROM hex editor (Atari → PS5)\n"
            "Python 3.14 ready • vibe-coded for AC Holdings retro dev\n"
            f"Supported tabs: {consoles}.\n\n"
            "Open ROMs per console tab, edit hex/ASCII live, find/replace, bookmarks, save.",
        )


if __name__ == "__main__":
    root = tk.Tk()
    root.minsize(800, 500)
    ACSHexEditor(root)
    root.mainloop()
