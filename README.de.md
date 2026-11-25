# go2rtc Speaker

🇬🇧 [English](README.md)

Macht den Lautsprecher einer Kamera oder Video-Türklingel (z. B. **Reolink Video Doorbell**) zu einem
**Mediaplayer** in Home Assistant. **TTS-Ansagen live**, MP3s oder beliebige Medien abspielen – ohne vorher
Schnellantworten in der Kamera anzulegen.

Genutzt wird der **Audio-Rückkanal (Zwei-Wege-Audio)** von [go2rtc](https://github.com/AlexxIT/go2rtc) –
derselbe Weg wie beim Gegensprechen über die Kamera.

## So funktioniert es

```
tts.speak / Medien ──► go2rtc Speaker ──ffmpeg──► 8-kHz-WAV (von HA bereitgestellt)
                                                    │
                          go2rtc  ◄── POST /api/streams?dst=<stream>&src=ffmpeg:<clip>#audio=pcmu#input=file
                            │
                            └── RTSP-Rückkanal ──► Lautsprecher der Kamera 🔊
```

1. Home Assistant wandelt den Ton mit ffmpeg um (Mono, 8 kHz, wahlweise Stille davor/danach, Lautstärke) und
   stellt ihn unter einer zufälligen Einmal-Adresse bereit. (go2rtc lehnt Quellen mit Leerzeichen ab, deshalb
   passieren die Filter in HA.)
2. go2rtc spielt die Datei in den Rückkanal des Streams.
3. Lehnt die Kamera den Rückkanal ab (Reolink antwortet manchmal `wrong response on DESCRIBE`), merkt der
   Mediaplayer das und versucht es automatisch erneut.
4. Ist die Datei durch, geht der Mediaplayer zurück auf „bereit“.

## Voraussetzungen

- Ein **go2rtc**, das Home Assistant erreicht – z. B. das go2rtc in **Frigate** (Port 1984) oder das
  go2rtc-Add-on.
- Ein go2rtc-Stream der Kamera **mit Rückkanal**. Bei Reolink die **RTSP**-Adresse der Kamera als Quelle
  eintragen (siehe [examples/go2rtc.yaml](examples/go2rtc.yaml)).
  Prüfen in der go2rtc-Oberfläche (`http://<go2rtc>:1984` → *info* beim Stream, während er läuft): Bei der
  RTSP-Verbindung muss `audio, sendonly, PCMU/8000` stehen.
- go2rtc muss die interne Adresse von Home Assistant erreichen (Einstellungen → System → Netzwerk).

## Installation

1. HACS → ⋮ → *Benutzerdefinierte Repositories* → `https://github.com/Aggro600/go2rtc-Speaker`, Typ *Integration*.
2. **go2rtc Speaker** installieren, Home Assistant neu starten.
3. Mediaplayer per YAML anlegen (z. B. als Paket) und noch einmal neu starten:

```yaml
media_player:
  - platform: go2rtc_speaker
    name: Haustür Klingel Lautsprecher
    unique_id: haustur_klingel_lautsprecher
    url: http://192.168.1.10:1984
    stream: haustur
    codec: pcmu
```

| Option | Standard | Bedeutung |
|---|---|---|
| `name` | – | Name der Entität |
| `url` | – | go2rtc-API, z. B. `http://192.168.1.10:1984` |
| `stream` | – | go2rtc-Stream mit Zwei-Wege-Audio |
| `codec` | `pcmu` | Codec des Rückkanals (`pcmu`, `pcma`, `pcm`, `opus`, `aac`) |
| `unique_id` | `go2rtc_speaker_<stream>` | Damit die Entität in der Oberfläche bearbeitbar ist |
| `lead_in_ms` | `0` | Stille vor dem Ton (falls die Kamera den Anfang verschluckt) |
| `tail_s` | `8` | Stille nach dem Ton (Reolink verwirft gepufferten Ton beim Trennen – ohne fehlen die letzten Wörter) |
| `retries` | `5` | Versuche, wenn die Kamera den Rückkanal ablehnt |
| `max_seconds` | `300` | Sicherheitsgrenze pro Wiedergabe |

## Benutzung

```yaml
action: tts.speak
target:
  entity_id: tts.google_cloud
data:
  media_player_entity_id: media_player.haustur_klingel_lautsprecher
  message: "Bitte das Paket vor die Tür legen."
```

MP3s: Medienbrowser der Entität oder `media_player.play_media` mit Medienquelle / URL.
Skript als Vorlage mit Textfeld:

```yaml
kamera_haustur_tts:
  alias: Kamera Haustür TTS
  icon: mdi:account-voice
  mode: queued
  fields:
    nachricht:
      name: Nachricht
      required: true
      example: Bitte das Paket vor die Tür legen.
      selector:
        text:
          multiline: true
  sequence:
    - action: tts.speak
      target:
        entity_id: tts.google_cloud
      data:
        media_player_entity_id: media_player.haustur_klingel_lautsprecher
        message: "{{ nachricht }}"
```

Der Mediaplayer zeigt „puffert“ beim Verbinden, „spielt“ während der Wiedergabe (inkl. Stille danach) und
danach „bereit“. Die Lautstärke (0–100 %) gilt ab der nächsten Wiedergabe; „Stopp“ beendet sofort.

## Tipps (Reolink Video Doorbell)

- **Nichts zu hören, kein Fehler:** im go2rtc-Log nach `wrong response on DESCRIBE` schauen – die Klingel hat
  die Verbindung abgelehnt. Die Wiederholungen fangen das ab; bei Bedarf `retries` erhöhen.
- **Ende abgeschnitten:** `tail_s` erhöhen.
- **Anfang abgeschnitten:** `lead_in_ms` setzen (z. B. `2000`).
- **TTS in falscher Sprache:** Sprache in der TTS-Integration einstellen (z. B. Google Cloud → Optionen →
  `de-DE`) oder `language` bei `tts.speak` mitgeben.
- Während die Klingel Ton ausgibt, schaltet sie ihr Mikrofon stumm – das ist normal.

## Lizenz

MIT
