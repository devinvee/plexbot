# PlexBot: Your All-in-One Discord Media Server Assistant

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

PlexBot is a powerful, multifaceted Discord bot designed to be the central hub for managing your entire media server ecosystem. It seamlessly integrates with Plex, Docker, Real-Debrid, and the \*Arr suite (Sonarr, Radarr) to bring server management, status monitoring, and user administration right into your Discord server.

## ✨ Key Features

PlexBot is packed with features to streamline your media server management:

#### 🐳 Docker & System Management

-   **Container Status:** Quickly check the status of your Plex container (`/plexstatus`).
-   **Container Restarts:** Restart the Plex container or the entire Docker stack directly from Discord (`/restartplex`, `/restartcontainers`).
-   **System Health:** Monitor the health of the bot and its services with a dedicated health check command.

#### 🗃️ Plex Integration

-   **Library Access Control:** Allow users to select which Plex libraries they are interested in, helping you manage access and notifications (`/plexaccess`).

#### 🧲 Real-Debrid Account Management

-   **Account Status:** Check your Real-Debrid account details, including premium status and expiration (`/realdebrid`).
-   **Expiry Notifications:** Automatically receive a warning in a designated channel when your Real-Debrid subscription is about to expire.

#### 🔔 \*Arr Suite & Overseerr Integration

-   **Automated Notifications:** Receive webhook notifications from Sonarr, Radarr, and Readarr to report on new media with rich embeds.
-   **Batched Notifications:** Episodes are automatically batched (60-second debounce) to avoid spam when multiple episodes are imported.
-   **Automatic Plex Scanning:** Plex libraries are automatically scanned when batched notifications are sent (replacing per-episode autoscan).
-   **Multi-Instance Support:** Configure multiple Sonarr instances (e.g., for 4K content or anime) in the `config.json` file.

#### 🌐 Web Dashboard

-   **Modern Web UI:** Beautiful, modal-based React interface for managing your PlexBot instance.
-   **System Status:** Real-time monitoring of Plex connection, Discord bot status, and notification history.
-   **Manual Controls:** Trigger Plex library scans manually from the web interface.
-   **Notification History:** View recent notifications from the last 24 hours.

#### 👤 User Management

-   **Automated Role Assignment:** Greet new users and automatically assign them a specific role.
-   **Easy Invite System:** Integrates with services like Wizarr to provide and manage user invites.

---

## 🚀 Getting Started

Follow these steps to get PlexBot up and running on your server.

### 1. Prerequisites

-   Python 3.10+
-   Docker and Docker Compose
-   A Discord Bot application created on the [Discord Developer Portal](https://discord.com/developers/applications)

### 2. Installation

Clone the repository to your local machine:

```bash
git clone https://github.com/your-username/Plexbot.git
cd Plexbot
```

### 3. Setup

It is highly recommended to use a Python virtual environment.

```bash
python -m venv venv
source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
```

Install the required dependencies:

```bash
pip install -r requirements.txt
```

### 4. Configuration

PlexBot uses two primary configuration files. Sample files are provided to get you started.

**A. Environment Variables (`.env`)**

Copy the sample file:

```bash
cp sample.env .env
```

Now, edit the `.env` file and fill in your details. This file holds your secret keys and basic connection info. See the comments in the `sample.env` file for guidance on each variable.

**B. JSON Configuration (`config.json`)**

Copy the sample configuration:

```bash
cp config.json.sample config.json
```

Edit `config.json` to configure the bot's features, like enabling/disabling modules, setting up Sonarr instances, and mapping users.

**C. Build Web UI (Optional)**

If you want to use the web dashboard, build the React frontend:

```bash
./build_webui.sh
```

Or manually:

```bash
cd webui
npm install
npm run build
```

### 5. Running the Bot

Once configured, you can start the bot with:

```bash
python bot.py
```

For a more robust setup, it is recommended to run the bot using the provided `docker-compose.yml` file after building the Docker image.

---

## 🤖 Usage

Here are some of the primary commands you can use with PlexBot:

-   `/plexstatus` - Checks the status of the Plex Docker container.
-   `/restartplex` - Initiates a restart of the Plex container.
-   `/restartcontainers` - Restarts the entire container stack defined in your environment file.
-   `/realdebrid` - Fetches and displays your Real-Debrid account status.
-   `/plexaccess` - Allows users to self-select the Plex libraries they're interested in.

### Web Dashboard

Once the bot is running, access the web dashboard at `http://localhost:5000` (or your server's IP address). The dashboard provides:

-   Real-time system status monitoring
-   Recent notification history
-   Manual Plex library scanning
-   Configuration overview

---

## ⚖️ License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
