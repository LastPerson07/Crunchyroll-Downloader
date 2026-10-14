#!/usr/bin/env python3
#
#   LastPerson07 - crunchyroll downloader
#
#   install : pip install curl_cffi pywidevine
#   on PATH : yt-dlp, ffmpeg, mp4decrypt
#
#   -------------------------------------------------------------------
#   YOU NEED YOUR OWN CDM / .wvd DEVICE FILE TO RUN THIS.
#
#   I am not shipping one and I will not give you one. Nothing here can
#   decrypt a single byte without it. Go dump your own from a device
#   you own, then drop it at:
#
#       LastPerson07/LastPerson07.wvd
#   -------------------------------------------------------------------
#
#   Run it in a terminal. No flags, it asks you everything.
#

import os, re, sys, json, time, uuid, shutil, signal, base64, subprocess
import xml.etree.ElementTree as ET
from contextlib import suppress

try:
    from curl_cffi import requests
    from pywidevine.cdm import Cdm
    from pywidevine.device import Device
    from pywidevine.pssh import PSSH
except ImportError as e:
    sys.exit("missing dependency: %s\nrun:  pip install curl_cffi pywidevine" % e.name)

# ------------------------------------------------------------------- config

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)

WVD = os.path.join(HERE, "LastPerson07", "LastPerson07.wvd")
TOKEN_CACHE = os.path.join(HERE, ".lp07_token.json")

WVD_NOTICE = """
  ---------------------------------------------------------------
   No .wvd file found.

   This needs a Widevine CDM device file or it can't decrypt
   anything at all. I'm not shipping one and I won't give you one
   - go dump your own from a device you own.

   Put it here, then run me again:

       %s

  ---------------------------------------------------------------
""" % WVD

COOKIE_FILES = [os.path.join(HERE, "LastPerson07", "cookies.txt"), os.path.join(HERE, "cookies.txt"),
                os.path.join(HERE, "cr_cookies.txt"), os.path.join(PARENT, "cookies.txt")]

# flip these if you want different behaviour
DOWNLOAD_DIR = os.path.join(HERE, "Crunchyroll Files")
SKIP_EXISTING = True          # leave files you already have alone
SAVE_ARTWORK = True           # poster / backdrop / logo next to the episodes

CR = "https://www.crunchyroll.com"
TOKEN_URL = CR + "/auth/v1/token"
SEARCH_URL = CR + "/content/v2/discover/search"
CMS = CR + "/content/v2/cms"
PLAY = "https://cr-play-service.prd.crunchyrollsvc.com/v1"
LICENSE_URL = "https://cr-license-proxy.prd.crunchyrollsvc.com/v1/license/widevine"
IMAGE_HOST = "https://imgsrv.crunchyroll.com/cdn-cgi/image"
BASIC_AUTH = "Basic bm9haWhkZXZtXzZpeWcwYThsMHE6"    # their public app credentials
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

LANG = {"ja-JP": "Japanese", "en-US": "English", "es-LA": "Spanish (LatAm)",
        "es-419": "Spanish (LatAm)", "es-ES": "Spanish (Spain)", "pt-BR": "Portuguese (BR)",
        "pt-PT": "Portuguese (PT)", "fr-FR": "French", "de-DE": "German", "ru-RU": "Russian",
        "ar-SA": "Arabic", "it-IT": "Italian", "zh-CN": "Chinese (Simp.)",
        "zh-TW": "Chinese (Trad.)", "zh-HK": "Chinese (HK)", "hi-IN": "Hindi",
        "ko-KR": "Korean", "tr-TR": "Turkish", "pl-PL": "Polish", "nl-NL": "Dutch",
        "vi-VN": "Vietnamese", "id-ID": "Indonesian", "th-TH": "Thai",
        "ms-MY": "Malay", "ta-IN": "Tamil", "te-IN": "Telugu"}

YTDLP = FFMPEG = MP4DECRYPT = None
scratch = []
picked = {"quality": "1", "subs": "1"}    # your last answers
live_token = live_ep = None

