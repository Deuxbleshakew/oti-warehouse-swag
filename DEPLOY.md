# Deploying Oti-Warehouse Swag to Render

This gets your backend onto a real internet URL — reachable from home,
your phone, anywhere — instead of only working while your PC is on.

Everything in this guide is free to start. No credit card needed for
Render's free tier.

---

## Step 1 — Create a GitHub account

Render deploys your code from a GitHub repository, so this comes first.

1. Go to **github.com** and sign up (email + password, a couple minutes).
2. Verify your email if it asks you to.

---

## Step 2 — Get your code onto GitHub

You don't need to learn git commands for this — GitHub lets you upload
files straight from your browser.

1. Once logged in, click the **+** in the top-right corner → **New repository**.
2. Name it `oti-warehouse-swag`. Leave it **Public** or **Private**, your
   choice (Private just means only you can see the code — doesn't
   affect anything else).
3. Don't check any of the "Add a README" boxes — leave the repo empty.
4. Click **Create repository**.
5. On the next page, click **uploading an existing file**.
6. Drag your entire `swag_system` folder contents in (or the files one
   level in — everything from inside `swag_system`, not the folder
   itself) and commit.

Your repo now has `backend/`, `frontend/`, `admin_app/`, `render.yaml`,
`requirements.txt`, etc. at the root.

---

## Step 3 — Create a Render account

1. Go to **render.com** → **Get Started**.
2. Sign up with GitHub (easiest — it connects the two automatically).

---

## Step 4 — Deploy the Blueprint

This is the part `render.yaml` does the heavy lifting for — Render reads
it and sets up the web service *and* the database together.

1. In the Render dashboard, click **New** → **Blueprint**.
2. Pick the `oti-warehouse-swag` repo you just created.
3. Render finds `render.yaml` and shows you what it's about to create:
   a web service and a Postgres database. Click **Apply**.
4. Wait a few minutes for the first build. You'll see logs scrolling —
   it's installing packages and creating database tables.
5. Once it says **Live**, click into the web service and copy its URL
   (something like `https://oti-warehouse-swag.onrender.com`).
6. Check it worked: open `<that URL>/health` in your browser — you
   should see `{"status":"ok"}`.

---

## Step 5 — Bring your real data over

Your Render database starts **empty** — none of your real items, users,
or orders are there yet. Don't run `seed_demo.py` unless you actually
want to wipe in and start over with fake demo data.

Instead, from **your own computer** (not Render):

1. In the Render dashboard, open your database → find the
   **External Database URL** (not the internal one — that one only
   works from inside Render).
2. Run:
   ```
   python scripts\migrate_to_postgres.py "<paste the External Database URL here>"
   ```
3. It'll show you what it's about to copy and ask to confirm. Type `y`.

This copies every item, user, order, and history record from your local
database into the new one — including any passwords you've already
changed. Safe to run again later if you ever need to.

---

## Step 6 — Point your apps at the new URL

- **Web ordering page**: open the Render service URL itself. The ordering
  page is served at `/` and automatically talks to the same backend.
- **Admin app**: on the login screen, put the Render URL in the
  **Server** field instead of `localhost:8000`.

From here, both work from any computer with internet access — not just
yours.

---

## What to expect on the free plan

- **Cold starts**: if nobody's used it in 15 minutes, the next request
  takes 30–60 seconds while it wakes back up. After that it's normal
  speed until it goes idle again.
- **Photos are database-backed**: new uploads are stored with the item data,
  so they no longer depend on the web server's temporary local files. Photos
  uploaded with an older build may need to be uploaded one more time if their
  original file has already disappeared.
- **Event shipping dates are automatic**: enter the event date and complete
  delivery address. The page targets delivery for the previous business day,
  applies the supplied UPS Ground map estimate, and stores the latest ship date
  with the order. Exact ZIP quotes can override the 1–6 day estimate.
- **Free database**: your Postgres database itself doesn't expire on
  its own, but keep an eye on Render's dashboard for any usage limits.

## Upgrading to always-on later

An always-on service can still be useful when you no longer want cold starts.
Photo persistence no longer requires a separate disk because uploaded image
bytes are stored in the database.
