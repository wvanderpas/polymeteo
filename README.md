# EHAM Weather Dashboard

Streamlit app showing the last 48 hours of:
- **Official METAR** data from KNMI (issued at HH+25 and HH+55)
- **10-minute AWS observations** from Schiphol station 06240

## Setup

### 1. Get a KNMI API key
Sign up for free at https://developer.dataplatform.knmi.nl  
No credit card needed. Takes ~1 minute.

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Run the app

**Option A — enter key in the sidebar UI:**
```bash
streamlit run eham_weather.py
```

**Option B — set key as environment variable:**
```bash
export KNMI_API_KEY="your-key-here"
streamlit run eham_weather.py
```

### 4. Deploy to Streamlit Community Cloud
1. Push this repo to GitHub
2. Go to https://share.streamlit.io
3. Connect your repo, set `eham_weather.py` as the main file
4. Add `KNMI_API_KEY` as a secret in the app settings

## Data sources

| Stream | Dataset | Update frequency |
|--------|---------|-----------------|
| Official METAR | `metar v1.0` | Every 30 min (HH+25, HH+55) |
| 10-min observations | `10-minute-in-situ-meteorological-observations v1.0` | Every 10 min |

Station: **06240 — Amsterdam/Schiphol AP**  
ICAO: **EHAM**

Data provided by KNMI under CC BY 4.0 license.
