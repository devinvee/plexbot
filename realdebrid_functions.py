import discord
from discord.ext import tasks
import os
import requests
import json
import logging
from datetime import datetime, timedelta

# Get a named logger for this module
logger = logging.getLogger(__name__)

# Environment variables for this module
# It's good practice to fetch environment variables once at module level if they are constant.
API_KEY = os.environ.get("REALDEBRID_API_KEY")


@tasks.loop(hours=24)
async def check_premium_expiry(bot, channel_id: int):
    """Periodically checks Real-Debrid premium status and sends a notification if expiring soon."""
    logger.info("RealDebrid: Starting premium expiry check task.")  # Info level
    await bot.wait_until_ready()  # Ensure bot is ready before trying to get channel

    channel = bot.get_channel(channel_id)
    if not channel:
        # Warning level
        logger.warning(
            f"RealDebrid: Could not find notification channel with ID: {channel_id}. Cannot send expiry notifications.")
        return

    if not API_KEY:
        # Error level
        logger.error(
            "RealDebrid: REALDEBRID_API_KEY is not set. Cannot perform premium expiry check.")
        await channel.send("Error: Real-Debrid API key not configured. Cannot check premium expiry.")
        return

    url = "https://api.real-debrid.com/rest/1.0/user"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        # This might not be strictly needed for GET, but harmless
        "Content-Type": "application/json"
    }
    # Debug level
    logger.debug(
        f"RealDebrid: Fetching user info from {url} for premium expiry check.")

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raises HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        # Debug level
        logger.debug(f"RealDebrid: API response for premium expiry: {data}")

        if data and data.get('premium'):
            premium_until_str = data.get('premium_until')
            if premium_until_str:
                # Real-Debrid's premium_until can be 'YYYY-MM-DD'. datetime.fromisoformat handles this.
                # Adding .replace('Z', '+00:00') for full ISO 8601 compliance if it ever sends Z.
                expiry_date = datetime.fromisoformat(
                    premium_until_str.replace('Z', '+00:00'))
                today = datetime.utcnow()
                time_difference = expiry_date - today
                # Info level
                logger.info(
                    f"RealDebrid: Premium expires on {expiry_date.strftime('%Y-%m-%d')}, {time_difference.days} days left.")

                if 0 < time_difference.days <= 90:  # Notify if 90 days or less remaining
                    # Info level
                    logger.info(
                        f"RealDebrid: Sending premium expiry warning for {time_difference.days} days left.")
                    await channel.send(f"⚠️ Your Real-Debrid premium is expiring in **{time_difference.days} days**! (Expires: {expiry_date.strftime('%Y-%m-%d')})")
                # Note: This also catches already expired. If it's today (0 days), it's also expired.
                elif time_difference.days <= 0:
                    # Warning level
                    logger.warning(
                        "RealDebrid: Sending premium expired notification.")
                    await channel.send("🔴 Your Real-Debrid premium has expired! Please renew.")
                else:
                    # Info level
                    logger.info(
                        f"RealDebrid: Premium is still far out ({time_difference.days} days). No notification sent.")
            else:
                # Warning level
                logger.warning(
                    "RealDebrid: 'premium_until' date not found in API response despite 'premium' being true. Cannot determine expiry.")
        elif not data.get('premium'):
            # Info level
            logger.info(
                "RealDebrid: Account is not premium. No expiry date to track.")

    except requests.exceptions.RequestException as e:
        # Error level, include traceback
        logger.error(
            f"RealDebrid: Network or API error checking Real-Debrid status for expiry: {e}", exc_info=True)
    except json.JSONDecodeError:
        # Error level
        logger.error(
            "RealDebrid: Error decoding Real-Debrid API response during expiry check. Response was not valid JSON.", exc_info=True)
    except Exception as e:
        # Critical level for unexpected errors
        logger.critical(
            f"RealDebrid: An unexpected critical error occurred during premium expiry check: {e}", exc_info=True)


