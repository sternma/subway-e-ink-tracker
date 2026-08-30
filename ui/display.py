import logging
import csv
import os
import traceback
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from PIL import Image
from pathlib import Path
from typing import Optional
import threading
import time

from data.models import AppData
from ui.layout import getImageFromAppData
from ui.lcd_display import DisplayUnavailableError, LcdDisplay
from ui.render_cache import RenderCache
from ui.time_format import displayed_clock
from config.config import config

logger = logging.getLogger(__name__)
render_cache = RenderCache()


class DisplayIntent(Enum):
    NORMAL = "normal"
    SCREEN_TRANSITION = "screen_transition"
    MAINTENANCE_CLEAR = "maintenance_clear"


_INTENT_PRIORITY = {
    DisplayIntent.NORMAL: 0,
    DisplayIntent.MAINTENANCE_CLEAR: 1,
    DisplayIntent.SCREEN_TRANSITION: 2,
}


@dataclass
class DisplayFrame:
    image: Image.Image
    partial: bool
    clear: bool
    screen_name: Optional[str]
    render_requested_at: Optional[datetime]
    queued_at: float
    sequence: int
    displayed_clock: str
    intent: DisplayIntent = DisplayIntent.NORMAL
    overwritten_before_consume: int = 0


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value).isoformat(timespec="microseconds")


def _safe_filename_part(value: str) -> str:
    safe = "".join(c if c.isalnum() else "-" for c in value.strip())
    return "-".join(part for part in safe.split("-") if part) or "unknown"


def _coerce_intent(intent: DisplayIntent | str | None, clear: bool) -> DisplayIntent:
    if intent is None:
        return DisplayIntent.MAINTENANCE_CLEAR if clear else DisplayIntent.NORMAL
    if isinstance(intent, DisplayIntent):
        return intent
    return DisplayIntent(intent)


