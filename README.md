# Crunchyroll-Downloader

A CLI downloader for Crunchyroll. Search for a series or paste a link, select the episodes you want, choose the dub, quality, and subtitles, and get everything muxed into a single MKV file.

No flags or complicated commands. Run the script and use the menu.

```text
LastPerson07

yt-dlp       /usr/local/bin/yt-dlp
ffmpeg       /usr/bin/ffmpeg
mp4decrypt   /usr/local/bin/mp4decrypt
wvd          .../LastPerson07/LastPerson07.wvd

Loaded 18 cookies from cr_cookies.txt
Logged in

1. Search a show
2. Paste a link
3. Quit
```

## Requirements

- Python 3.8+
- `yt-dlp`
- `ffmpeg`
- `mp4decrypt` (Bento4)
- Your own Widevine CDM

Install the Python dependencies:

```bash
pip install curl_cffi pywidevine
```

Make sure `yt-dlp`, `ffmpeg`, and `mp4decrypt` are available in your `PATH` or placed next to the script. Windows executables are supported.

- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [FFmpeg](https://ffmpeg.org/download.html)
- [Bento4](https://github.com/axiomatic-systems/Bento4)

## Widevine CDM

A `.wvd` file is required. It isn't included with the project.

Place your own legally obtained CDM file at:

```text
LastPerson07/LastPerson07.wvd
```

The script checks for this file at startup and exits if it can't find it.

## Login

The script uses the `etp_rt` cookie from your Crunchyroll session.

Export your cookies in Netscape format and save them as:

```text
LastPerson07/cookies.txt
```

It also checks for `cookies.txt` and `cr_cookies.txt` in the script's directory.

If no cookie file is found, you'll be prompted to enter the `etp_rt` value. The access token is cached in `.lp07_token.json`.

## Usage

```bash
python LastPerson07.py
```

Select an option from the menu:

1. Search for a series.
2. Paste a `/series/` or `/watch/` URL.
3. Quit.

Choose a season and enter an episode range:

- `1` — episode 1
- `1-5` — episodes 1 through 5
- `all` — all episodes in the season

For each episode, select the dub, video quality, and subtitles. Previous selections are used as defaults for subsequent episodes.

Files are saved under `Crunchyroll Files/<Series Name>/` with filenames in this format:

```text
Series.S01E02.Episode.Title.mkv
```

Poster artwork is embedded as cover art.

## Notes

- Audio and video are downloaded separately and muxed into an MKV using FFmpeg.
- FFmpeg uses `-ignore_editlist 1` to handle the audio edit list found in some Crunchyroll streams.
- Audio tracks are downloaded sequentially to avoid `TOO_MANY_ACTIVE_STREAMS` errors.
- Playback tokens are released after downloads and when the script is interrupted.
- Available quality depends on your subscription and the source. Some older titles are only available at 480p.

## -:-

This project is not affiliated with or endorsed by Crunchyroll. Accessing or decrypting protected streams may violate its terms of service or local laws. Use it only where you have the necessary rights and authorization.