def lang_name(code):
    return LANG.get(code, code)

# ----------------------------------------------------------------- tiny ui

def table(title, head, rows):
    if not rows:
        return
    width = [max(len(str(c)) for c in col) for col in zip(*([head] + rows))]
    line = "  ".join("%-*s" % (w, h) for w, h in zip(width, head))
    print("\n  " + title + "\n  " + line + "\n  " + "-" * len(line))
    for row in rows:
        print("  " + "  ".join("%-*s" % (w, c) for w, c in zip(width, row)).rstrip())

def ask(prompt, default=None):
    hint = " [%s]" % default if default not in (None, "") else ""
    try:
        answer = input("%s%s: " % (prompt, hint)).strip()
    except EOFError:
        return default if default is not None else ""
    return answer or (default if default is not None else "")

def ask_yes(prompt, default=True):
    return ask("%s (y/n)" % prompt, "y" if default else "n").lower().startswith("y")

# ------------------------------------------------------------- startup bits

def wipe():
    for path in scratch:
        with suppress(OSError): os.remove(path)
    del scratch[:]

def bail_out(signum=None, frame=None):
    # hand the stream slot back, otherwise it counts against the account and
    # the next episode dies with TOO_MANY_ACTIVE_STREAMS
    if live_token and live_ep:
        with suppress(Exception):
            requests.Session(impersonate="chrome124").delete(
                "%s/token/%s/%s" % (PLAY, live_ep, live_token),
                headers={"authorization": "Bearer " + live_token}, timeout=10)
    print("\nstopped, cleaning up...")
    wipe()
    sys.exit(1)

signal.signal(signal.SIGINT, bail_out)

def find_tool(name):
    path = shutil.which(name)
    if path:
        return path
    for candidate in (os.path.join(HERE, name), os.path.join(HERE, name + ".exe")):
        if os.path.isfile(candidate):     # dropped next to the script
            return candidate
    return None

def check_tools():
    global YTDLP, FFMPEG, MP4DECRYPT
    YTDLP, FFMPEG, MP4DECRYPT = (find_tool("yt-dlp"), find_tool("ffmpeg"),
                                 find_tool("mp4decrypt"))
    for name, path in (("yt-dlp", YTDLP), ("ffmpeg", FFMPEG),
                       ("mp4decrypt", MP4DECRYPT), ("wvd", WVD if os.path.isfile(WVD) else None)):
        print("  %-12s %s" % (name, path or "MISSING"))
    return all([YTDLP, FFMPEG, MP4DECRYPT, os.path.isfile(WVD)])

# ---------------------------------------------------------- cookies / login

def read_cookies(path):
    """Parse a netscape cookie jar. Cookie-Editor exports work fine."""
    jar = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return jar
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.lstrip().startswith("#"):
            continue
        bits = line.rstrip("\r").split("\t")
        if len(bits) < 7:
            bits = re.split(r"\s{2,}|\t", line)     # some exporters use spaces
        if len(bits) < 7:
            continue
        domain, _flag, cpath, _sec, _exp, name, value = bits[:7]
        jar[name] = {"value": value, "domain": domain.strip(), "path": cpath.strip() or "/"}
    return jar

