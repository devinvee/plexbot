# Development Workflow

This guide explains how to develop and test locally before pushing to GitHub.

## Quick Development Cycle

### 1. Start Local Development Servers

**Option A: Use the dev script (recommended)**
```bash
./dev.sh
```

**Option B: Run manually in separate terminals**

Terminal 1 (Backend):
```bash
python3 dev_backend.py
```

Terminal 2 (Frontend):
```bash
cd webui
npm run dev
```

### 2. Make Your Changes

- **Frontend changes**: Edit files in `webui/src/`
  - Changes auto-reload in browser (hot module replacement)
  - No restart needed!

- **Backend changes**: Edit Python files (`.py` files in root)
  - Flask auto-reloads if `debug=True` (default in `dev_backend.py`)
  - If changes don't appear, restart the backend: `Ctrl+C` then `python3 dev_backend.py`

### 3. Test Your Changes

- Open http://localhost:5173 in your browser
- Test the features you're working on
- Check browser console for errors (F12)
- Check terminal output for backend errors

### 4. Iterate

- Make changes → See results instantly (frontend)
- Make changes → Restart backend if needed (backend)
- Test → Fix → Test again
- Repeat until working!

### 5. When Ready, Commit and Push

```bash
# Stage your changes
git add .

# Commit with a descriptive message
git commit -m "Your descriptive commit message"

# Push to GitHub
git push origin dev
```

## Common Development Tasks

### Testing Frontend Changes

1. Start dev servers: `./dev.sh`
2. Open http://localhost:5173
3. Edit `webui/src/App.jsx` or `webui/src/App.css`
4. Save → See changes instantly in browser
5. Test functionality
6. Repeat until satisfied

### Testing Backend API Changes

1. Start backend: `python3 dev_backend.py`
2. Test API endpoints:
   ```bash
   # Example: Test status endpoint
   curl http://localhost:5000/api/status
   
   # Or use the browser dev tools Network tab
   ```
3. Make changes to `media_watcher_service.py`
4. Restart backend if needed
5. Test again

### Testing Full Integration

1. Start both servers: `./dev.sh`
2. Test the full flow:
   - Frontend makes API call
   - Backend processes request
   - Frontend displays result
3. Check both browser console and terminal logs

## Stopping and Restarting

### Stop Servers
- Press `Ctrl+C` in the terminal(s) running the servers
- Or close the terminal windows

### Restart Servers
- Just run `./dev.sh` again (or the manual commands)
- No need to rebuild anything!

## Tips

### Fast Iteration
- Keep the dev servers running while you code
- Frontend changes appear instantly (no restart needed)
- Backend changes usually auto-reload (Flask debug mode)

### Debugging
- **Frontend errors**: Check browser console (F12)
- **Backend errors**: Check terminal output
- **API issues**: Check Network tab in browser dev tools

### Before Pushing
- Test all your changes thoroughly
- Make sure both frontend and backend work
- Check for console errors
- Write clear commit messages

## Example Session

```bash
# 1. Start development
./dev.sh

# 2. Open browser to http://localhost:5173

# 3. Make changes to webui/src/App.jsx
#    → Changes appear instantly!

# 4. Test the changes in browser

# 5. Make more changes, test again

# 6. When satisfied, commit
git add webui/src/App.jsx
git commit -m "Update UI layout"
git push origin dev

# 7. Stop dev servers (Ctrl+C)
```

## Troubleshooting

### Changes Not Appearing

**Frontend:**
- Hard refresh browser: `Ctrl+Shift+R` (or `Cmd+Shift+R` on Mac)
- Check browser console for errors
- Make sure Vite dev server is running

**Backend:**
- Restart the backend server
- Check terminal for errors
- Verify Flask debug mode is enabled

### Port Already in Use

```bash
# Kill process on port 5000 (backend)
lsof -ti:5000 | xargs kill -9

# Kill process on port 5173 (frontend)
lsof -ti:5173 | xargs kill -9
```

### Dependencies Missing

```bash
# Install Python deps
pip3 install -r requirements.txt

# Install Node deps
cd webui && npm install && cd ..
```

