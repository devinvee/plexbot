# PlexBot Web UI

Modern React-based web interface for managing PlexBot.

## Development

```bash
npm install
npm run dev
```

The dev server will run on `http://localhost:5173` and proxy API requests to the Flask server on port 5000.

## Building for Production

```bash
npm run build
```

This will create a `dist` directory with the production build. The Flask server will automatically serve these files.

## Features

-   **Dashboard**: View system status (Plex connection, Discord bot status, notifications)
-   **Notification History**: See recent notifications from Sonarr/Radarr
-   **Manual Plex Scan**: Trigger Plex library scans on demand
-   **Settings**: View current configuration
