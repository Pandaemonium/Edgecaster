# edgecaster/scenes/audio_manager.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Any
import os
import random
import pygame


# A dedicated event for "music ended" notifications.
# (You can pick a different number if you already use USEREVENT+10 elsewhere.)
MUSIC_END_EVENT = pygame.USEREVENT + 10


@dataclass
class MusicRequest:
    """
    Declarative music intent (from a scene or an event).
    Only ONE track plays at a time.
    """
    key: Optional[str] = None                 # a music registry key (preferred)
    path: Optional[str] = None                # or a direct file path
    playlist: Optional[Sequence[str]] = None  # keys or paths; manager decides one
    loop: bool = True
    fade_out_ms: int = 700
    fade_in_ms: int = 700
    hard_cut: bool = False                    # abrupt swap (no fades)


class AudioManager:
    """
    One-at-a-time music controller + layered SFX (later).

    Core features:
      - base music from scene stack
      - temporary overrides (dialogue window open)
      - interrupt music (event track plays, then resume previous)
      - fade transitions (best-effort using pygame.mixer.music)
    """

    def __init__(self, *, enabled_music: bool = True, enabled_sfx: bool = True) -> None:
        self.enabled_music = bool(enabled_music)
        self.enabled_sfx = bool(enabled_sfx)

        self._initted = False

        # Registry: key -> filepath
        self.music_registry: dict[str, str] = {}

        # Current / pending state
        self._current_id: Optional[str] = None   # "key:xxx" or "path:..."
        self._current_loop: bool = True

        self._pending: Optional[tuple[str, bool, int]] = None
        # pending = (music_id, loop, fade_in_ms)

        # Override stack (dialogue etc). Top wins.
        self._override_stack: list[MusicRequest] = []

        # Interrupt/resume support
        self._resume_target: Optional[MusicRequest] = None
        self._interrupt_active: bool = False

        # Deterministic-ish randomness if you want later
        self._rng = random.Random()
        # Playlist sequencing state
        self._active_playlist: list[str] | None = None
        self._playlist_index: int = 0
        self._playlist_loop: bool = True
        # Saved playlist state for interrupts (event stingers)
        self._saved_playlist: list[str] | None = None
        self._saved_playlist_index: int = 0
        self._saved_playlist_loop: bool = True



    # ---------------------------- Init / plumbing ----------------------------

    def ensure_init(self) -> None:
        if self._initted:
            return
        try:
            if not pygame.mixer.get_init():
                # If you later want lower latency, tweak buffer/frequency here.
                pygame.mixer.init()
            pygame.mixer.music.set_endevent(MUSIC_END_EVENT)
        except Exception:
            # Fail-soft: audio just won't play.
            self.enabled_music = False
            self.enabled_sfx = False
        self._initted = True

    def set_music_enabled(self, on: bool) -> None:
        self.enabled_music = bool(on)
        if not self.enabled_music:
            self.stop_music(hard_cut=True)

    def set_sfx_enabled(self, on: bool) -> None:
        self.enabled_sfx = bool(on)
        # If/when you add active SFX channels, you can stop them here when disabled.



    # ---------------------------- Registry helpers ----------------------------

    def register_music(self, key: str, path: str) -> None:
        self.music_registry[str(key)] = str(path)

    def register_music_many(self, mapping: dict[str, str]) -> None:
        for k, p in (mapping or {}).items():
            self.register_music(k, p)

    def resolve_to_path(self, req: MusicRequest) -> Optional[str]:
        """
        Returns a filesystem path to load, or None if impossible.

        NOTE:
        - This resolves ONLY single-track requests (key/path).
        - Playlists are handled explicitly in set_music() so we can sequence them.
        """
        if req is None:
            return None

        if req.key:
            return self.music_registry.get(req.key)

        if req.path:
            return req.path

        return None


    # ---------------------------- Low-level music ops ----------------------------

    def _resolve_entry_to_path(self, entry: str) -> Optional[str]:
        # playlist entries can be music keys or direct paths
        if entry in self.music_registry:
            return self.music_registry[entry]
        return str(entry) if entry else None


    def _make_id(self, *, key: Optional[str], path: Optional[str]) -> Optional[str]:
        if key:
            return f"key:{key}"
        if path:
            return f"path:{os.path.abspath(path)}"
        return None

    def stop_music(self, *, hard_cut: bool = False, fade_out_ms: int = 300) -> None:
        self.ensure_init()
        self._pending = None
        self._interrupt_active = False
        self._resume_target = None



        try:
            if hard_cut or fade_out_ms <= 0:
                pygame.mixer.music.stop()
            else:
                pygame.mixer.music.fadeout(int(fade_out_ms))
        except Exception:
            pass

        self._current_id = None

    def _play_path_now(self, path: str, *, loop: bool, fade_in_ms: int) -> None:
        self.ensure_init()
        if not self.enabled_music:
            return

        try:
            pygame.mixer.music.load(path)
            loops = -1 if loop else 0
            if fade_in_ms and fade_in_ms > 0:
                pygame.mixer.music.play(loops=loops, fade_ms=int(fade_in_ms))
            else:
                pygame.mixer.music.play(loops=loops)
        except Exception:
            # Fail-soft
            self._current_id = None

    def set_music(self, req: Optional[MusicRequest]) -> None:
        """
        Set the "desired" music immediately (respecting fades).
        This is what SceneManager should call when scene stack changes.
        """
        self.ensure_init()

        if not self.enabled_music:
            self.stop_music(hard_cut=True)
            return

        if req is None or (req.key is None and req.path is None and not req.playlist):
            self.stop_music(hard_cut=False, fade_out_ms=400)
            return

        # --- PLAYLIST REQUEST (sequential ambient) ---
        if req.playlist:
            desired = list(req.playlist)
            # If we are already running this exact playlist, don't restart it.
            if self._active_playlist == desired:
                # Don't restart the playlist *only if* something is actually playing or queued.
                self._playlist_loop = bool(req.loop)

                is_playing = False
                try:
                    is_playing = bool(pygame.mixer.music.get_busy())
                except Exception:
                    is_playing = False

                if is_playing or self._pending:
                    return

                # Playlist matches but nothing is playing (e.g. we were muted/stopped).
                # Resume from the current index.
                entry = self._active_playlist[self._playlist_index] if self._active_playlist else ""
                path = self._resolve_entry_to_path(entry)
                if path:
                    self._play_path_now(path, loop=False, fade_in_ms=req.fade_in_ms)
                    self._current_id = self._make_id(key=None, path=path)
                    self._current_loop = False
                return


            # New playlist: install + start at index 0
            self._active_playlist = desired
            self._playlist_index = 0
            self._playlist_loop = bool(req.loop)

            first_entry = self._active_playlist[self._playlist_index] if self._active_playlist else ""
            path = self._resolve_entry_to_path(first_entry)
            if not path:
                return

            # For playlists, individual tracks must NOT loop, or MUSIC_END_EVENT never fires.
            track_loop = False
            music_id = self._make_id(key=None, path=path)

            # Transition behavior mirrors single-track logic below
            if req.hard_cut:
                self.stop_music(hard_cut=True)
                self._play_path_now(path, loop=track_loop, fade_in_ms=req.fade_in_ms)
                self._current_id = music_id
                self._current_loop = track_loop
                return

            try:
                if req.fade_out_ms and req.fade_out_ms > 0:
                    pygame.mixer.music.fadeout(int(req.fade_out_ms))
                    self._pending = (music_id or "unknown", track_loop, int(req.fade_in_ms))
                    self._pending_path = path  # type: ignore[attr-defined]
                    self._pending_delay_ms = int(req.fade_out_ms)  # type: ignore[attr-defined]
                    self._pending_started_at = pygame.time.get_ticks()  # type: ignore[attr-defined]
                else:
                    self._play_path_now(path, loop=track_loop, fade_in_ms=req.fade_in_ms)
                    self._current_id = music_id
                    self._current_loop = track_loop
                    self._pending = None
            except Exception:
                pass
            return

        # --- SINGLE TRACK REQUEST (key/path) ---
        path = self.resolve_to_path(req)
        if not path:
            return

        # Any single-track request cancels playlist mode
        self._active_playlist = None
        self._playlist_index = 0

        # Track playlist intent (for sequencing)
        if req.playlist:
            self._active_playlist = list(req.playlist)
            self._playlist_loop = bool(req.loop)
            # If switching playlists, reset index
            if self._playlist_index >= len(self._active_playlist):
                self._playlist_index = 0
        else:
            self._active_playlist = None
            self._playlist_index = 0

        music_id = self._make_id(key=req.key, path=path)
        if music_id and music_id == self._current_id and bool(req.loop) == bool(self._current_loop):
            # Already playing what we want.
            return

        # Decide transition
        if req.hard_cut:
            self.stop_music(hard_cut=True)
            self._play_path_now(path, loop=bool(req.loop), fade_in_ms=req.fade_in_ms)
            self._current_id = music_id
            self._current_loop = bool(req.loop)
            return

        # Fade-out then start.
        # pygame.mixer.music is single-channel, so we schedule a delayed start.
        try:
            if req.fade_out_ms and req.fade_out_ms > 0:
                pygame.mixer.music.fadeout(int(req.fade_out_ms))
                self._pending = (music_id or "unknown", bool(req.loop), int(req.fade_in_ms))
                # store the actual pending path in a side-field
                self._pending_path = path  # type: ignore[attr-defined]
                self._pending_delay_ms = int(req.fade_out_ms)  # type: ignore[attr-defined]
                self._pending_started_at = pygame.time.get_ticks()  # type: ignore[attr-defined]
            else:
                self._play_path_now(path, loop=bool(req.loop), fade_in_ms=req.fade_in_ms)
                self._current_id = music_id
                self._current_loop = bool(req.loop)
                self._pending = None
        except Exception:
            pass



    # ---------------------------- Overrides & interrupts ----------------------------

    def push_override(self, req: MusicRequest) -> None:
        """
        Dialogue / UI windows should push an override when opened,
        and pop it when closed. Top override wins.
        """
        self._override_stack.append(req)

    def pop_override(self) -> None:
        if self._override_stack:
            self._override_stack.pop()

    def clear_overrides(self) -> None:
        self._override_stack.clear()

    def current_override(self) -> Optional[MusicRequest]:
        return self._override_stack[-1] if self._override_stack else None

    def interrupt_then_resume(
        self,
        req: MusicRequest,
        *,
        resume_to: Optional[MusicRequest],
    ) -> None:
        self.ensure_init()
        if not self.enabled_music:
            return

        # --- SAVE playlist state (so we can resume without restarting at index 0) ---
        if self._active_playlist:
            self._saved_playlist = list(self._active_playlist)
            self._saved_playlist_index = int(self._playlist_index)
            self._saved_playlist_loop = bool(self._playlist_loop)
        else:
            self._saved_playlist = None
            self._saved_playlist_index = 0
            self._saved_playlist_loop = True

        self._resume_target = resume_to
        self._interrupt_active = True

        self.set_music(req)


    def is_interrupting(self) -> bool:
        """True while a one-shot interrupt track is active (stinger/fanfare)."""
        return bool(getattr(self, "_interrupt_active", False))


    # ---------------------------- Update / event handling ----------------------------

    def update(self) -> None:
        """
        Called each frame (preferably from SceneManager live loop).
        Handles delayed fade-out -> play transitions.
        """
        if not self._pending:
            return
        try:
            now = pygame.time.get_ticks()
            started = getattr(self, "_pending_started_at", now)
            delay = getattr(self, "_pending_delay_ms", 0)
            if (now - started) >= delay:
                path = getattr(self, "_pending_path", None)
                music_id, loop, fade_in_ms = self._pending
                self._pending = None
                if path:
                    self._play_path_now(path, loop=loop, fade_in_ms=fade_in_ms)
                    self._current_id = music_id
                    self._current_loop = loop
        except Exception:
            self._pending = None

    def handle_pygame_event(self, event: pygame.event.Event) -> bool:
        if event.type != MUSIC_END_EVENT:
            return False

        # Interrupt logic wins: event track ended -> resume target
        if self._interrupt_active:
            self._interrupt_active = False
            resume = self._resume_target
            self._resume_target = None

            # --- RESTORE playlist state if we're resuming a playlist ---
            if resume is not None and resume.playlist:
                if self._saved_playlist is not None and list(resume.playlist) == list(self._saved_playlist):
                    self._active_playlist = list(self._saved_playlist)
                    self._playlist_index = int(self._saved_playlist_index)
                    self._playlist_loop = bool(self._saved_playlist_loop)

                # clear saved snapshot either way
                self._saved_playlist = None
                self._saved_playlist_index = 0
                self._saved_playlist_loop = True

            if resume is not None:
                self.set_music(resume)
            return True

        # Playlist sequencing: advance to next track
        if self._active_playlist:
            self._playlist_index += 1
            if self._playlist_index >= len(self._active_playlist):
                if not self._playlist_loop:
                    self._active_playlist = None
                    return True
                self._playlist_index = 0

            entry = self._active_playlist[self._playlist_index]
            path = self._resolve_entry_to_path(entry)
            if path:
                # IMPORTANT: don't call set_music() here (it can cancel playlist mode)
                music_id = self._make_id(key=None, path=path)
                self._play_path_now(path, loop=False, fade_in_ms=800)
                self._current_id = music_id
                self._current_loop = False
            return True

        return True