def login(force=False):
    """Returns (session, token). Reuses the cached token when it's still fresh."""
    session = requests.Session(impersonate="chrome124")
    session.headers.update({"accept": "application/json, text/plain, */*",
                            "accept-language": "en-US,en;q=0.9", "origin": CR,
                            "referer": CR + "/", "user-agent": UA})
    try:
        with open(TOKEN_CACHE) as fh: cached = json.load(fh)
    except Exception: cached = {}
    if not force and time.time() < cached.get("expiry", 0) - 60:
        return session, cached["access_token"]
    jar = {}
    path = next((p for p in COOKIE_FILES if os.path.isfile(p)), None)
    if path:
        jar = read_cookies(path)
        for name, c in jar.items():
            with suppress(Exception):
                session.cookies.set(name, c["value"], domain=c["domain"], path=c["path"])
        print("  loaded %d cookies from %s" % (len(jar), os.path.basename(path)))
    etp_rt = jar.get("etp_rt", {}).get("value")
    if not etp_rt:
        print("\n  I need the etp_rt cookie.")
        print("  log into crunchyroll.com, F12 -> Application -> Cookies, copy etp_rt.")
        print("  (or save a cookie export as LastPerson07/cookies.txt and I won't ask)")
        etp_rt = ask("paste etp_rt")
        if not etp_rt:
            sys.exit("nothing pasted, can't log in")
    print("  asking for a token...")
    session.cookies.set("etp_rt", etp_rt, domain=".crunchyroll.com")
    r = session.post(TOKEN_URL, timeout=30, data={
        "device_id": str(uuid.uuid4()), "device_type": "Chrome on Windows",
        "grant_type": "etp_rt_cookie"}, headers={
        "authorization": BASIC_AUTH, "content-type": "application/x-www-form-urlencoded",
        "etp-anonymous-id": str(uuid.uuid4())})
    if r.status_code != 200:
        raise RuntimeError("token request failed (%s): %s" % (r.status_code, r.text[:200]))
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError("no access_token in response: %s" % data)
    with suppress(OSError):
        with open(TOKEN_CACHE, "w") as fh: json.dump(data, fh)
    print("  logged in")
    return session, data["access_token"]

# ---------------------------------------------------------------------- api

def auth_h(token, extra=None):
    h = {"authorization": "Bearer " + token}
    h.update(extra or {})
    return h

def mpd_h(token):
    return auth_h(token, {"origin": "https://static.crunchyroll.com",
                          "referer": "https://static.crunchyroll.com/", "user-agent": UA})

def search(session, token, query):
    r = session.get(SEARCH_URL, headers=auth_h(token), timeout=30,
                    params={"q": query, "n": 10, "type": "series", "locale": "en-US"})
    r.raise_for_status()
    return [{"id": (p := e.get("panel", e)).get("id", ""), "title": p.get("title", ""),
             "images": p.get("images", {})}
            for g in r.json().get("data", []) for e in g.get("items", [])]

def get_object(session, token, cid):
    r = session.get("%s/objects/%s" % (CMS, cid), params={"locale": "en-US"},
                    headers=auth_h(token), timeout=30)
    r.raise_for_status()
    return (r.json().get("data") or [{}])[0]

def seasons_of(session, token, series_id):
    # some shows only turn up depending on the audio preference, so try a few
    for params in ({"preferred_audio_language": "ja-JP", "locale": "en-US"},
                   {"locale": "en-US"},
                   {"preferred_audio_language": "en-US", "locale": "en-US"}, {}):
        r = session.get("%s/series/%s/seasons" % (CMS, series_id), params=params,
                        headers=auth_h(token), timeout=30)
        r.raise_for_status()
        data = r.json().get("data", [])
        if data:
            return [{"id": s["id"], "title": s["title"], "number": s["season_number"],
                     "count": s["number_of_episodes"]} for s in data]
    return []

def episodes_of(session, token, season_id):
    r = session.get("%s/seasons/%s/episodes" % (CMS, season_id), timeout=30,
                    params={"preferred_audio_language": "ja-JP", "locale": "en-US"},
                    headers=auth_h(token))
    r.raise_for_status()
    return [{"id": e["id"], "title": e.get("title") or "Episode %s" % e.get("episode_number"),
             "series": e.get("series_title", ""), "series_id": e.get("series_id", ""),
             "season": e.get("season_number"), "episode": e.get("episode_number")}
            for e in r.json().get("data", [])]

def parse_link(session, token, url):
    m = re.match(r"https?://(?:www\.)?crunchyroll\.com/(?:[a-z-]{2,5}/)?(series|watch)/([A-Z0-9]+)", url)
    if not m:
        raise ValueError("that isn't a crunchyroll link")
    kind, cid = m.groups()
    if kind == "series":
        return cid, None, None, None
    meta = get_object(session, token, cid).get("episode_metadata", {})
    return meta.get("series_id"), meta.get("season_number"), meta.get("episode_number"), cid

