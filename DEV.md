# Local Development Guide

This guide explains how to run PlexBot locally for development without waiting for Docker builds.

## Quick Start

### Option 1: Use the Dev Script (Recommended)

```bash
./dev.sh
```

This script will:
- Check and install dependencies
- Start the Flask backend on port 5000
- Start the Vite dev server on port 5173
- Proxy API requests from the frontend to the backend

### Option 2: Manual Setup

#### 1. Install Python Dependencies

```bash
pip3 install -r requirements.txt
```

#### 2. Install Node Dependencies

```bash
cd webui
npm install
cd ..
```

#### 3. Set Up Environment Variables

```bash
cp sample.env .env
# Edit .env with your configuration
```

#### 4. Start the Backend

In one terminal:

```bash
python3 dev_backend.py
```

Or if you want to run the full bot (including Discord):

```bash
python3 bot.py
```

#### 5. Start the Frontend

In another terminal:

```bash
cd webui
npm run dev
```

## Accessing the Application

- **Frontend (Vite Dev Server)**: http://localhost:5173
- **Backend API**: http://localhost:5000
- **API Endpoints**: http://localhost:5173/api/* (proxied to backend)

## Development Workflow

1. **Frontend Changes**: 
   - Edit files in `webui/src/`
   - Vite will hot-reload automatically
   - Changes appear instantly in the browser

2. **Backend Changes**:
   - Edit Python files in the root directory
   - Flask will auto-reload if `debug=True` (default in `dev_backend.py`)
   - Restart the backend if needed

3. **API Testing**:
   - All `/api/*` requests from the frontend are proxied to `http://localhost:5000`
   - You can also test API endpoints directly at `http://localhost:5000/api/*`

## Notes

- The Vite dev server proxies `/api` requests to the Flask backend automatically
- You don't need to build the frontend (`npm run build`) for development
- The Discord bot functionality requires the full `bot.py` to be running
- For web UI development, `dev_backend.py` is sufficient (no Discord bot needed)

## Troubleshooting

### Port Already in Use

If port 5000 or 5173 is already in use:

**Backend (port 5000):**
```bash
# Find and kill the process
lsof -ti:5000 | xargs kill -9
```

**Frontend (port 5173):**
```bash
# Find and kill the process
lsof -ti:5173 | xargs kill -9
```

Or change the ports in:
- Backend: `dev_backend.py` (change `port=5000`)
- Frontend: `webui/vite.config.js` (change proxy target and server port)

### Missing Dependencies

If you get import errors:
```bash
pip3 install -r requirements.txt
cd webui && npm install && cd ..
```

### Environment Variables Not Loading

Make sure you have a `.env` file in the root directory:
```bash
cp sample.env .env
# Edit .env with your actual values
```

