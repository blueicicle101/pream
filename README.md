# 📡 Pream (Proxy Stream)

**Pream** is a lightweight, high-performance, self-hosted IPTV playlist manager and Xtream Codes API gateway built with Python, FastAPI, and SQLite.

Inspired by *Tuliprox*, Pream unifies multiple upstream Xtream Codes and M3U playlists into a single clean, curated, and organized library for your IPTV players (TiviMate, IPTV Smarters, VLC, Apple TV, etc.).

### DISCLAIMER
<span style="color:'red !important';"> This project does not condone the use of illegally sourced IPTV content and this software does not include any source by default.</span>

---

## ✨ Features

- **Multi-Source Ingestion**: Ingest multiple Xtream and M3U providers simultaneously with support for parallel background fetching.
- **Rules Engine with Raw Name Retention**: All transformations (`rename_channel`, `rename_group`, `hide_group`, `hide_channel`, `set_group`) mutate only display fields. Provider names (`raw_name`, `raw_group`) are never modified, allowing instant re-evaluation and tuning without re-fetching providers.
- **Zero-Bandwidth Stream Redirects**: Proxies live TV, movies, and series via HTTP 302 redirects directly to upstream providers. Your server uses 0 stream bandwidth.
- **User-Agent Cloaking**: Sticky User-Agent per source or randomized realistic media player User-Agents per refresh cycle.
- **Standard Xtream Codes & M3U Output**: Native `/player_api.php` and `/get.php` endpoints compatible with all modern IPTV applications.
- **TMDB Enrichment**: Automatically enriches movies and TV series with high-resolution posters, backdrops, ratings, and genre tags, with support for `<LANG> | <GENRE>` re-grouping.
- **Telegram Notifications**: Detailed refresh reports and instant alerts on source failure (auth errors, banned credentials, 404s).
- **Modern Dark Web UI**: Bootstrap 5 + Bootswatch Darkly dashboard with an embedded HLS.js video player for previewing streams in your browser.
- **Docker-Ready**: Packaged with Dockerfile, health checks, and Docker Compose templates.

---

## 🚀 Quick Start

### 1. Using Docker Compose (Recommended)

1. Clone this repository and copy the environment template:
   ```bash
   cp .env.example .env
   ```
2. Start the container:
   ```bash
   docker compose up -d
   ```
3. Open your browser and navigate to:
   ```
   http://localhost:8000
   ```

### 2. Local Python Development

1. Create and activate a Python virtual environment:
   ```bash
   python -m venv venv
   # On Windows:
   .\venv\Scripts\activate
   # On Linux/macOS:
   source venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Start the application:
   ```bash
   python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

---

## 📺 Connecting IPTV Players

In TiviMate, IPTV Smarters, or your preferred player, select **Xtream Codes API** and enter:

- **Server URL**: `http://<your-server-ip>:8000`
- **Username**: `pream` *(or your `OUTPUT_USERNAME` from `.env`)*
- **Password**: `pream` *(or your `OUTPUT_PASSWORD` from `.env`)*

Or connect via **M3U Playlist**:
```
http://<your-server-ip>:8000/get.php?username=pream&password=pream
```

---

## 🛠️ Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `OUTPUT_USERNAME` | `pream` | Username used by IPTV clients |
| `OUTPUT_PASSWORD` | `pream` | Password used by IPTV clients |
| `ADMIN_USERNAME` | `admin` | Admin dashboard username |
| `ADMIN_PASSWORD` | `pream` | Admin dashboard password |
| `DATABASE_URL` | `sqlite:///./data/pream.db` | SQLite database location |
| `REFRESH_CRON` | `0 4 * * *` | Scheduled refresh schedule (4:00 AM daily) |
| `GROUP_BY_SOURCE`| `false` | Prefix all categories with source name |
| `TMDB_API_KEY` | `""` | TMDB API v3 key for movie/series metadata |
| `TELEGRAM_BOT_TOKEN` | `""` | Telegram bot token for reports/alerts |
| `TELEGRAM_CHAT_ID` | `""` | Telegram chat ID for notifications |

---

## 🧪 Running Tests

Run the full test suite with unittest:
```bash
python -m unittest discover tests
```

---

## 📄 License

AGPL-3.0 License. All further modifications must be open-source under the AGPL License.
Reference: https://choosealicense.com/licenses/agpl-3.0/
