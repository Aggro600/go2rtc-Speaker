"""Media player that plays any audio (TTS, media source, URL) on a camera speaker via go2rtc two-way audio.

Flow:
1. Home Assistant converts the audio with ffmpeg into an 8 kHz mono WAV (optional lead-in / tail silence,
   volume) and serves it under a random URL (go2rtc refuses sources containing spaces, so filters can't be
   passed to go2rtc directly).
2. go2rtc is told to play it into the stream's backchannel:
   POST /api/streams?dst=<stream>&src=ffmpeg:<clip>#audio=<codec>#input=file
3. Some cameras (e.g. Reolink doorbells) sometimes reject the backchannel; this is detected and retried.
4. When the clip has ended, its producer disappears from /api/streams and the player goes idle.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from urllib.parse import quote

from aiohttp import web
import voluptuous as vol

from homeassistant.components import media_source
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.media_player import (
    PLATFORM_SCHEMA as MEDIA_PLAYER_PLATFORM_SCHEMA,
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.const import CONF_NAME, CONF_UNIQUE_ID, CONF_URL
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import get_url

_LOGGER = logging.getLogger(__name__)

DOMAIN = "go2rtc_speaker"
CLIPS: dict[str, bytes] = {}

PLATFORM_SCHEMA = MEDIA_PLAYER_PLATFORM_SCHEMA.extend({
    vol.Required(CONF_NAME): cv.string,
    vol.Required(CONF_URL): cv.url,
    vol.Required("stream"): cv.string,
    vol.Optional("codec", default="pcmu"): vol.In(["pcmu", "pcma", "pcm", "opus", "aac"]),
    vol.Optional(CONF_UNIQUE_ID): cv.string,
    vol.Optional("lead_in_ms", default=0): cv.positive_int,
    vol.Optional("tail_s", default=8.0): vol.All(vol.Coerce(float), vol.Range(min=0, max=60)),
    vol.Optional("max_seconds", default=300): cv.positive_int,
    vol.Optional("retries", default=5): vol.All(vol.Coerce(int), vol.Range(min=1, max=20)),
})


class ClipView(HomeAssistantView):
    """Serves the prepared clip to go2rtc (random token, no auth, only the latest clip is kept)."""

    url = "/api/go2rtc_speaker/{token}.wav"
    name = "api:go2rtc_speaker"
    requires_auth = False

    async def get(self, request: web.Request, token: str) -> web.Response:
        data = CLIPS.get(token)
        if data is None:
            return web.Response(status=404)
        return web.Response(body=data, content_type="audio/wav")


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    if not hass.data.get(DOMAIN):
        hass.http.register_view(ClipView())
        hass.data[DOMAIN] = True
    async_add_entities([Go2rtcSpeaker(config)])


class Go2rtcSpeaker(MediaPlayerEntity):
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.MEDIA_ANNOUNCE
    )
    _attr_should_poll = False
    _attr_media_content_type = MediaType.MUSIC

    def __init__(self, config) -> None:
        self._attr_name = config[CONF_NAME]
        self._attr_unique_id = config.get(CONF_UNIQUE_ID) or f"{DOMAIN}_{config['stream']}"
        self._base = config[CONF_URL].rstrip("/")
        self._stream = config["stream"]
        self._codec = config["codec"]
        self._lead_in = config["lead_in_ms"]
        self._tail = config["tail_s"]
        self._max = config["max_seconds"]
        self._retries = config["retries"]
        self._attr_state = MediaPlayerState.IDLE
        self._attr_volume_level = 1.0
        self._task: asyncio.Task | None = None
        self._clip_url = ""

    # ---------- go2rtc API ----------
    async def _post(self, src: str) -> None:
        session = async_get_clientsession(self.hass)
        url = f"{self._base}/api/streams?dst={quote(self._stream)}&src={quote(src, safe='')}"
        async with asyncio.timeout(15), session.post(url) as resp:
            if resp.status >= 400:
                raise HomeAssistantError(f"go2rtc HTTP {resp.status}: {(await resp.text())[:200]}")

    async def _stream_info(self) -> dict:
        session = async_get_clientsession(self.hass)
        async with asyncio.timeout(5), session.get(f"{self._base}/api/streams") as resp:
            data = await resp.json(content_type=None)
        return data.get(self._stream) or {}

    async def _clip_active(self) -> bool:
        producers = (await self._stream_info()).get("producers") or []
        return any(p.get("url") == self._clip_url and p.get("format_name") for p in producers)

    async def _backchannel_connected(self) -> bool:
        """True once go2rtc holds an outgoing (sendonly) audio connection to the camera."""
        for consumer in (await self._stream_info()).get("consumers") or []:
            if (consumer.get("format_name") == "rtsp"
                    and any("sendonly" in m for m in consumer.get("medias") or [])
                    and not str(consumer.get("remote_addr", "")).startswith("127.")):
                return True
        return False

    async def _start(self, src: str) -> None:
        for attempt in range(self._retries):
            await self._post(src)
            for _ in range(10):
                await asyncio.sleep(0.4)
                try:
                    if await self._backchannel_connected():
                        if attempt:
                            _LOGGER.info("%s: backchannel connected on attempt %s", self.entity_id, attempt + 1)
                        return
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("go2rtc poll failed: %s", err)
            _LOGGER.warning("%s: camera did not accept the audio backchannel, retrying", self.entity_id)
            await self._post("")
            await asyncio.sleep(3)
        raise HomeAssistantError(f"{self._stream}: camera did not accept the audio backchannel")

    # ---------- audio preparation ----------
    async def _prepare(self, url: str) -> bytes:
        filters = []
        if self._lead_in:
            filters.append(f"adelay={self._lead_in}:all=1")
        if self._tail:
            filters.append(f"apad=pad_dur={self._tail}")
        if self._attr_volume_level < 0.99:
            filters.append(f"volume={self._attr_volume_level}")
        args = ["ffmpeg", "-v", "error", "-i", url, "-vn", "-ac", "1", "-ar", "8000"]
        if filters:
            args += ["-af", ",".join(filters)]
        args += ["-c:a", "pcm_s16le", "-f", "wav", "-"]
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            async with asyncio.timeout(60):
                out, err = await proc.communicate()
        except TimeoutError:
            proc.kill()
            raise HomeAssistantError("ffmpeg timed out") from None
        if proc.returncode or not out:
            raise HomeAssistantError(f"ffmpeg: {err.decode(errors='ignore')[:200]}")
        return out

    # ---------- media player ----------
    async def async_set_volume_level(self, volume: float) -> None:
        self._attr_volume_level = round(volume, 2)
        self.async_write_ha_state()

    async def async_browse_media(self, media_content_type=None, media_content_id=None) -> BrowseMedia:
        return await media_source.async_browse_media(
            self.hass, media_content_id, content_filter=lambda item: item.media_content_type.startswith("audio/"))

    async def async_play_media(self, media_type, media_id, **kwargs) -> None:
        if media_source.is_media_source_id(media_id):
            item = await media_source.async_resolve_media(self.hass, media_id, self.entity_id)
            media_id = item.url
        url = async_process_play_media_url(self.hass, media_id)
        if self._task:
            self._task.cancel()
            self._task = None
        self._attr_state = MediaPlayerState.BUFFERING
        self._attr_media_content_id = media_id
        self.async_write_ha_state()
        try:
            clip = await self._prepare(url)
            token = secrets.token_urlsafe(16)
            CLIPS.clear()
            CLIPS[token] = clip
            self._clip_url = f"{get_url(self.hass, allow_external=False)}/api/go2rtc_speaker/{token}.wav"
            await self._start(f"ffmpeg:{self._clip_url}#audio={self._codec}#input=file")
        except Exception:
            self._attr_state = MediaPlayerState.IDLE
            self.async_write_ha_state()
            raise
        self._attr_state = MediaPlayerState.PLAYING
        self.async_write_ha_state()
        self._task = self.hass.async_create_background_task(self._watch(), f"{DOMAIN} watch")

    async def _watch(self) -> None:
        try:
            await asyncio.sleep(2)
            for _ in range(self._max):
                try:
                    if not await self._clip_active():
                        break
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("go2rtc poll failed: %s", err)
                await asyncio.sleep(1)
            else:
                await self._post("")
        except asyncio.CancelledError:
            return
        self._attr_state = MediaPlayerState.IDLE
        self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        await self._post("")
        self._attr_state = MediaPlayerState.IDLE
        self.async_write_ha_state()