def request_stream(session, token, cid):
    r = session.get("%s/%s/web/chrome/play" % (PLAY, cid), timeout=30,
                    headers=auth_h(token, {"x-cr-tab-id": str(uuid.uuid4()),
                                           "x-cr-device-id": str(uuid.uuid4()),
                                           "x-cr-device-type": "com.crunchyroll.windows.desktop",
                                           "origin": "https://static.crunchyroll.com",
                                           "referer": "https://static.crunchyroll.com/"}))
    if r.status_code in (401, 403):
        raise PermissionError("stream refused (%s), the cookie is probably stale" % r.status_code)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(str(data["error"]))
    return {"mpd": data["url"], "token": data["token"],
            "dubs": data.get("versions", []), "subs": data.get("subtitles", {}),
            "license": {"authorization": "Bearer " + token,
                        "content-type": "application/octet-stream",
                        "x-cr-content-id": cid, "x-cr-video-token": data["token"],
                        "origin": "https://static.crunchyroll.com",
                        "referer": "https://static.crunchyroll.com/"}}

def release_stream(session, token, cid, play_token):
    with suppress(Exception):
        session.delete("%s/token/%s/%s" % (PLAY, cid, play_token),
                       headers=auth_h(token), timeout=10)

# ---------------------------------------------------------------------- mpd

def read_mpd(session, mpd_url, headers):
    """One request gets every resolution, the audio tracks and the pssh box."""
    r = session.get(mpd_url, headers=headers, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.text)

    videos, audios = {}, {}
    for aset in root.findall(".//{*}AdaptationSet"):
        for rep in aset.findall("{*}Representation"):
            kind = rep.get("mimeType", aset.get("mimeType", ""))
            bw = int(rep.get("bandwidth", 0) or 0)
            if "video" in kind:
                height = int(rep.get("height", 0) or 0)
                if height not in videos or bw > videos[height]["bandwidth"]:
                    videos[height] = {"height": height, "bandwidth": bw,
                                      "codec": rep.get("codecs", "")}
            elif "audio" in kind:
                lang = aset.get("lang", "und")
                if lang not in audios or bw > audios[lang]["bandwidth"]:
                    audios[lang] = {"lang": lang, "bandwidth": bw}
    if not videos:
        raise RuntimeError("no video tracks in the mpd")
    # the pssh shows up twice in some manifests, the short one is the real
    # content box, the long one is the manifest level one
    boxes = [b.text.strip() for b in root.findall(".//{*}pssh") if b.text and b.text.strip()]
    return (sorted(videos.values(), key=lambda v: v["height"], reverse=True),
            list(audios.values()), min(boxes, key=len) if boxes else None)

# ----------------------------------------------------------------- widevine

def get_keys(pssh, headers, session):
    """LastPerson07.wvd builds the challenge, we keep the content keys it hands back."""
    if not os.path.isfile(WVD):
        raise RuntimeError("can't find %s" % WVD)
    cdm = Cdm.from_device(Device.load(WVD))
    sid = cdm.open()
    try:
        r = session.post(LICENSE_URL, timeout=30, headers=headers,
                         data=cdm.get_license_challenge(sid, PSSH(pssh)))
        if r.status_code != 200:
            raise RuntimeError("license http %s: %s"
                               % (r.status_code, r.text[:150].replace("\n", " ")))
        blob = r.content
        if "json" in r.headers.get("content-type", "") or blob[:1] in (b"{", b"["):
            with suppress(Exception):
                blob = base64.b64decode(r.json()["license"]) if r.json().get("license") else blob
        if len(blob) < 50:
            raise RuntimeError("license came back empty (%d bytes)" % len(blob))
        cdm.parse_license(sid, blob)
        keys = ["%s:%s" % (k.kid.hex, k.key.hex())
                for k in cdm.get_keys(sid) if k.type != "SIGNING"]
        if not keys:
            raise RuntimeError("no content keys in the license")
        return keys
    finally:
        with suppress(Exception): cdm.close(sid)

