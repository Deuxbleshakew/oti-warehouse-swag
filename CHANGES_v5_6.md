# v5.6 — Double-click launchers

- **Start Backend.bat** — double-click to run the backend locally.
  Creates a private `.venv` and installs `requirements.txt` on first run
  (re-installs automatically if requirements.txt ever changes), then
  starts uvicorn on port 8000 and opens the ordering page in the browser
  once `/health` answers. Close the window or Ctrl+C to stop.
- **Open Admin App.bat** — double-click to open the admin desktop app.
  Same self-setup, then launches `admin_app\main.py` via `pythonw`
  (no console window left behind). Point Server at the Render URL or
  `http://localhost:8000`.
- Both give plain-English messages if Python isn't installed or the
  package install fails. No app code changed — backend build stays
  `5.4-event-shipping`.
