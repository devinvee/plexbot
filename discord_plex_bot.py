import discord
from discord.ext import commands, tasks
import paramiko
import os
import asyncio
import logging
import json
import requests
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
DOCKER_SERVER_IP = os.environ.get("DOCKER_SERVER_IP")
DOCKER_SERVER_USER = os.environ.get("DOCKER_SERVER_USER")
DOCKER_SERVER_PASSWORD = os.environ.get("DOCKER_SERVER_PASSWORD")
CONTAINER_NAMES = os.environ.get("CONTAINER_NAMES").split(",")
RESTART_ORDER = os.environ.get("RESTART_ORDER").split(",")
API_KEY = os.environ.get("REALDEBRID_API_KEY")
channel_id = 1315715339283333232  # Ensure this is the correct channel ID

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)


@tasks.loop(hours=24)
async def check_premium_expiry():
    """Periodically checks Real-Debrid premium status and sends a notification if expiring soon."""

    channel = bot.get_channel(channel_id)
    if not channel:
        logging.warning(f"Could not find channel with ID: {channel_id}")
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
                    "Real-Debrid premium_until date not found in API response.")
        elif not data.get('premium'):
            logging.info("Real-Debrid account is not premium.")

    except requests.exceptions.RequestException as e:
        logging.error(f"Error checking Real-Debrid status for expiry: {e}")
    except json.JSONDecodeError:
        logging.error(
            "Error decoding Real-Debrid API response for expiry check.")
    except Exception as e:
        logging.error(
            f"An unexpected error occurred during premium expiry check: {e}")


async def check_container_health(ssh, container):
    """Checks if a container is running and healthy (asynchronous)."""
    logging.info(f"Checking health of {container}...")
    command = f"docker inspect --format='{{{{.State.Health.Status}}}}' {container}"
    stdin, stdout, stderr = await asyncio.to_thread(ssh.exec_command, command)
    health_status = (await asyncio.to_thread(stdout.read)).decode().strip()

    command = f"docker inspect --format='{{{{.State.Running}}}}' {container}"
    stdin, stdout, stderr = await asyncio.to_thread(ssh.exec_command, command)
    running_status = (await asyncio.to_thread(stdout.read)).decode().strip()

    logging.info(
        f"  {container} - Running: {running_status}, Health: {health_status}")

    # Check if the container is running AND (healthy OR no health check defined)
    return running_status == "true" and (health_status == "healthy" or health_status == "")


async def restart_containers(restart_order):
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        await asyncio.to_thread(ssh.connect, DOCKER_SERVER_IP, username=DOCKER_SERVER_USER, password=DOCKER_SERVER_PASSWORD)

        for container in restart_order:
            # Stop the container
            stop_command = f"docker stop {container}"
            stdin, stdout, stderr = await asyncio.to_thread(ssh.exec_command, stop_command)
            stderr_output = (await asyncio.to_thread(stderr.read)).decode().strip()
            if stderr_output:
                logging.error(f"Error stopping {container}: {stderr_output}")

            # Start the container
            start_command = f"docker start {container}"
            stdin, stdout, stderr = await asyncio.to_thread(ssh.exec_command, start_command)
            stderr_output = (await asyncio.to_thread(stderr.read)).decode().strip()
            if stderr_output:
                logging.error(f"Error starting {container}: {stderr_output}")

            # Wait for container to become healthy
            max_attempts = 300
            attempts = 0
            while attempts < max_attempts:
                logging.info(
                    f"Checking health of {container}, attempt {attempts + 1}/{max_attempts}")
                if await check_container_health(ssh, container):
                    logging.info(f"{container} is healthy!")
                    break
                await asyncio.sleep(10)
                attempts += 1

            if attempts == max_attempts:
                logging.error(
                    f"Container {container} did not become healthy in time.")
                await asyncio.to_thread(ssh.close)
                return False

        await asyncio.to_thread(ssh.close)
        return True

    except Exception as e:
        logging.error(f"Restart failed: {e}")
        return False


def check_docker_containers(container_names):
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(DOCKER_SERVER_IP, username=DOCKER_SERVER_USER,
                    password=DOCKER_SERVER_PASSWORD)

        container_statuses = {}
        for container in container_names:
            command = f"docker inspect --format='{{{{.State.Health.Status}}}}' {container}"
            stdin, stdout, stderr = ssh.exec_command(command)
            health_status = stdout.read().decode().strip()

            command = f"docker inspect --format='{{{{.State.Running}}}}' {container}"
            stdin, stdout, stderr = ssh.exec_command(command)
            running_status = stdout.read().decode().strip()

            # Check if the container is running and (healthy OR no health check defined)
            container_statuses[container] = {
                "health": health_status, "running": running_status == "true"}

        ssh.close()
        return container_statuses

    except Exception as e:
        logging.error(f"Docker check failed: {e}")
        return None