# ------------------------------------------------------------------ prompts

def pick_quality(videos):
    if len(videos) == 1:
        print("  only %dp available" % videos[0]["height"])
        return videos[0]["height"]
    table("quality", ["#", "resolution", "codec", "bitrate"],
          [[str(i), "%dp" % v["height"], v["codec"][:14], "%.2f Mbps" % (v["bandwidth"] / 1e6)]
           for i, v in enumerate(videos, 1)])
    answer = ask("pick one (# or 1080p)", picked["quality"]).lower()
    picked["quality"] = answer
    m = re.match(r"(\d+)p?$", answer)
    if m and int(m.group(1)) <= len(videos):
        return videos[int(m.group(1)) - 1]["height"]
    if m:
        for v in videos:
            if v["height"] == int(m.group(1)):
                return v["height"]
    print("  didn't get that, going with the best")
    return videos[0]["height"]

def pick_dub(dubs, current):
    if not dubs:
        print("  no other dubs")
        return current
    default, rows = "1", []
    for i, v in enumerate(dubs, 1):
        code = v.get("audio_locale", "?")
        here = v.get("guid", "") == current
        if here:
            default = str(i)
        rows.append([str(i), lang_name(code), code, "current" if here else ""])
    table("audio", ["#", "language", "code", ""], rows)
    answer = ask("which audio", default)
    if answer.isdigit() and 0 <= int(answer) - 1 < len(dubs):
        return dubs[int(answer) - 1].get("guid", current)
    return current

def pick_subs(subs):
    if not subs:
        print("  no subtitles on this one")
        return {}
    items = list(subs.items())
    table("subtitles", ["#", "language", "code", "fmt"],
          [[str(i), lang_name(info.get("language", key)), info.get("language", key),
            str(info.get("format", "vtt")).upper()] for i, (key, info) in enumerate(items, 1)])
    answer = ask("which subs (1,2 / all / none)", picked["subs"]).lower()
    picked["subs"] = answer
    if answer in ("none", "no", "n"):
        return {}
    if answer in ("all", "a"):
        return dict(items)
    chosen = {items[int(b) - 1][0]: items[int(b) - 1][1] for b in answer.split(",")
              if b.strip().isdigit() and 0 <= int(b) - 1 < len(items)}
    return chosen or {items[0][0]: items[0][1]}

def pick_episodes(episodes):
    numbers = sorted(e["episode"] for e in episodes if e["episode"] is not None)
    top = numbers[-1] if numbers else len(episodes)
    answer = ask("which episodes (1 / 1-5 / all / b)", "1").lower()
    if answer in ("b", "back", ""):
        return []
    if answer == "all":
        return list(range(1, top + 1))
    if "-" in answer:
        left, right = answer.split("-", 1)
        with suppress(ValueError): return list(range(int(left), int(right) + 1))
    try:
        return [int(answer)]
    except ValueError:
        print("  that's not an episode number")
        return []

# --------------------------------------------------------------- media work

PROGRESS = re.compile(r"\[download\]\s+(\d+\.?\d*)%\s+of\s+~?([\d.]+\s*\S+)", re.I)

def fetch_media(url, dest, fmt, headers, label):
    cmd = [YTDLP, "--allow-unplayable-formats", "-f", fmt, "--no-check-certificate",
           "--hls-use-mpegts", "--no-part", "--newline", "--progress", "-o", dest]
    for k, v in headers.items():
        cmd += ["--add-header", "%s:%s" % (k, v)]
    cmd.append(url)
    print("  %s" % label)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, errors="replace")
    for line in iter(proc.stdout.readline, ""):
        hit = PROGRESS.search(line)
        if hit:
            sys.stdout.write("\r    %5.1f%%  of %s        " % (float(hit.group(1)), hit.group(2)))
            sys.stdout.flush()
    proc.wait()
    print("")
    # yt-dlp reports success on a 0 byte file, so check it ourselves
    if proc.returncode or not os.path.exists(dest) or os.path.getsize(dest) < 1024:
        raise RuntimeError("download failed: %s" % label)

