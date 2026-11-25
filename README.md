# go2rtc Speaker

[![Validate](https://github.com/Aggro600/go2rtc-Speaker/actions/workflows/validate.yml/badge.svg)](https://github.com/Aggro600/go2rtc-Speaker/actions/workflows/validate.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)

🇩🇪 [Deutsche Anleitung](README.de.md)

Turns the speaker of a camera or video doorbell (e.g. **Reolink Video Doorbell**) into a Home Assistant
**media player**. Play **TTS announcements live**, MP3s or any media – no need to configure quick replies
on the camera first.

It uses the **two-way audio (backchannel)** of [go2rtc](https://github.com/AlexxIT/go2rtc) – the same path
that is used for talking through the camera.

## How it works

```
tts.speak / media ──► go2rtc Speaker ──ffmpeg──► 8 kHz WAV (served by HA)
                                                   │
                         go2rtc  ◄── POST /api/streams?dst=<stream>&src=ffmpeg:<clip>#audio=pcmu#input=file
                           │
                           └── RTSP backchannel ──► camera speaker 🔊
```

1. Home Assistant converts the audio with ffmpeg (mono, 8 kHz, optional silence before/after, volume) and
   serves it under a random one-off URL. (go2rtc refuses sources with spaces, so filters are applied in HA.)
2. go2rtc plays the clip into the stream's backchannel.
3. If the camera rejects the backchannel (Reolink sometimes answers `wrong response on DESCRIBE`), the player
   notices and retries automatically.
4. When the clip has finished, the player goes back to `idle`.

## Requirements

- A **go2rtc** instance reachable from Home Assistant – e.g. the go2rtc inside **Frigate** (port 1984) or the
  go2rtc add-on.
- A go2rtc stream of the camera **with backchannel**. For Reolink, add the camera's **RTSP** URL as a source
  (see [examples/go2rtc.yaml](examples/go2rtc.yaml)).
  Check in the go2rtc web UI (`http://<go2rtc>:1984` → *info* of the stream while it is in use): the RTSP
  connection must list `audio, sendonly, PCMU/8000`.
- go2rtc must be able to reach Home Assistant's internal URL (Settings → System → Network).

## Installation

1. HACS → ⋮ → *Custom repositories* → `https://github.com/Aggro600/go2rtc-Speaker`, type *Integration*.
2. Install **go2rtc Speaker**, restart Home Assistant.
3. Add the media player in YAML (e.g. a package) and restart again:

```yaml
media_player:
  - platform: go2rtc_speaker
    name: Front door speaker
    unique_id: front_door_speaker
    url: http://192.168.1.10:1984
    stream: front_door
    codec: pcmu
```

| Option | Default | Meaning |
|---|---|---|
| `name` | – | Entity name |
| `url` | – | go2rtc API, e.g. `http://192.168.1.10:1984` |
| `stream` | – | go2rtc stream with two-way audio |
| `codec` | `pcmu` | Codec of the backchannel (`pcmu`, `pcma`, `pcm`, `opus`, `aac`) |
| `unique_id` | `go2rtc_speaker_<stream>` | Makes the entity editable in the UI |
| `lead_in_ms` | `0` | Silence before the audio (if the camera cuts off the beginning) |
| `tail_s` | `8` | Silence after the audio (Reolink drops buffered audio when the connection ends – without it the last words are missing) |
| `retries` | `5` | Attempts when the camera rejects the backchannel |
| `max_seconds` | `300` | Safety limit for one playback |

## Usage

```yaml
action: tts.speak
target:
  entity_id: tts.google_cloud
data:
  media_player_entity_id: media_player.front_door_speaker
  message: "Please leave the parcel in front of the door."
```

MP3s: media browser of the entity, or `media_player.play_media` with a media source / URL.
A reusable script with a text field: [examples/script.yaml](examples/script.yaml).

The player shows `buffering` while connecting, `playing` while the clip runs (incl. tail silence) and `idle`
afterwards. Volume (0–100 %) applies from the next playback; `stop` ends playback.

## Tips (Reolink Video Doorbell)

- **Nothing to hear, no error:** look in the go2rtc log for `wrong response on DESCRIBE` – the doorbell rejected
  the connection. The retries handle this; increase `retries` if it happens often.
- **End cut off:** increase `tail_s`.
- **Beginning cut off:** set `lead_in_ms` (e.g. `2000`).
- **TTS in the wrong language:** set the language of the TTS integration (e.g. Google Cloud → options →
  `de-DE`) or pass `language` in `tts.speak`.
- While the doorbell plays audio it mutes its microphone – that is normal.

## License

MIT