async def send_realdebrid_startup_status(channel):
    """Fetches and sends the Real-Debrid status as an embed on bot startup."""
    logger.info(
        "RealDebrid: Attempting to send Real-Debrid startup status to channel.")  # Info level

    if not API_KEY:
        # Error level
        logger.error(
            "RealDebrid: REALDEBRID_API_KEY is not set. Cannot send startup status.")
        await channel.send("Error: Real-Debrid API key not configured. Cannot send startup status.")
        return

    url = "https://api.real-debrid.com/rest/1.0/user"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    # Debug level
    logger.debug(
        f"RealDebrid: Fetching user info from {url} for startup status.")

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        # Debug level
        logger.debug(f"RealDebrid: API response for startup status: {data}")

        if data:
            # Real-Debrid's 'expiration' is a Unix timestamp (integer string)
            # 'premium_until' is an ISO 8601 date string (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)
            expiration_timestamp = data.get('expiration', None)
            premium_until_str = data.get('premium_until', None)
            # 0 for Free, 1 for Premium
            premium_status = data.get('premium', 0) == 1

            embed = discord.Embed(
                title="Real-Debrid Account Status", color=discord.Color.green())
            embed.add_field(name="Username", value=data.get(
                'username', 'N/A'), inline=True)
            embed.add_field(name="Email", value=data.get(
                'email', 'N/A'), inline=True)
            embed.add_field(name="Points", value=data.get(
                'points', 'N/A'), inline=True)

            if expiration_timestamp:
                try:
                    expiration_date = datetime.fromtimestamp(
                        int(expiration_timestamp))
                    embed.add_field(name="Expiration Date (Timestamp)", value=expiration_date.strftime(
                        '%Y-%m-%d %H:%M:%S UTC'), inline=False)
                except ValueError:
                    # Warning level
                    logger.warning(
                        f"RealDebrid: Invalid 'expiration' timestamp format received: {expiration_timestamp}")
                    embed.add_field(name="Expiration Date (Timestamp)",
                                    value="N/A (Invalid Format)", inline=False)
            else:
                embed.add_field(name="Expiration Date (Timestamp)",
                                value="N/A", inline=False)

            embed.add_field(name="Premium Status",
                            value="Yes" if premium_status else "No", inline=True)
            if premium_status and premium_until_str:
                try:
                    premium_until_date = datetime.fromisoformat(
                        premium_until_str.replace('Z', '+00:00'))
                    embed.add_field(name="Premium Until", value=premium_until_date.strftime(
                        '%Y-%m-%d %H:%M:%S UTC'), inline=True)
                except ValueError:
                    # Warning level
                    logger.warning(
                        f"RealDebrid: Invalid 'premium_until' date format received: {premium_until_str}")
                    embed.add_field(name="Premium Until",
                                    value="N/A (Invalid Format)", inline=True)
            elif premium_status:
                embed.add_field(name="Premium Until",
                                value="N/A (Not provided)", inline=True)

            await channel.send(embed=embed)
            # Info level
            logger.info("RealDebrid: Sent startup status embed successfully.")
        else:
            # Warning level
            logger.warning(
                "RealDebrid: Could not retrieve Real-Debrid account information from API response on startup.")
            await channel.send("Could not retrieve Real-Debrid account information on startup.")

    except requests.exceptions.RequestException as e:
        # Error level
        logger.error(
            f"RealDebrid: Network or API error checking Real-Debrid status on startup: {e}", exc_info=True)
        await channel.send(f"Error checking Real-Debrid status on startup: {e}")
    except json.JSONDecodeError:
        # Error level
        logger.error(
            "RealDebrid: Error decoding Real-Debrid API response on startup. Response was not valid JSON.", exc_info=True)
        await channel.send("Error decoding Real-Debrid API response on startup.")
    except Exception as e:
        # Critical level
        logger.critical(
            f"RealDebrid: An unexpected critical error occurred on startup status check: {e}", exc_info=True)
        await channel.send(f"An unexpected error occurred on startup status check: {e}")

# This function will be assigned to a command later in bot.py