def decrypt_file(src, dest, keys):
    cmd = [MP4DECRYPT]
    for k in keys:
        cmd += ["--key", k]
    cmd += [src, dest]
    if subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) \
            or not os.path.isfile(dest) or os.path.getsize(dest) < 1000:
        raise RuntimeError("mp4decrypt choked on %s" % os.path.basename(src))
    with suppress(OSError): os.remove(src)

def remux(video, audios, subs, a_langs, s_langs, dest, cover=None, tags=None):
    # crunchyroll's audio carries an mp4 edit list that pushes it ~8.8s down the
    # timeline. if ffmpeg honours it the video ends up late and the whole thing
    # is out of sync, so tell it to ignore edit lists.
    cmd = [FFMPEG, "-y", "-ignore_editlist", "1", "-i", video]
    cmd += sum([["-ignore_editlist", "1", "-i", a] for a in audios], [])
    for s in subs:
        cmd += ["-i", s]
    cmd += ["-map", "0:v:0"]
    cmd += sum([["-map", "%d:a:0" % (i + 1)] for i in range(len(audios))], [])
    cmd += sum([["-map", "%d:s:0" % (len(audios) + 1 + i)] for i in range(len(subs))], [])
    cmd += sum([["-metadata:s:a:%d" % i, "language=" + l] for i, l in enumerate(a_langs)], [])
    cmd += sum([["-metadata:s:s:%d" % i, "language=" + l] for i, l in enumerate(s_langs)], [])
    for k, v in (tags or {}).items():
        if v:
            cmd += ["-metadata", "%s=%s" % (k, v)]
    if cover and os.path.exists(cover):
        cmd += ["-attach", cover, "-metadata:s:t:0", "mimetype=image/jpeg",
                "-metadata:s:t:0", "filename=cover.jpg"]
    cmd += ["-c", "copy", dest]
    if subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):
        raise RuntimeError("ffmpeg failed while muxing")

def grab_art(session, series_id, images, folder):
    """Poster / backdrop / logo, dumped next to the episodes."""
    os.makedirs(folder, exist_ok=True)
    urls = {
        "backdrop": "%s/fit=cover,format=auto,quality=94,width=1920/keyart/%s-backdrop_wide"
                    % (IMAGE_HOST, series_id),
        "logo": "%s/fit=contain,format=auto,quality=94,width=800/keyart/%s-title_logo-en-us"
                % (IMAGE_HOST, series_id),
        "poster": "%s/fit=contain,format=auto,quality=94,width=900,height=1350/keyart/%s-poster_tall"
                  % (IMAGE_HOST, series_id)}
    for key in ("poster_tall", "poster_wide", "thumbnail"):
        value = (images or {}).get(key, [])
        for item in (value if isinstance(value, list) else [value]):
            if isinstance(item, dict) and item.get("source"):
                urls["poster"] = item["source"]
                break
    poster = None
    for label, url in urls.items():
        target = os.path.join(folder, label + ".jpg")
        if os.path.exists(target):
            poster = target if label == "poster" else poster
            continue
        with suppress(Exception):
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                with open(target, "wb") as fh: fh.write(r.content)
                poster = target if label == "poster" else poster
                print("    saved %s.jpg" % label)
    return poster

def clean_name(text):
    return re.sub(r"\s+", " ", re.sub(r'[<>:"/\\|?*]', "", text)).strip()

# -------------------------------------------------------------- one episode