async def send_realdebrid_startup_status(channel):
    """Fetches and sends the Real-Debrid status as an embed."""
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
                    expiration_date = datetime.fromtimestamp(
                        int(expiration))
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
        logging.error(f"Error checking Real-Debrid status on startup: {e}")
        await channel.send(f"Error checking Real-Debrid status on startup: {e}")
    except json.JSONDecodeError:
        logging.error("Error decoding Real-Debrid API response on startup.")
        await channel.send("Error decoding Real-Debrid API response on startup.")
    except Exception as e:
        logging.error(
            f"An unexpected error occurred on startup status check: {e}")
        await channel.send(f"An unexpected error occurred on startup status check: {e}")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(e)

# Send a startup message and then the Real-Debrid status
    startup_channel = bot.get_channel(channel_id)
    if startup_channel:
        await startup_channel.send("👋 Bot is online and ready!")
        # Call the new function to send Real-Debrid status
        await send_realdebrid_startup_status(startup_channel)
    else:
        logging.warning(
            # Corrected line
            f"Could not find startup channel with ID: {channel_id}")

    check_premium_expiry.start()


@bot.tree.command(name="plexstatus", description="Check Plex Docker container status.")
async def plex_status(interaction: discord.Interaction):
    statuses = check_docker_containers(CONTAINER_NAMES)
    if statuses is None:
        await interaction.response.send_message("Failed to check Docker status.")
        return

    broken_containers = []
    for container, status in statuses.items():
        if not status["running"] or (status["health"] != "healthy" and status["health"] != ""):
            broken_containers.append(
                f"{container} (Running: {status['running']}, Health: {status['health']})")

    if broken_containers:
        await interaction.response.send_message(f"Problem containers: {', '.join(broken_containers)}")
    else:
        await interaction.response.send_message("Everything looks good!")


async def restart_plex():
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        await asyncio.to_thread(ssh.connect, DOCKER_SERVER_IP, username=DOCKER_SERVER_USER, password=DOCKER_SERVER_PASSWORD)

        stop_command = f"docker stop plex"
        stdin, stdout, stderr = await asyncio.to_thread(ssh.exec_command, stop_command)
        stderr_output = (await asyncio.to_thread(stderr.read)).decode().strip()
        if stderr_output:
            logging.error(f"Error stopping Plex: {stderr_output}")

            # Start the container
        start_command = f"docker start plex"
        stdin, stdout, stderr = await asyncio.to_thread(ssh.exec_command, start_command)
        stderr_output = (await asyncio.to_thread(stderr.read)).decode().strip()
        if stderr_output:
            logging.error(f"Error starting plex: {stderr_output}")

        # Wait for container to become healthy
        max_attempts = 300
        attempts = 0
        while attempts < max_attempts:
            logging.info(
                f"Checking health of Plex, attempt {attempts + 1}/{max_attempts}")
            if await check_container_health(ssh, 'plex'):
                logging.info(f"Plex is healthy!")
                break
            await asyncio.sleep(10)
            attempts += 1

        if attempts == max_attempts:
            logging.error(
                f"Container Plex did not become healthy in time.")
            await asyncio.to_thread(ssh.close)
            return False

        await asyncio.to_thread(ssh.close)
        return True

    except Exception as e:
        logging.error(f"Restart failed: {e}")
        return False


async def restart_containers_async(interaction: discord.Interaction, restart_order):
    """Asynchronous task to restart containers."""
    await interaction.response.send_message("Containers are restarting...")

    if await restart_containers(restart_order):
        await interaction.followup.send("All containers have been restarted! Enjoy!")
    else:
        await interaction.followup.send("Failed to restart containers.")


async def restart_plex_async(interaction: discord.Interaction):
    """Asynchronous task to restart containers."""
    await interaction.response.send_message("Container is restarting...")

    if await restart_plex():
        await interaction.followup.send("Plex container has been restarted! Enjoy!")
    else:
        await interaction.followup.send("Failed to restart container.")


@bot.tree.command(name="restartcontainers", description="Restart Plex Docker containers.")
async def restart_containers_command(interaction: discord.Interaction):
    asyncio.create_task(restart_containers_async(interaction, RESTART_ORDER))


@bot.tree.command(name="restartplex", description="Restart Plex container.")
async def restart_plex_command(interaction: discord.Interaction):
    asyncio.create_task(restart_plex_async(interaction))


@bot.tree.command(name="realdebrid", description="Check your Real-Debrid account status.")
async def realdebrid_status(interaction: discord.Interaction):
    """Checks and displays the Real-Debrid account status."""
    await interaction.response.defer()  # Tell Discord we're working on it

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
                    expiration_date = datetime.fromtimestamp(
                        int(expiration))
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
        await interaction.followup.send(f"Error checking Real-Debrid status: {e}")
    except json.JSONDecodeError:
        await interaction.followup.send("Error decoding Real-Debrid API response.")


@bot.event
async def on_close():
    check_premium_expiry.cancel()

bot.run(DISCORD_TOKEN)
