#!/usr/bin/env python3
"""
playlist_sync.py  –  V1.8
────────────────────────────────────────────────────────────────────────────
• Two-way Jellyfin playlist sync
• Rolling log file (1 MB)
• Cache refresh on start + every <cache_secs>
• Safe / Freeze windows driven by live scan progress
• Queued tail-adds now flush via API *without* forcing a Library.Refresh
"""

import os, time, json, sys, hashlib, xml.etree.ElementTree as ET
import requests, logging, http.client
from logging.handlers import RotatingFileHandler
from pathlib import Path
from collections import defaultdict

# ─── logging ─────────────────────────────────────────────────────────────
DEBUG_ON = os.getenv("PLAYLIST_SYNC_DEBUG", "0") == "0"

fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", "%H:%M:%S")
stream = logging.StreamHandler();       stream.setFormatter(fmt)
fileh  = RotatingFileHandler("playlist_sync.log",
                             maxBytes=1_048_576, backupCount=1,
                             encoding="utf-8");         fileh.setFormatter(fmt)

log = logging.getLogger("psync")
log.setLevel(logging.DEBUG if DEBUG_ON else logging.INFO)
log.addHandler(stream);  log.addHandler(fileh)

if DEBUG_ON:
    logging.getLogger("urllib3").setLevel(logging.DEBUG)
    http.client.HTTPConnection.debuglevel = 1

def info(msg): log.info(msg)

# ─── config ──────────────────────────────────────────────────────────────
CFG_FILE, HASH_STORE, ID_CACHE = (
    "playlist_sync.json", "playlist_sync.hashes", "playlist_sync.ids")

cfg = json.load(open(CFG_FILE, encoding="utf-8"))

DEBOUNCE   = cfg.get("debounce_secs")
POLL       = cfg.get("poll_secs")
CACHE_INT  = cfg.get("cache_secs")
JF_URL     = cfg["jellyfin_url"].rstrip("/")
API_KEY    = cfg["api_key"]
PAIRS      = cfg["pairs"]

# ─── globals -------------------------------------------------------------
HEAD = {"X-Emby-Token": API_KEY}
MUSIC_LIB_ID = None
try:    PATH2ID = json.load(open(ID_CACHE))
except (FileNotFoundError, json.JSONDecodeError): PATH2ID = {}

HASHES, CHANGE_AT      = {}, defaultdict(float)
QUEUE                  = set()
next_cache_refresh     = time.time() + CACHE_INT

# ─── HTTP helper ---------------------------------------------------------
sess = requests.Session()
def _req(m, url, **kw):
    if DEBUG_ON and "/Playlists/" in url:
        log.debug("--> %s %s", m, url)
    r = sess.request(m, url, headers=HEAD, timeout=15, **kw)
    if DEBUG_ON and "/Playlists/" in url:
        log.debug("<-- %s %s", r.status_code, r.reason)
    return r
g = lambda u, **k: _req("GET",  u, **k)
p = lambda u, **k: _req("POST", u, **k)

# ─── XML helpers ---------------------------------------------------------
def tidy_xml(path):
    root = ET.parse(path).getroot()
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    if (ad := root.find("Added")) is not None:
        root.remove(ad)
    return root
def write_xml(root, dst):
    ET.ElementTree(root).write(dst, encoding="utf-8", xml_declaration=True)
def xml_hash(path):
    try:    return hashlib.sha1(ET.tostring(tidy_xml(path))).hexdigest()
    except Exception: return None
def playlist_info(path):
    r = tidy_xml(path)
    return (
        r.findtext("OwnerUserId"),
        r.findtext("LocalTitle"),
        [pi.findtext("Path") for pi in r.findall("./PlaylistItems/PlaylistItem") if pi.findtext("Path")],
        r)
def show_name(path):
    try:    return playlist_info(path)[1] or Path(path).parent.name
    except Exception: return Path(path).parent.name