async def realdebrid_status_command(interaction: discord.Interaction):
    """Checks and displays the Real-Debrid account status in response to a Discord command."""
    logger.info(
        # Info level
        f"RealDebrid: Received /realdebrid command from user {interaction.user.id}.")
    # Defer publicly as command output might be long
    await interaction.response.defer(ephemeral=False)

    if not API_KEY:
        # Error level
        logger.error(
            "RealDebrid: REALDEBRID_API_KEY is not set for command execution.")
        # Ephemeral for sensitive config error
        await interaction.followup.send("Error: Real-Debrid API key not configured.", ephemeral=True)
        return

    url = "https://api.real-debrid.com/rest/1.0/user"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    # Debug level
    logger.debug(
        f"RealDebrid: Fetching user info from {url} for command status.")

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        # Debug level
        logger.debug(f"RealDebrid: API response for command status: {data}")

        if data:
            expiration_timestamp = data.get('expiration', None)
            premium_until_str = data.get('premium_until', None)
            premium_status = data.get('premium', 0) == 1

            embed = discord.Embed(
                title="Real-Debrid Account Status", color=discord.Color.green())
            embed.add_field(name="Username", value=data.get(
                'username', 'N/A'), inline=True)
            embed.add_field(name="Email", value=data.get(
                'email', 'N/A'), inline=True)
            embed.add_field(name="Points", value=data.get(
                'points', 'N/A'), inline=True)

            if expiration_timestamp:
                try:
                    expiration_date = datetime.fromtimestamp(
                        int(expiration_timestamp))
                    embed.add_field(name="Expiration Date (Timestamp)", value=expiration_date.strftime(
                        '%Y-%m-%d %H:%M:%S UTC'), inline=False)
                except ValueError:
                    # Warning level
                    logger.warning(
                        f"RealDebrid: Invalid 'expiration' timestamp format received for command: {expiration_timestamp}")
                    embed.add_field(name="Expiration Date (Timestamp)",
                                    value="N/A (Invalid Format)", inline=False)
            else:
                embed.add_field(name="Expiration Date (Timestamp)",
                                value="N/A", inline=False)

            embed.add_field(name="Premium Status",
                            value="Yes" if premium_status else "No", inline=True)
            if premium_status and premium_until_str:
                try:
                    premium_until_date = datetime.fromisoformat(
                        premium_until_str.replace('Z', '+00:00'))
                    embed.add_field(name="Premium Until", value=premium_until_date.strftime(
                        '%Y-%m-%d %H:%M:%S UTC'), inline=True)
                except ValueError:
                    # Warning level
                    logger.warning(
                        f"RealDebrid: Invalid 'premium_until' date format received for command: {premium_until_str}")
                    embed.add_field(name="Premium Until",
                                    value="N/A (Invalid Format)", inline=True)
            elif premium_status:
                embed.add_field(name="Premium Until",
                                value="N/A (Not provided)", inline=True)

            await interaction.followup.send(embed=embed)
            # Info level
            logger.info(
                f"RealDebrid: Sent /realdebrid command response to user {interaction.user.id}.")
        else:
            # Warning level
            logger.warning(
                "RealDebrid: Could not retrieve Real-Debrid account information from API response for command.")
            await interaction.followup.send("Could not retrieve Real-Debrid account information.")

    except requests.exceptions.RequestException as e:
        # Error level
        logger.error(
            f"RealDebrid: Network or API error checking Real-Debrid status for command: {e}", exc_info=True)
        await interaction.followup.send(f"Error checking Real-Debrid status: {e}")
    except json.JSONDecodeError:
        # Error level
        logger.error(
            "RealDebrid: Error decoding Real-Debrid API response for command. Response was not valid JSON.", exc_info=True)
        await interaction.followup.send("Error decoding Real-Debrid API response.")
    except Exception as e:
        # Critical level
        logger.critical(
            f"RealDebrid: An unexpected critical error occurred for command: {e}", exc_info=True)
        await interaction.followup.send(f"An unexpected error occurred: {e}")
