"""
EHAM Weather Dashboard
- KNMI Official METARs (every 30 min, HH+25 / HH+55)
- KNMI 10-minute in-situ observations from Schiphol (station 06240)
Last 48 hours of data.
"""

import os
import re
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────────
KNMI_API_KEY = "eyJvcmciOiI1ZTU1NGUxOTI3NGE5NjAwMDEyYTNlYjEiLCJpZCI6ImQ2NDI4ZjRlZGE5YzQ1NTA5ZjJmMmIyZjkxNmM1MzdjIiwiaCI6Im11cm11cjEyOCJ9"
BASE_URL = "https://api.dataplatform.knmi.nl/open-data/v1"
EDR_BASE = "https://api.dataplatform.knmi.nl/edr/v1"

METAR_DATASET = "metar"
METAR_VERSION = "1.0"
OBS_COLLECTION = "10-minute-in-situ-meteorological-observations"
SCHIPHOL_STATION = "0-20000-0-06240"  # station 06240 = Amsterdam/Schiphol AP

# ── Helpers ───────────────────────────────────────────────────────────────────

def knmi_headers():
    return {"Authorization": KNMI_API_KEY}


def get_now_utc():
    return datetime.now(timezone.utc)


def dt_range_48h():
    end = get_now_utc()
    start = end - timedelta(hours=48)
    return start, end


# ── METAR fetching ─────────────────────────────────────────────────────────────

def list_metar_files(start: datetime, end: datetime) -> list[str]:
    """List METAR files from KNMI Open Data API, newest first, within time range."""
    params = {
        "maxKeys": 200,
        "orderBy": "filename",
        "sorting": "desc",
    }
    url = f"{BASE_URL}/datasets/{METAR_DATASET}/versions/{METAR_VERSION}/files"
    r = requests.get(url, headers=knmi_headers(), params=params, timeout=15)
    r.raise_for_status()
    files = r.json().get("files", [])

    # Filter by timestamp encoded in filename: METAR_EHAM_YYYYMMDDHHMMSS.txt
    # or similar — we filter to last 48h based on lastModified
    cutoff = start.timestamp()
    result = []
    for f in files:
        lm = f.get("lastModified", "")
        # lastModified is ISO string
        try:
            t = datetime.fromisoformat(lm.replace("Z", "+00:00"))
            if t.timestamp() >= cutoff:
                result.append(f["filename"])
        except Exception:
            pass
    return result


def download_metar_file(filename: str) -> str:
    """Get temporary download URL and fetch content."""
    url = f"{BASE_URL}/datasets/{METAR_DATASET}/versions/{METAR_VERSION}/files/{filename}/url"
    r = requests.get(url, headers=knmi_headers(), timeout=10)
    r.raise_for_status()
    dl_url = r.json().get("temporaryDownloadUrl")
    content = requests.get(dl_url, timeout=10)
    content.raise_for_status()
    return content.text


def parse_metar_temp(raw: str) -> float | None:
    """Extract temperature from raw METAR string. Returns °C or None."""
    # Temperature field: TT/TD, e.g. 15/07, M02/M05
    m = re.search(r'\b(M?\d{2})/(M?\d{2})\b', raw)
    if m:
        t = m.group(1)
        sign = -1 if t.startswith("M") else 1
        return sign * int(t.lstrip("M"))
    return None


def parse_metar_time(raw: str) -> datetime | None:
    """Extract observation time from METAR (DDHHMMz group)."""
    m = re.search(r'\b(\d{2})(\d{2})(\d{2})Z\b', raw)
    if m:
        day, hour, minute = int(m.group(1)), int(m.group(2)), int(m.group(3))
        now = get_now_utc()
        # Reconstruct: same month/year, adjust if day rolled over
        try:
            t = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
            if t > now + timedelta(hours=1):
                # Must be previous month
                t = (t.replace(day=1) - timedelta(days=1)).replace(
                    day=day, hour=hour, minute=minute, second=0, microsecond=0
                )
            return t
        except ValueError:
            return None
    return None


@st.cache_data(ttl=120)
def fetch_metars() -> pd.DataFrame:
    """Fetch METAR data for EHAM for last 48h."""
    start, end = dt_range_48h()
    try:
        filenames = list_metar_files(start, end)
    except Exception as e:
        st.error(f"Error listing METAR files: {e}")
        return pd.DataFrame()

    records = []
    for fn in filenames:
        # Only process files that look like EHAM
        if "EHAM" not in fn and "metar" not in fn.lower():
            continue
        try:
            text = download_metar_file(fn)
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "EHAM" in line:
                    t = parse_metar_time(line)
                    temp = parse_metar_temp(line)
                    if t and temp is not None:
                        records.append({"time": t, "temp_c": temp, "raw": line})
        except Exception:
            continue

    if not records:
        return pd.DataFrame(columns=["time", "temp_c", "raw"])

    df = pd.DataFrame(records)
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    return df


# ── 10-min observations fetching ───────────────────────────────────────────────