def grab_episode(session, token, episode, images=None):
    global live_token, live_ep
    season_no, ep_no = episode.get("season") or 0, episode.get("episode") or 0
    series_folder = clean_name(episode["series"])
    stem = re.sub(r"\s+", ".", clean_name("%s.S%02dE%02d.%s"
                 % (episode["series"], season_no, ep_no, episode["title"])))
    out_dir = os.path.join(DOWNLOAD_DIR, series_folder)
    os.makedirs(out_dir, exist_ok=True)
    final = os.path.join(out_dir, stem + ".mkv")
    if SKIP_EXISTING and os.path.exists(final) and os.path.getsize(final) > 10 * 1024 * 1024:
        print("\n  already have %s, skipping" % os.path.basename(final))
        return
    print("\n  %s  S%02dE%02d  %s" % (episode["series"], season_no, ep_no, episode["title"]))
    cover = grab_art(session, episode["series_id"], images or {}, out_dir) \
        if SAVE_ARTWORK and episode.get("series_id") else None

    print("  getting the play token...")
    cid = episode["id"]
    stream = request_stream(session, token, cid)
    active = cid
    if stream["dubs"]:
        chosen = pick_dub(stream["dubs"], cid)
        if chosen != cid:
            release_stream(session, token, cid, stream["token"])
            active = chosen
            print("  switching to that dub...")
            stream = request_stream(session, token, active)
    live_token, live_ep = stream["token"], active

    videos, audios, pssh = read_mpd(session, stream["mpd"], mpd_h(token))
    if not pssh:
        raise RuntimeError("no pssh box in the manifest, can't ask for keys")
    print("  %d video track(s), %d audio track(s), %d subtitle(s)"
          % (len(videos), len(audios), len(stream["subs"])))
    height = pick_quality(videos)
    chosen_subs = pick_subs(stream["subs"])

    print("  asking widevine for the keys...")
    keys = get_keys(pssh, stream["license"], session)
    print("  got %d key(s)" % len(keys))

    stamp = str(int(time.time()))
    enc_v = os.path.join(out_dir, "enc_v_%s.mp4" % stamp)
    dec_v = os.path.join(out_dir, "dec_v_%s.mp4" % stamp)
    scratch.extend([enc_v, dec_v])
    enc_a, dec_a, a_langs = [], [], []
    for track in audios:
        enc_a.append(os.path.join(out_dir, "enc_a_%s_%s.m4a" % (track["lang"], stamp)))
        dec_a.append(os.path.join(out_dir, "dec_a_%s_%s.m4a" % (track["lang"], stamp)))
        a_langs.append(track["lang"])
        scratch.extend([enc_a[-1], dec_a[-1]])

    print("\n  downloading")
    fetch_media(stream["mpd"], enc_v,
                "bestvideo[height=%d]/bestvideo[height<=%d]/bestvideo" % (height, height),
                mpd_h(token), "video %dp" % height)
    # one at a time on purpose, parallel pulls trip their stream limiter
    for track, dest in zip(audios, enc_a):
        lang = track["lang"]
        fetch_media(stream["mpd"], dest,
                    "bestaudio[language=%s]/bestaudio[language=%s]/bestaudio"
                    % (lang, lang.split("-")[0]), mpd_h(token), "audio %s" % lang_name(lang))
    release_stream(session, token, active, stream["token"])
    live_token = live_ep = None

    sub_files, sub_langs = [], []
    for key, info in chosen_subs.items():
        lang = str(info.get("language", key)).split("-")[0]
        path = os.path.join(out_dir, "sub_%s_%s.vtt" % (lang, stamp))
        try:
            r = session.get(info.get("url", ""), timeout=30)
            r.raise_for_status()
            with open(path, "wb") as fh: fh.write(r.content)
            sub_files.append(path)
            sub_langs.append(lang)
            scratch.append(path)
        except Exception as e:
            print("    subtitle %s failed: %s" % (lang, e))

    print("  decrypting...")
    decrypt_file(enc_v, dec_v, keys)
    for enc, dec in zip(enc_a, dec_a):
        decrypt_file(enc, dec, keys)

    print("  muxing...")
    remux(dec_v, dec_a, sub_files, a_langs, sub_langs, final, cover,
          {"title": episode["title"], "show": episode["series"],
           "season_number": str(season_no), "episode_id": str(ep_no),
           "encoded_by": "LastPerson07"})
    print("\n  done -> %s (%.1f MB)"
          % (os.path.basename(final), os.path.getsize(final) / 1024.0 / 1024.0))
    wipe()

