# playlist-buddy

Keep two (or more) Jellyfin playlists in perfect, two-way sync across different users.

<p align="center">
  <img src="docs/diagram.png" width="450" alt="High-level flow" />
</p>

---

## ✨ Features

- Works on Jellyfin 10.10.x (uses the classic playlist endpoints)
- Handles adds, deletes, and reordering
- **Fast**: tail additions go straight through the Jellyfin API
- **Safe**: bulk deletes or reorders copy the full playlist XML and trigger a single `Library.Refresh`
- **Progress-aware**: never touches playlists while Jellyfin is actively scanning
- Rolling log (1 MB), plus optional full HTTP debug logging

---

## 🧠 How It Works

Jellyfin doesn't support real-time shared editing of playlists across users.  
What `playlist-buddy` does is:

- Watch for changes on a source playlist
- Mirror those changes to a destination playlist
- Repeat the process in both directions, so both stay in sync

---

## 🎯 Fast vs Slow Sync Paths

- **Adds to the end**: synced instantly using Jellyfin’s API (fast path)
- **Deletes or reorders**: the entire playlist is copied via raw XML and a `Library.Refresh` is triggered (slow path)

This is the best-effort solution without native Jellyfin shared editing support — and it works reliably with careful use.

---

## ✅ What It Does

| Action on source playlist       | Result on destination playlist             |
| ------------------------------ | ------------------------------------------ |
| Add track(s) to the end        | Added immediately via API (no refresh)     |
| Delete / Reorder               | XML is copied → one `Library.Refresh`      |
| Simultaneous edits < 5 sec     | The playlist modified last wins            |
| Edits during library scan      | < 86% scanned → processed normally         |
|                                | ≥ 86% → queued and applied after scan      |

> 📌 **Note:** Currently only supports **music** playlists.  
> I can add support for movies or other libraries if people want it.

---

## ❌ What It Doesn’t Do

- Merge conflicting edits (e.g., add + delete) after the 5-second debounce — the **later edit wins**
- Handle the `/Playlists/.../Items/Replace` endpoint (only in Jellyfin 10.11+)
- Sync metadata like images, titles, descriptions, liked/played flags — **only the track list** is synced

---

## ⚠️ Warnings

This tool is a workaround for something Jellyfin doesn’t officially support (as of v10.10.7).  
By default, **only the playlist owner can edit a playlist** — not multiple users.

What `playlist-buddy` does is act as a behind-the-scenes syncer — watching and mirroring playlists between users.

### Use at your own risk — but if you're careful, this tool can save loads of manual effort.

### Strongly recommended:

- 🧪 Test in a safe environment first (e.g., test server or dummy playlists)
- 💾 Back up your playlists before trying it on anything important
- ⚠️ Understand that **playlist data can be lost** if something misbehaves

---

## 🚨 Very Important

I can’t stress this enough:

- ❗️ **DO NOT have two users Delete or reorder from a playlist or multiple devices at the same time**
- ❗️ If deleting tracks, do it together, on one device, ideally in the same room, use the time to compliment each others hair
Additions are generally safe — but simultaneous **removals or reorders** can cause sync issues or data loss.

---

## ⚡ Quick Start

```bash
# 1. Python 3.10+ and requests
pip install requests

# 2. Copy the sample config
cp playlist_sync.json.example playlist_sync.json

# 3. Edit playlist_sync.json:
#     – Add your Jellyfin URL and API key
#     – Define source/destination playlist pairs (see notes below)

# 4. Run the script
python playlist_sync.py          # Ctrl-C to stop

# 5. Want wire-level logs?
SET PLAYLIST_SYNC_DEBUG=1        # Windows
export PLAYLIST_SYNC_DEBUG=1     # Linux/macOS
python playlist_sync.py
```

---

## 🔧 Next Steps & Tips

### 🔑 1. Create an API Key

- In Jellyfin, go to **Admin Panel → API Keys**
- Create a new key and use it in `playlist_sync.json`

---

### 📝 2. Create the playlists ahead of time

- You **must** create both playlists on both accounts **before running** the tool
- Add at least **1 song to each** (preferably the same one)
- You **can** use the same name for both playlists — Jellyfin keeps them in user-specific folders
- The second playlist may appear as `Playlist Name (1)` — that’s normal and expected

---

### ⚙️ 3. Configure your sync pairs

In `playlist_sync.json`, define pairs like this:

```json
"pairs": [
  {
    "src": "C:\ProgramData\Jellyfin\Server\data\playlists\EXAMPLE\playlist.xml",
    "dst": "C:\ProgramData\Jellyfin\Server\data\playlists\EXAMPLE1\playlist.xml"
  },
  {
    "src": "C:\ProgramData\Jellyfin\Server\data\playlists\EXAMPLE1\playlist.xml",
    "dst": "C:\ProgramData\Jellyfin\Server\data\playlists\EXAMPLE\playlist.xml"
  }
]
```

> ✅ You can sync multiple pairs at once.  
> ⚠️ Only **two-way sync** has been tested so far. More than two users **might** work, but is **not guaranteed**.

---

## 🧪 Platform & Compatibility Notes

- ✅ Tested on **Windows 10/11**
- 🐧 Should work on **Linux/macOS** too — it’s just Python (your mileage may vary)
- 🔁 Only tested with **two playlists / two users**
- 🤔 In theory, more users could be supported — but be careful with edit conflicts

---