# ─── cache builder with ticker ------------------------------------------
def build_media_map(update_only=False):
    global MUSIC_LIB_ID, PATH2ID
    if MUSIC_LIB_ID is None:
        for v in g(f"{JF_URL}/Library/VirtualFolders").json():
            if v.get("CollectionType","").lower()=="music":
                MUSIC_LIB_ID=v["ItemId"]; break
        if not MUSIC_LIB_ID:
            sys.exit("Music library not found.")

    info("Refreshing cache…" if update_only else "Caching ItemIds…")
    added, start = 0, 0
    while True:
        url=(f"{JF_URL}/Items?ParentId={MUSIC_LIB_ID}"
             "&IncludeItemTypes=Audio&Recursive=true&Fields=Path"
             f"&StartIndex={start}&Limit=1000")
        data=g(url).json(); items=data.get("Items",[])
        if not items: break
        for it in items:
            p=it.get("Path",""); key=p.lower()
            if not p or (update_only and key in PATH2ID): continue
            PATH2ID[key]=it["Id"]; added+=1
        start+=len(items)
        total=data.get("TotalRecordCount",0)
        print(f"\r  {start}/{total} tracks cached — please wait", end="")
        if start>=total: break
    print()
    if added:
        json.dump(PATH2ID, open(ID_CACHE,"w"))
        msg="initial cache" if not update_only else "cache updated"
        info(f"✅ {msg}, +{added} tracks")
    elif update_only:
        info("✅ cache already up-to-date")

# ─── scan progress helper -------------------------------------------------
def scan_state():
    best_pct=None
    try:
        for t in g(f"{JF_URL}/ScheduledTasks").json():
            name=(t.get("Name") or "").lower(); key=t.get("Key","")
            if (("scan" in name and "library" in name) or key=="RefreshLibrary"):
                if t.get("State")=="Running":
                    best_pct = max(best_pct or 0, t.get("CurrentProgressPercentage",0.0))
    except Exception: pass
    if best_pct is None: return "idle", None
    return ("safe" if best_pct<86 else "freeze"), best_pct

# ─── playlist helpers -----------------------------------------------------
def find_playlist(owner, xml_path, title):
    want = Path(xml_path).parent.as_posix().lower()
    items = g(f"{JF_URL}/Users/{owner}/Items?IncludeItemTypes=Playlist&Recursive=true"
              "&Fields=Path,Name,Id&Limit=200").json()["Items"]
    for it in items:
        if it.get("Path","").lower().startswith(want):
            return it["Id"]
    for it in items:
        if it.get("Name")==title: return it["Id"]
    return None
def append_items(pid, owner, ids):
    if ids:
        p(f"{JF_URL}/Playlists/{pid}/Items?Ids={','.join(ids)}&UserId={owner}")
def copy_xml(src,dst,own_d,title_d):
    root=playlist_info(src)[3]
    root.find("OwnerUserId").text=own_d
    root.find("LocalTitle").text =title_d
    write_xml(root,dst)
def enqueue(src,dst):
    QUEUE.add((src,dst))
    info(f"⏸️  Change queued (freeze) for {show_name(src)}")

# ─── enqueue Jellyfin Library.Refresh ------------------------------------
def enqueue_refresh():
    p(f"{JF_URL}/Library/Refresh")
    info("🔄 Library.Refresh triggered")

