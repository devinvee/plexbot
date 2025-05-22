import discord
from discord.ext import tasks
import os
import requests
import json
import logging
from datetime import datetime, timedelta

# Environment variables for this module
API_KEY = os.environ.get("REALDEBRID_API_KEY")


@tasks.loop(hours=24)
async def check_premium_expiry(bot, channel_id: int):
    """Periodically checks Real-Debrid premium status and sends a notification if expiring soon."""
    await bot.wait_until_ready()  # Ensure bot is ready before trying to get channel

    channel = bot.get_channel(channel_id)
    if not channel:
        logging.warning(
            f"RealDebrid: Could not find channel with ID: {channel_id}")
        return

    if not API_KEY:
        logging.error("RealDebrid: REALDEBRID_API_KEY is not set.")
        await channel.send("Error: Real-Debrid API key not configured. Cannot check premium expiry.")
        return

    url = "https://api.real-debrid.com/rest/1.0/user"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data and data.get('premium'):
            premium_until_str = data.get('premium_until')
            if premium_until_str:
                expiry_date = datetime.fromisoformat(
                    premium_until_str.replace('Z', '+00:00'))
                today = datetime.utcnow()
                time_difference = expiry_date - today

                if 0 < time_difference.days <= 90:
                    await channel.send(f"⚠️ Your Real-Debrid premium is expiring in {time_difference.days} days!")
                elif time_difference.days < 0:
                    await channel.send("🔴 Your Real-Debrid premium has expired!")
            else:
                logging.info(
                    "RealDebrid: Real-Debrid premium_until date not found in API response.")
        elif not data.get('premium'):
            logging.info("RealDebrid: Real-Debrid account is not premium.")

    except requests.exceptions.RequestException as e:
        logging.error(
            f"RealDebrid: Error checking Real-Debrid status for expiry: {e}")
    except json.JSONDecodeError:
        logging.error(
            "RealDebrid: Error decoding Real-Debrid API response for expiry check.")
    except Exception as e:
        logging.error(
            f"RealDebrid: An unexpected error occurred during premium expiry check: {e}")


async def send_realdebrid_startup_status(channel):
    """Fetches and sends the Real-Debrid status as an embed."""
    if not API_KEY:
        logging.error(
            "RealDebrid: REALDEBRID_API_KEY is not set. Cannot send startup status.")
        await channel.send("Error: Real-Debrid API key not configured. Cannot send startup status.")
        return

    url = "https://api.real-debrid.com/rest/1.0/user"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data:
            expiration = data.get('expiration', None)
            premium_until = data.get('premium_until', None)
            premium = data.get('premium', False)

            embed = discord.Embed(
                title="Real-Debrid Account Status (Startup)", color=discord.Color.green())
            embed.add_field(name="Username", value=data.get(
                'username', 'N/A'), inline=False)
            embed.add_field(name="Email", value=data.get(
                'email', 'N/A'), inline=False)
            embed.add_field(name="Points", value=data.get(
                'points', 'N/A'), inline=False)

            if expiration:
                try:
                    expiration_date = datetime.fromtimestamp(int(expiration))
                    embed.add_field(name="Expiration Date", value=expiration_date.strftime(
                        '%Y-%m-%d %H:%M:%S UTC'), inline=False)
                except ValueError:
                    embed.add_field(name="Expiration Date",
                                    value="N/A (Invalid Format)", inline=False)
            else:
                embed.add_field(name="Expiration Date",
                                value="N/A", inline=False)

            embed.add_field(
                name="Premium", value="Yes" if premium else "No", inline=False)
            if premium and premium_until:
                premium_until_date = datetime.fromisoformat(
                    premium_until.replace('Z', '+00:00'))
                embed.add_field(name="Premium Until", value=premium_until_date.strftime(
                    '%Y-%m-%d %H:%M:%S UTC'), inline=False)
            elif premium:
                embed.add_field(name="Premium Until",
                                value="N/A", inline=False)

            await channel.send(embed=embed)
        else:
            await channel.send("Could not retrieve Real-Debrid account information on startup.")

    except requests.exceptions.RequestException as e:
        logging.error(
            f"RealDebrid: Error checking Real-Debrid status on startup: {e}")
        await channel.send(f"Error checking Real-Debrid status on startup: {e}")
    except json.JSONDecodeError:
        logging.error(
            "RealDebrid: Error decoding Real-Debrid API response on startup.")
        await channel.send("Error decoding Real-Debrid API response on startup.")
    except Exception as e:
        logging.error(
            f"RealDebrid: An unexpected error occurred on startup status check: {e}")
        await channel.send(f"An unexpected error occurred on startup status check: {e}")

# This function will be assigned to a command later in bot.py


async def realdebrid_status_command(interaction: discord.Interaction):
    """Checks and displays the Real-Debrid account status."""
    await interaction.response.defer()

    if not API_KEY:
        logging.error("RealDebrid: REALDEBRID_API_KEY is not set.")
        await interaction.followup.send("Error: Real-Debrid API key not configured.")
        return

    url = "https://api.real-debrid.com/rest/1.0/user"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data:
            expiration = data.get('expiration', None)
            premium_until = data.get('premium_until', None)
            premium = data.get('premium', False)

            embed = discord.Embed(
                title="Real-Debrid Account Status", color=discord.Color.green())
            embed.add_field(name="Username", value=data.get(
                'username', 'N/A'), inline=False)
            embed.add_field(name="Email", value=data.get(
                'email', 'N/A'), inline=False)
            embed.add_field(name="Points", value=data.get(
                'points', 'N/A'), inline=False)

            if expiration:
                try:
                    expiration_date = datetime.fromtimestamp(int(expiration))
                    embed.add_field(name="Expiration Date", value=expiration_date.strftime(
                        '%Y-%m-%d %H:%M:%S UTC'), inline=False)
                except ValueError:
                    embed.add_field(name="Expiration Date",
                                    value="N/A (Invalid Format)", inline=False)
            else:
                embed.add_field(name="Expiration Date",
                                value="N/A", inline=False)

            embed.add_field(
                name="Premium", value="Yes" if premium else "No", inline=False)
            if premium and premium_until:
                premium_until_date = datetime.fromisoformat(
                    premium_until.replace('Z', '+00:00'))
                embed.add_field(name="Premium Until", value=premium_until_date.strftime(
                    '%Y-%m-%d %H:%M:%S UTC'), inline=False)
            elif premium:
                embed.add_field(name="Premium Until",
                                value="N/A", inline=False)

            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("Could not retrieve Real-Debrid account information.")

    except requests.exceptions.RequestException as e:
        logging.error(f"RealDebrid: Error checking Real-Debrid status: {e}")
        await interaction.followup.send(f"Error checking Real-Debrid status: {e}")
    except json.JSONDecodeError:
        logging.error("RealDebrid: Error decoding Real-Debrid API response.")
        await interaction.followup.send("Error decoding Real-Debrid API response.")
    except Exception as e:
        logging.error(f"RealDebrid: An unexpected error occurred: {e}")
        await interaction.followup.send(f"An unexpected error occurred: {e}")