class DebugDisplay:
    def __init__(self, output_dir: Path | str = "debug_output", history_enabled: Optional[bool] = None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.current_image_path = self.output_dir / "current_display.png"
        self.history_enabled = (
            _bool_env("DEBUG_FRAME_HISTORY")
            if history_enabled is None
            else history_enabled
        )
        self.history_dir = self.output_dir / "frames"
        self.manifest_path = self.output_dir / "frame_manifest.csv"
        if self.history_enabled:
            self.history_dir.mkdir(exist_ok=True)
    
    def initialize(self):
        """Initialize the debug display"""
        logger.info("Initialized debug display - images will be saved to debug_output/current_display.png")
        if self.history_enabled:
            logger.info(
                "Debug frame history enabled - timestamped frames will be saved to %s",
                self.history_dir,
            )
        
    def update(
        self,
        img: Image.Image,
        partial: bool = False,
        clear: bool = False,
        metadata: DisplayFrame | None = None,
    ):
        """Update the debug display with new data"""
        try:
            self._update_display(img, metadata)
                
        except Exception as e:
            logger.error(f"Error updating debug display: {str(e)}")
            raise

    def _update_display(self, img: Image.Image, metadata: DisplayFrame | None = None):
        """Save the image without automatically opening it"""
        try:
            img.save(self.current_image_path)
            logger.info(f"Saved display image to {self.current_image_path}")
            if self.history_enabled:
                self._save_history_frame(img, metadata)
                
        except Exception as e:
            logger.error(f"Error updating debug display: {str(e)}")
            raise

    def _save_history_frame(self, img: Image.Image, metadata: DisplayFrame | None):
        consumed_at = time.time()
        sequence = metadata.sequence if metadata else 0
        screen_name = metadata.screen_name if metadata and metadata.screen_name else "unknown"
        clock = metadata.displayed_clock if metadata else ""
        filename = (
            f"{sequence:06d}_"
            f"{datetime.fromtimestamp(consumed_at).strftime('%Y%m%d-%H%M%S-%f')}_"
            f"{_safe_filename_part(screen_name)}"
        )
        if clock:
            filename += f"_{_safe_filename_part(clock)}"
        frame_path = self.history_dir / f"{filename}.png"
        img.save(frame_path)
        self._append_manifest(frame_path, consumed_at, metadata)

    def _append_manifest(self, frame_path: Path, consumed_at: float, metadata: DisplayFrame | None):
        fieldnames = [
            "sequence",
            "frame_path",
            "screen_name",
            "displayed_clock",
            "intent",
            "partial",
            "clear",
            "render_requested_at",
            "queued_at",
            "consumed_at",
            "queue_wait_seconds",
            "overwritten_before_consume",
        ]
        write_header = self._prepare_manifest_for_append(fieldnames)
        row = {
            "sequence": metadata.sequence if metadata else "",
            "frame_path": str(frame_path),
            "screen_name": metadata.screen_name if metadata else "",
            "displayed_clock": metadata.displayed_clock if metadata else "",
            "intent": metadata.intent.value if metadata else "",
            "partial": metadata.partial if metadata else "",
            "clear": metadata.clear if metadata else "",
            "render_requested_at": (
                metadata.render_requested_at.isoformat(timespec="microseconds")
                if metadata and metadata.render_requested_at
                else ""
            ),
            "queued_at": _timestamp(metadata.queued_at) if metadata else "",
            "consumed_at": _timestamp(consumed_at),
            "queue_wait_seconds": (
                f"{consumed_at - metadata.queued_at:.6f}" if metadata else ""
            ),
            "overwritten_before_consume": metadata.overwritten_before_consume if metadata else "",
        }
        with self.manifest_path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            writer.writerow(row)

    def _prepare_manifest_for_append(self, fieldnames: list[str]) -> bool:
        if not self.manifest_path.exists() or self.manifest_path.stat().st_size == 0:
            return True

        with self.manifest_path.open(newline="") as f:
            reader = csv.reader(f)
            existing_header = next(reader, None)

        if existing_header == fieldnames:
            return False

        rotated_path = self._rotated_manifest_path()
        self.manifest_path.rename(rotated_path)
        logger.warning(
            "Rotated debug frame manifest with stale schema to %s",
            rotated_path,
        )
        return True

    def _rotated_manifest_path(self) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        candidate = self.manifest_path.with_name(
            f"{self.manifest_path.stem}.{timestamp}{self.manifest_path.suffix}"
        )
        counter = 1
        while candidate.exists():
            candidate = self.manifest_path.with_name(
                f"{self.manifest_path.stem}.{timestamp}.{counter}{self.manifest_path.suffix}"
            )
            counter += 1
        return candidate

class Display:
    def __init__(self):
        self.display = None
        self.render_cache = render_cache
        self.next_frame = None
        self.clear_cooldown_seconds = config.timing.DISPLAY_CLEAR_COOLDOWN_SECONDS
        self._large_update_cooldown_until = 0
        self._queue_lock = threading.Lock()
        self._queue_ready = threading.Condition(self._queue_lock)
        self._frame_sequence = 0
        self._pending_overwrite_count = 0
        self.update_thread = threading.Thread(target=self._process_queue)
        self.update_thread.daemon = True
        self.update_thread.start()
    
    def initialize(self):
        """Initialize the appropriate display based on config"""
        if config.DEBUG:
            self.display = DebugDisplay()
        else:
            try:
                self.display = LcdDisplay(
                    rotation=config.DISPLAY_ROTATION,
                    device=config.DISPLAY_DEVICE,
                )
            except DisplayUnavailableError as exc:
                raise RuntimeError(
                    f"Cannot open the panel ({exc}). Set DEBUG=true to render to "
                    "debug_output/ instead of a physical display."
                ) from exc

        self.display.initialize()

    def _process_queue(self):
        """Process the latest queued frame, waking when new work becomes available."""
        while True:
            try:
                frame = self._wait_for_next_frame()
                self.display.update(frame.image, frame.partial, frame.clear, frame)
                if self._starts_large_update_cooldown(frame):
                    with self._queue_ready:
                        self._large_update_cooldown_until = time.time() + self.clear_cooldown_seconds
                        self._queue_ready.notify_all()
            except Exception as e:
                logger.error(f"Error processing update queue: {str(e)}")
                logger.error(traceback.format_exc())

    def _wait_for_next_frame(self) -> DisplayFrame:
        with self._queue_ready:
            while True:
                frame = self._take_next_frame_locked(time.time())
                if frame is not None:
                    return frame

                timeout = self._seconds_until_next_consumable_frame(time.time())
                self._queue_ready.wait(timeout=timeout)

    def _take_next_frame(self, now: float) -> DisplayFrame | None:
        with self._queue_ready:
            return self._take_next_frame_locked(now)

    def _take_next_frame_locked(self, now: float) -> DisplayFrame | None:
        if self.next_frame is None:
            return None
        if (
            self.next_frame.intent == DisplayIntent.NORMAL
            and now < self._large_update_cooldown_until
        ):
            return None

        frame = self.next_frame
        self.next_frame = None
        frame.overwritten_before_consume = self._pending_overwrite_count
        self._pending_overwrite_count = 0
        return frame

    def _seconds_until_next_consumable_frame(self, now: float) -> float | None:
        if self.next_frame is None:
            return None
        if (
            self.next_frame.intent == DisplayIntent.NORMAL
            and now < self._large_update_cooldown_until
        ):
            return max(0, self._large_update_cooldown_until - now)
        return 0

    def _can_replace_pending_frame(self, intent: DisplayIntent) -> bool:
        if self.next_frame is None:
            return True
        return _INTENT_PRIORITY[intent] >= _INTENT_PRIORITY[self.next_frame.intent]

    def _queue_frame(self, frame: DisplayFrame) -> bool:
        with self._queue_ready:
            if not self._can_replace_pending_frame(frame.intent):
                logger.info(
                    "Ignoring display frame sequence=%s intent=%s because pending intent=%s has higher priority",
                    frame.sequence,
                    frame.intent.value,
                    self.next_frame.intent.value,
                )
                return False

            if self.next_frame is not None:
                self._pending_overwrite_count += 1
                logger.info(
                    "Replacing queued display frame with sequence=%s (pending_overwrites=%s)",
                    frame.sequence,
                    self._pending_overwrite_count,
                )
            self.next_frame = frame
            self._queue_ready.notify()
            return True

    def _can_accept_intent_without_render(self, intent: DisplayIntent) -> bool:
        with self._queue_lock:
            return self._can_replace_pending_frame(intent)

    def _next_frame_sequence(self) -> int:
        with self._queue_lock:
            self._frame_sequence += 1
            return self._frame_sequence

    def _starts_large_update_cooldown(self, frame: DisplayFrame) -> bool:
        return frame.intent in {
            DisplayIntent.SCREEN_TRANSITION,
            DisplayIntent.MAINTENANCE_CLEAR,
        }

    def prewarm(
        self,
        app_data: AppData,
        now: Optional[datetime],
        screen_names: list[str],
    ) -> None:
        self.render_cache.prewarm(
            app_data,
            now,
            screen_names,
            getImageFromAppData,
        )

    def update(
        self,
        app_data: AppData,
        now: Optional[datetime] = None,
        screen_name: Optional[str] = None,
        partial: bool = False,
        clear: bool = False,
        intent: DisplayIntent | str | None = None,
    ):
        """Queue an update for the display with new data"""
        try:
            logger.info("Generating display image...")
            display_intent = _coerce_intent(intent, clear)
            if not self._can_accept_intent_without_render(display_intent):
                logger.info("Skipping %s render because a higher-priority frame is already pending", display_intent.value)
                return False

            cacheable = not (
                screen_name == "transit"
                and display_intent == DisplayIntent.NORMAL
            )
            img = self.render_cache.get_or_render(
                app_data,
                now,
                screen_name,
                getImageFromAppData,
                cacheable=cacheable,
            )
            queued_at = time.time()
            frame = DisplayFrame(
                image=img,
                partial=partial,
                clear=clear,
                screen_name=screen_name,
                render_requested_at=now,
                queued_at=queued_at,
                sequence=self._next_frame_sequence(),
                displayed_clock=displayed_clock(now, compact=True),
                intent=display_intent,
            )
            queued = self._queue_frame(frame)
            return queued
                
        except Exception as e:
            logger.error(f"Error queuing display update: {str(e)}")
            logger.error(traceback.format_exc())
            raise