# ─── sync logic -----------------------------------------------------------
def sync_pair(src_xml,dst_xml,state):
    own_s,_,src_paths,_ = playlist_info(src_xml)
    own_d,title_d,dst_paths,_ = playlist_info(dst_xml)
    if src_paths==dst_paths: return

    # tail-add fast path
    if len(src_paths)>len(dst_paths) and src_paths[:len(dst_paths)]==dst_paths:
        new_ids=[PATH2ID.get(p.lower()) for p in src_paths[len(dst_paths):] if p.lower() in PATH2ID]
        if not new_ids: return
        if state=="idle":
            pid=find_playlist(own_d,dst_xml,title_d)
            if pid: append_items(pid,own_d,new_ids)
            info(f"➕ Added {len(new_ids)} (API) to {show_name(dst_xml)}")
        elif state=="safe":
            copy_xml(src_xml,dst_xml,own_d,title_d)
            info(f"📝 Added {len(new_ids)} via XML (safe) to {show_name(dst_xml)}")
            enqueue_refresh()
        else:
            enqueue(src_xml,dst_xml)
        return

    # complex change
    if state=="idle":
        copy_xml(src_xml,dst_xml,own_d,title_d)
        info(f"📝 Copied XML for {show_name(src_xml)} → {show_name(dst_xml)}")
        enqueue_refresh()
    elif state=="safe":
        copy_xml(src_xml,dst_xml,own_d,title_d)
        info(f"📝 Copied XML (safe) for {show_name(src_xml)}")
        enqueue_refresh()
    else:
        enqueue(src_xml,dst_xml)

# ─── hash helpers ---------------------------------------------------------
def load_hashes():
    global HASHES
    try: HASHES=json.load(open(HASH_STORE))
    except: HASHES={}
    for pr in PAIRS:
        for k in ("src","dst"):
            HASHES.setdefault(pr[k], xml_hash(pr[k]))
    json.dump(HASHES, open(HASH_STORE,"w"))

# ─── main loop ------------------------------------------------------------
def main():
    global next_cache_refresh
    info("🟢 playlist-sync starting")
    build_media_map(False); load_hashes()

    while True:
        time.sleep(POLL)

        # periodic cache refresh
        if time.time() >= next_cache_refresh:
            build_media_map(True)
            next_cache_refresh = time.time() + CACHE_INT

        state,pct = scan_state()
        if pct is not None:
            print(f"\r📊 Library scan {pct:.1f}% ({state})", end="")

        # flush queued changes when idle
        if state=="idle" and QUEUE:
            need_refresh=False
            for src,dst in list(QUEUE):
                own_s,_,src_paths,_ = playlist_info(src)
                _,_,dst_paths,_     = playlist_info(dst)
                if len(src_paths)>len(dst_paths) and src_paths[:len(dst_paths)]==dst_paths:
                    # tail-add → API
                    pid=find_playlist(playlist_info(dst)[0], dst, playlist_info(dst)[1])
                    new_ids=[PATH2ID.get(p.lower()) for p in src_paths[len(dst_paths):] if p.lower() in PATH2ID]
                    if pid and new_ids:
                        append_items(pid, playlist_info(dst)[0], new_ids)
                        info(f"➕ (flush) Added {len(new_ids)} (API) to {show_name(dst)}")
                else:
                    copy_xml(src,dst,playlist_info(dst)[0],playlist_info(dst)[1])
                    info(f"📝 (flush) Copied XML for {show_name(src)}")
                    need_refresh=True
                QUEUE.remove((src,dst))
            if need_refresh:
                enqueue_refresh()
            next_cache_refresh = time.time() + CACHE_INT

        now=time.time()
        for pr in PAIRS:
            src,dst=pr["src"],pr["dst"]
            h_src,h_dst=xml_hash(src), xml_hash(dst)

            if h_src!=HASHES.get(src):
                if src not in CHANGE_AT: CHANGE_AT[src]=now
                if now-CHANGE_AT[src]>=DEBOUNCE:
                    sync_pair(src,dst,state)
                    HASHES[src]=h_src; HASHES[dst]=xml_hash(dst)
                    json.dump(HASHES, open(HASH_STORE,"w"))
                    CHANGE_AT.pop(src,None)

            elif h_dst!=HASHES.get(dst):
                if dst not in CHANGE_AT: CHANGE_AT[dst]=now
                if now-CHANGE_AT[dst]>=DEBOUNCE:
                    sync_pair(dst,src,state)
                    HASHES[dst]=h_dst; HASHES[src]=xml_hash(src)
                    json.dump(HASHES, open(HASH_STORE,"w"))
                    CHANGE_AT.pop(dst,None)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        info("👋 stopped by user")