@st.cache_data(ttl=300)
def fetch_10min_obs() -> pd.DataFrame:
    """Fetch 10-minute temperature observations from Schiphol via KNMI EDR API."""
    start, end = dt_range_48h()
    dt_str = f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end.strftime('%Y-%m-%dT%H:%M:%SZ')}"

    url = f"{EDR_BASE}/collections/{OBS_COLLECTION}/locations/{SCHIPHOL_STATION}"
    params = {
        "datetime": dt_str,
        "parameter-name": "ta",  # air temperature at 1.5m
        "f": "CoverageJSON",
    }
    try:
        r = requests.get(url, headers=knmi_headers(), params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        st.error(f"Error fetching 10-min observations: {e}")
        return pd.DataFrame()

    # Show raw response for debugging
    with st.expander("🔍 10-min obs raw API response (debug)", expanded=False):
        st.json(data)

    # Parse CoverageJSON
    try:
        times_raw = data["domain"]["axes"]["t"]["values"]
        values = data["ranges"]["ta"]["values"]
        times = [datetime.fromisoformat(t.replace("Z", "+00:00")) for t in times_raw]
        df = pd.DataFrame({"time": times, "temp_c": values})
        df = df.dropna(subset=["temp_c"]).sort_values("time").reset_index(drop=True)
        return df
    except Exception as e:
        st.error(f"Error parsing 10-min obs response: {e}")
        st.json(data)  # show raw response so we can see what came back
        return pd.DataFrame()

# ── UI ─────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="EHAM Weather", page_icon="✈️", layout="wide")

st.title("✈️ EHAM — Amsterdam Schiphol Weather")
st.caption("KNMI official METARs + 10-minute AWS observations · Last 48 hours")

# API key input if not set via env
if not KNMI_API_KEY:
    api_key_input = st.sidebar.text_input(
        "KNMI API Key", type="password",
        help="Get a free key at https://developer.dataplatform.knmi.nl"
    )
    if api_key_input:
        KNMI_API_KEY = api_key_input
    else:
        st.warning("⚠️ Enter your KNMI API key in the sidebar to load data.")
        st.info("Get a free key at https://developer.dataplatform.knmi.nl")
        st.stop()

# Refresh button
col1, col2 = st.columns([6, 1])
with col2:
    if st.button("🔄 Refresh"):
        st.cache_data.clear()

# Load data
with st.spinner("Fetching METAR data from KNMI..."):
    df_metar = fetch_metars()

with st.spinner("Fetching 10-min observations from KNMI..."):
    df_obs = fetch_10min_obs()

# ── Chart ──────────────────────────────────────────────────────────────────────

fig = go.Figure()

# 10-min observations — faint background line
if not df_obs.empty:
    fig.add_trace(go.Scatter(
        x=df_obs["time"],
        y=df_obs["temp_c"],
        mode="lines",
        name="10-min AWS obs",
        line=dict(color="rgba(100, 160, 255, 0.5)", width=1.5),
        hovertemplate="%{x|%d %b %H:%M UTC}<br>Temp: %{y:.1f}°C<extra>10-min obs</extra>",
    ))

# Official METARs — bold markers + line
if not df_metar.empty:
    fig.add_trace(go.Scatter(
        x=df_metar["time"],
        y=df_metar["temp_c"],
        mode="lines+markers",
        name="Official METAR",
        line=dict(color="#FF6B35", width=2.5),
        marker=dict(size=7, color="#FF6B35", symbol="circle"),
        hovertemplate="%{x|%d %b %H:%M UTC}<br>Temp: %{y:.1f}°C<extra>METAR</extra>",
        customdata=df_metar["raw"],
    ))

now = get_now_utc()
fig.add_vline(
    x=now, line_dash="dash", line_color="gray", line_width=1,
    annotation_text="now", annotation_position="top right"
)

fig.update_layout(
    title=dict(text="Temperature (°C) — EHAM last 48h", font=dict(size=16)),
    xaxis=dict(
        title="Time (UTC)",
        range=[now - timedelta(hours=48), now + timedelta(minutes=30)],
        gridcolor="#2a2a2a",
    ),
    yaxis=dict(title="Temperature (°C)", gridcolor="#2a2a2a"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    hovermode="x unified",
    template="plotly_dark",
    height=500,
    margin=dict(l=60, r=30, t=60, b=60),
)

st.plotly_chart(fig, use_container_width=True)

# ── Stats row ──────────────────────────────────────────────────────────────────

col1, col2, col3, col4 = st.columns(4)

if not df_metar.empty:
    latest = df_metar.iloc[-1]
    col1.metric("Latest METAR temp", f"{latest['temp_c']:.1f} °C",
                help=f"{latest['time'].strftime('%H:%MZ')}")
    col2.metric("METAR 48h min", f"{df_metar['temp_c'].min():.1f} °C")
    col3.metric("METAR 48h max", f"{df_metar['temp_c'].max():.1f} °C")
    col4.metric("METAR reports loaded", str(len(df_metar)))

if not df_obs.empty:
    st.caption(
        f"10-min obs: {len(df_obs)} data points  |  "
        f"Latest: {df_obs.iloc[-1]['temp_c']:.1f}°C at "
        f"{df_obs.iloc[-1]['time'].strftime('%H:%MZ')}"
    )

# ── Raw METAR table ────────────────────────────────────────────────────────────

with st.expander("📋 Raw METAR strings"):
    if not df_metar.empty:
        display = df_metar[["time", "temp_c", "raw"]].copy()
        display["time"] = display["time"].dt.strftime("%Y-%m-%d %H:%MZ")
        display.columns = ["Time (UTC)", "Temp (°C)", "Raw METAR"]
        st.dataframe(display[::-1].reset_index(drop=True), use_container_width=True)
    else:
        st.info("No METAR data loaded.")

with st.expander("📊 10-min observation data"):
    if not df_obs.empty:
        display_obs = df_obs.copy()
        display_obs["time"] = display_obs["time"].dt.strftime("%Y-%m-%d %H:%MZ")
        display_obs.columns = ["Time (UTC)", "Temp (°C)"]
        st.dataframe(display_obs[::-1].reset_index(drop=True), use_container_width=True)
    else:
        st.info("No 10-min observation data loaded.")

st.divider()
st.caption("Data: KNMI Data Platform (CC BY 4.0) · METAR dataset `metar v1.0` · 10-min obs `10-minute-in-situ-meteorological-observations v1.0` · Station 06240 Schiphol")