def run_episode(session, token, episode, images=None):
    """grab_episode with the error handling, used by both flows."""
    try:
        grab_episode(session, token, episode, images)
    except PermissionError:
        wipe()
        raise                           # let main() log in again
    except Exception as e:
        print("  episode failed: %s" % e)
        wipe()

# -------------------------------------------------------------------- flows

def flow_search(session, token):
    query = ask("\nwhat do you want to search for")
    if not query:
        return
    results = search(session, token, query)
    if not results:
        print("  nothing came back")
        return
    table('results for "%s"' % query, ["#", "title", "id"],
          [[str(i), r["title"], r["id"]] for i, r in enumerate(results, 1)])
    answer = ask("which one (number, b to go back)", "b").lower()
    if answer in ("b", "") or not answer.isdigit() or not 0 < int(answer) <= len(results):
        return
    chosen = results[int(answer) - 1]
    flow_series(session, token, chosen["id"], chosen.get("images", {}))

def flow_series(session, token, series_id, images=None):
    seasons = seasons_of(session, token, series_id)
    if not seasons:
        print("  no seasons for that one")
        return
    table("seasons", ["#", "title", "episodes"],
          [[str(s["number"]), s["title"], str(s["count"])] for s in seasons])
    answer = ask("which season (number, b to go back)", "b").lower()
    season = next((s for s in seasons if str(s["number"]) == answer), None)
    if season is None:
        print("  no such season")
        return
    episodes = episodes_of(session, token, season["id"])
    if not episodes:
        print("  that season is empty")
        return
    table("episodes - " + season["title"], ["ep", "title"],
          [[str(e["episode"]) if e["episode"] is not None else "?", e["title"]]
           for e in episodes])
    wanted = pick_episodes(episodes)
    if not wanted or not ask_yes("grab episode(s) %s?" % wanted):
        return
    for number in wanted:
        match = next((e for e in episodes if e["episode"] == number), None)
        if match:
            run_episode(session, token, match, images)
        else:
            print("  episode %s isn't in that list" % number)

def flow_url(session, token):
    url = ask("\npaste a crunchyroll link")
    if not url:
        return
    try:
        series_id, season_no, ep_no, cid = parse_link(session, token, url)
    except Exception as e:
        print("  %s" % e)
        return
    print("  series id: %s" % series_id)
    if cid and season_no is not None and ep_no is not None:
        print("  looks like S%02dE%02d" % (season_no, ep_no))
        if ask_yes("just this episode?"):
            obj = get_object(session, token, cid)
            meta = obj.get("episode_metadata", {})
            run_episode(session, token,
                        {"id": cid, "title": obj.get("title", "Episode"),
                         "series": meta.get("series_title", "Unknown"),
                         "series_id": meta.get("series_id", series_id),
                         "season": meta.get("season_number", season_no),
                         "episode": meta.get("episode_number", ep_no)})
            return
    flow_series(session, token, series_id)

def main():
    print("\n  LastPerson07\n")
    if not check_tools():
        if os.path.isfile(WVD):
            print("\ninstall what's missing and run me again")
        else:
            print(WVD_NOTICE)
        sys.exit(1)
    session, token = login()
    try:
        while True:
            print("\n  menu\n    1  search a show\n    2  paste a link\n    3  quit")
            choice = ask("  pick", "1")
            if choice == "1":
                flow_search(session, token)
            elif choice == "2":
                flow_url(session, token)
            elif choice == "3":
                print("\nbye")
                return
    except PermissionError:
        print("\n  cookie went stale, logging in again...")
        session, token = login(force=True)
        main()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        bail_out()
    except Exception as err:
        print("\nsomething broke: %s" % err)
        wipe()
        sys.exit(1)
