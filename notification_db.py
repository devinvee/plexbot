"""
Database module for persisting notifications and user notification counts.
Uses SQLite for simplicity and persistence across container restarts.
"""
import sqlite3
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("notifications.db")


def get_db_connection():
    """Get a database connection."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the database with required tables."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Notifications table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_type TEXT NOT NULL,
            title TEXT NOT NULL,
            year INTEGER,
            media_type TEXT,  -- 'episode', 'movie', 'book'
            season_number INTEGER,
            episode_number INTEGER,
            episode_title TEXT,
            quality TEXT,
            poster_url TEXT,
            fanart_url TEXT,
            backdrop_url TEXT,
            timestamp TEXT NOT NULL,
            episode_count INTEGER DEFAULT 1,
            episodes_json TEXT,  -- JSON array of episode details
            created_at TEXT NOT NULL
        )
    """)

    # User notification counts table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_notification_counts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_user_id TEXT NOT NULL,
            plex_username TEXT,
            notification_count INTEGER DEFAULT 0,
            last_notification_at TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(discord_user_id)
        )
    """)

    # User notifications junction table (tracks which users were notified for each notification)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_id INTEGER NOT NULL,
            discord_user_id TEXT NOT NULL,
            plex_username TEXT,
            notified_at TEXT NOT NULL,
            FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE,
            UNIQUE(notification_id, discord_user_id)
        )
    """)

    # Create indexes for better query performance
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_notifications_timestamp 
        ON notifications(timestamp DESC)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_notifications_user_id 
        ON user_notifications(discord_user_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_notifications_notification_id 
        ON user_notifications(notification_id)
    """)

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")


def save_notification(
    notification_type: str,
    title: str,
    year: Optional[int] = None,
    media_type: str = "episode",
    season_number: Optional[int] = None,
    episode_number: Optional[int] = None,
    episode_title: Optional[str] = None,
    quality: Optional[str] = None,
    poster_url: Optional[str] = None,
    fanart_url: Optional[str] = None,
    backdrop_url: Optional[str] = None,
    episodes: Optional[List[Dict[str, Any]]] = None,
    notified_user_ids: Optional[List[str]] = None,
    user_mappings: Optional[Dict[str, str]] = None
) -> int:
    """
    Save a notification to the database.
    
    Returns:
        The notification ID
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    timestamp = datetime.now().isoformat()
    episode_count = len(episodes) if episodes else 1
    episodes_json = json.dumps(episodes) if episodes else None

    cursor.execute("""
        INSERT INTO notifications (
            notification_type, title, year, media_type,
            season_number, episode_number, episode_title,
            quality, poster_url, fanart_url, backdrop_url,
            timestamp, episode_count, episodes_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        notification_type, title, year, media_type,
        season_number, episode_number, episode_title,
        quality, poster_url, fanart_url, backdrop_url,
        timestamp, episode_count, episodes_json, timestamp
    ))

    notification_id = cursor.lastrowid

    # Track which users were notified
    if notified_user_ids:
        user_mappings = user_mappings or {}
        for user_id in notified_user_ids:
            # Get Plex username from mapping if available
            plex_username = None
            for plex_user, discord_id in user_mappings.items():
                if discord_id == user_id:
                    plex_username = plex_user
                    break

            cursor.execute("""
                INSERT INTO user_notifications (
                    notification_id, discord_user_id, plex_username, notified_at
                ) VALUES (?, ?, ?, ?)
            """, (notification_id, user_id, plex_username, timestamp))

            # Update user notification count
            cursor.execute("""
                INSERT INTO user_notification_counts (
                    discord_user_id, plex_username, notification_count,
                    last_notification_at, updated_at
                ) VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(discord_user_id) DO UPDATE SET
                    notification_count = notification_count + 1,
                    last_notification_at = ?,
                    updated_at = ?,
                    plex_username = COALESCE(?, plex_username)
            """, (
                user_id, plex_username, timestamp, timestamp,
                timestamp, timestamp, plex_username
            ))

    conn.commit()
    conn.close()
    return notification_id


def get_recent_notifications(hours: int = 24, limit: int = 100) -> List[Dict[str, Any]]:
    """Get recent notifications from the database."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

    cursor.execute("""
        SELECT * FROM notifications
        WHERE timestamp > ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (cutoff, limit))

    notifications = []
    for row in cursor.fetchall():
        notif = dict(row)
        # Parse episodes JSON if present
        if notif.get('episodes_json'):
            try:
                notif['episodes'] = json.loads(notif['episodes_json'])
            except:
                notif['episodes'] = []
        else:
            # Create episode object from single episode fields
            if notif.get('season_number') and notif.get('episode_number'):
                notif['episode'] = {
                    'season': notif['season_number'],
                    'number': notif['episode_number'],
                    'title': notif.get('episode_title', '')
                }
        notifications.append(notif)

    conn.close()
    return notifications


def get_user_notification_counts() -> List[Dict[str, Any]]:
    """Get notification counts for all users."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 
            discord_user_id,
            plex_username,
            notification_count,
            last_notification_at
        FROM user_notification_counts
        ORDER BY notification_count DESC, last_notification_at DESC
    """)

    users = []
    for row in cursor.fetchall():
        users.append(dict(row))

    conn.close()
    return users


def get_user_notification_count(discord_user_id: str) -> int:
    """Get notification count for a specific user."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT notification_count
        FROM user_notification_counts
        WHERE discord_user_id = ?
    """, (discord_user_id,))

    row = cursor.fetchone()
    count = row['notification_count'] if row else 0

    conn.close()
    return count


def get_user_recent_notifications(discord_user_id: str, hours: int = 24, limit: int = 50) -> List[Dict[str, Any]]:
    """Get recent notifications for a specific user."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

    cursor.execute("""
        SELECT n.*
        FROM notifications n
        INNER JOIN user_notifications un ON n.id = un.notification_id
        WHERE un.discord_user_id = ? AND n.timestamp > ?
        ORDER BY n.timestamp DESC
        LIMIT ?
    """, (discord_user_id, cutoff, limit))

    notifications = []
    for row in cursor.fetchall():
        notif = dict(row)
        if notif.get('episodes_json'):
            try:
                notif['episodes'] = json.loads(notif['episodes_json'])
            except:
                notif['episodes'] = []
        notifications.append(notif)

    conn.close()
    return notifications

