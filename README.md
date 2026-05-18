# Haier AC Controller

A simple self-hosted web app to control your GE / Haier SmartHQ air conditioner from any browser — desktop or phone — without using the official app.
Created as a much more responsive and less fussy replacement to the SmartHQ app


---

## Features

- Power on/off
- Mode: Cool / Eco (Energy Saver) / Fan Only
- Temperature control (°F)
- Fan speed: Auto / Low / Medium / High
- Live room temperature from the AC's ambient sensor
- Dark UI that works great on mobile — bookmark it to your home screen
- Fully self-hosted — runs on any machine on your local network

---

## Tested on

| Model | Type | BTU | Modes |
|-------|------|-----|-------|
| **QHNG10AA** (GE / Haier) | Window | 10,000 | Cool, Eco, Fan |

---

## Compatibility

This app uses GE's **SmartHQ API** via the [`gehomesdk`](https://github.com/simbaja/gehome) library. It should work with any GE or Haier SmartHQ WiFi air conditioner.

### Likely compatible (same ERD codes, same API)

**GE & Haier window ACs (cooling only)**
- QHNG series — QHNG08AA, QHNG10AA ✅ tested
- AWCS series — AWCS14WWA and similar
- PHC / Profile series — PHC06LY, PHC08LY
- QHC series (Haier-branded)

**GE & Haier portable ACs**
- APWD series — APWD08JAWW, APWD10JAWG
- APWA series — APWA11YBBW, APWA12YZBW
- QPWA series (Haier-branded) — QPWA14YZMW

**GE heat pump window ACs** (heat mode available in the API but not in this UI — easy to add)
- AHE series — AHE08AX, AHE12DX
- AWGP series — AWGP08WWA, AWGP12WWA

### Requirements

- Unit must have **SmartHQ WiFi** (built-in, or via the PBX23W00Y0 WiFi Connect add-on module)
- A SmartHQ account (same login as the official app)
- Python 3.11+ on a machine that stays on your local network (Raspberry Pi, NUC, old laptop, etc.)

### Not compatible

- Haier units using the **hOn** app (common outside North America) — use [pyhOn](https://github.com/Andre0512/pyhOn) instead
- Haier units using the **SmartAir2** app — use [haier-esphome](https://github.com/paveldn/haier-esphome) instead
- Units without any WiFi module

---

## Setup

**1. Clone and install**
```bash
git clone https://github.com/YOUR_USERNAME/haier-ac-controller.git
cd haier-ac-controller/bridge
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**2. Configure**
```bash
cp .env.example .env
```

Edit `.env`:
```
SMARTHQ_USERNAME=your@email.com
SMARTHQ_PASSWORD=yourpassword
SMARTHQ_REGION=US
SMARTHQ_DEVICE_ID=        # leave blank for now
```

**3. Find your device ID**

Start the server:
```bash
uvicorn main:app --host 0.0.0.0 --port 8765
```

Open `http://localhost:8765/api/devices` — it lists every appliance on your account. Copy the `id` for your AC into `.env` as `SMARTHQ_DEVICE_ID=`, then restart.

**4. Open the app**

`http://<your-server-ip>:8765` — works on any browser. On mobile, use "Add to Home Screen" to make it feel like an app.

---

## Run on boot (Linux systemd)

```ini
# /etc/systemd/system/haier-ac.service
[Unit]
Description=Haier AC Controller
After=network.target

[Service]
WorkingDirectory=/path/to/haier-ac-controller/bridge
ExecStart=/path/to/haier-ac-controller/bridge/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8765
Restart=always

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now haier-ac
```

---

## API

| Endpoint | Method | Description |
|---|---|---|
| `GET /` | GET | Web UI |
| `GET /api/devices` | GET | List all SmartHQ appliances (find your device ID here) |
| `GET /api/status` | GET | Current AC state |
| `POST /api/control` | POST | Change AC state |

`/api/control` body (all fields optional):
```json
{
  "power": "on | off",
  "mode":  "cool | eco | fan",
  "temp":  60–86,
  "fan":   "auto | low | medium | high"
}
```

---

## Adding heat pump support

If your unit supports heating, add these entries to `_MODE_TO_ERD` in `bridge/haier_smarthq.py`:

```python
"heat": ErdAcOperationMode.HEAT,
"dry":  ErdAcOperationMode.DRY,
```

Then add the corresponding buttons to `bridge/static/index.html`.

---

## Credits

- [`gehomesdk`](https://github.com/simbaja/gehome) by [@simbaja](https://github.com/simbaja) — reverse-engineered SmartHQ API
- [SmartHQ Developer Portal](https://developer.smarthq.com/)
