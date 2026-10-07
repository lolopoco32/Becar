# Becar
Hybrid ALSA audio player for FLAC / FFmpeg
## Why this player exists

I built this player out of necessity: every GUI audio player I tried outputs to ALSA hardware only in `RW_INTERLEAVED` (read/write) access mode, most likely for compatibility with the widest range of audio devices, and none of them support `MMAP_INTERLEAVED`.

MMAP (memory mapping) avoids the intermediate buffer copies that RW mode makes on every write. Instead, the audio stream is written straight into the memory region shared with the sound card, USB DAC, or other audio device.

In practice, this means lower latency and, in my experience, better sound quality (tested with fast, planar-magnetic headphones).

## How it works

The player uses `aplay` (part of `alsa-utils`) as its playback backend, with FLAC and FFmpeg decoding other formats to PCM so that `aplay` can play them.

- **WAV:** played directly by `aplay`, since it's the only format it natively supports.
- **FLAC:** the FLAC decoder converts the file and pipes the PCM stream to `aplay`.
- **Everything else:** FFmpeg decodes the file and pipes the PCM stream to `aplay`.

Decoders are loaded into RAM only when needed, to keep memory usage low:

| Format | Processes loaded   |
|--------|--------------------|
| WAV    | `aplay`            |
| FLAC   | `flac` + `aplay`   |
| Other  | `ffmpeg` + `aplay` |

<img width="1110" height="897" alt="Screenshot_20261007_173849" src="https://github.com/user-attachments/assets/904b172d-010d-4d34-bf19-53bb1bb06720" />
