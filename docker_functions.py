import discord
import paramiko
import os
import asyncio
import logging

# Environment variables for this module
DOCKER_SERVER_IP = os.environ.get("DOCKER_SERVER_IP")
DOCKER_SERVER_USER = os.environ.get("DOCKER_SERVER_USER")
DOCKER_SERVER_PASSWORD = os.environ.get("DOCKER_SERVER_PASSWORD")
CONTAINER_NAMES = os.environ.get("CONTAINER_NAMES").split(
    ",") if os.environ.get("CONTAINER_NAMES") else []
RESTART_ORDER = os.environ.get("RESTART_ORDER").split(
    ",") if os.environ.get("RESTART_ORDER") else []

# Centralized SSH client connection logic


async def get_ssh_client():
    """Establishes and returns an SSH client connection asynchronously."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        await asyncio.to_thread(
            ssh.connect,
            DOCKER_SERVER_IP,
            username=DOCKER_SERVER_USER,
            password=DOCKER_SERVER_PASSWORD  # Consider using SSH keys for better security
        )
        return ssh
    except Exception as e:
        logging.error(f"Docker: Failed to establish SSH connection: {e}")
        return None


async def check_container_health(ssh, container):
    """Checks if a container is running and healthy (asynchronous)."""
    if not ssh:
        return False  # If no SSH connection, cannot check
    logging.info(f"Docker: Checking health of {container}...")

    # Check health status
    command_health = f"docker inspect --format='{{{{.State.Health.Status}}}}' {container}"
    stdin, stdout, stderr = await asyncio.to_thread(ssh.exec_command, command_health)
    health_status = (await asyncio.to_thread(stdout.read)).decode().strip()
    error_output = (await asyncio.to_thread(stderr.read)).decode().strip()
    if error_output:
        logging.warning(
            f"Docker: Error checking health of {container}: {error_output}")
        return False

    # Check running status
    command_running = f"docker inspect --format='{{{{.State.Running}}}}' {container}"
    stdin, stdout, stderr = await asyncio.to_thread(ssh.exec_command, command_running)
    running_status = (await asyncio.to_thread(stdout.read)).decode().strip()
    error_output = (await asyncio.to_thread(stderr.read)).decode().strip()
    if error_output:
        logging.warning(
            f"Docker: Error checking running status of {container}: {error_output}")
        return False

    logging.info(
        f"Docker: {container} - Running: {running_status}, Health: {health_status}")

    # A container is considered 'healthy' if it's running AND (its health check is 'healthy' or it has no health check defined)
    return running_status == "true" and (health_status == "healthy" or health_status == "")


async def restart_containers_logic(container_list):
    """Handles the core logic for stopping, starting, and health-checking containers."""
    ssh = await get_ssh_client()
    if not ssh:
        return False

    try:
        for container in container_list:
            logging.info(f"Docker: Attempting to stop {container}...")
            stop_command = f"docker stop {container}"
            stdin, stdout, stderr = await asyncio.to_thread(ssh.exec_command, stop_command)
            stderr_output = (await asyncio.to_thread(stderr.read)).decode().strip()
            # Handle case where container is already stopped/doesn't exist
            if stderr_output and "No such container" not in stderr_output:
                logging.error(
                    f"Docker: Error stopping {container}: {stderr_output}")

            logging.info(f"Docker: Attempting to start {container}...")
            start_command = f"docker start {container}"
            stdin, stdout, stderr = await asyncio.to_thread(ssh.exec_command, start_command)
            stderr_output = (await asyncio.to_thread(stderr.read)).decode().strip()
            if stderr_output:
                logging.error(
                    f"Docker: Error starting {container}: {stderr_output}")
                return False  # Critical error if start fails

            # Wait for container to become healthy
            max_attempts = 60  # 60 attempts * 10 seconds = 10 minutes timeout
            attempts = 0
            while attempts < max_attempts:
                logging.info(
                    f"Docker: Checking health of {container}, attempt {attempts + 1}/{max_attempts}...")
                if await check_container_health(ssh, container):
                    logging.info(f"Docker: {container} is healthy!")
                    break
                await asyncio.sleep(10)  # Wait 10 seconds between checks
                attempts += 1

            if attempts == max_attempts:
                logging.error(
                    f"Docker: Container {container} did not become healthy in time.")
                return False

        return True

    except Exception as e:
        logging.error(
            f"Docker: Restart failed for container list {container_list}: {e}")
        return False
    finally:
        if ssh:
            # Ensure SSH connection is closed
            await asyncio.to_thread(ssh.close)


def check_docker_containers_sync():
    """Synchronous function to check container statuses for commands."""
    ssh = None
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            DOCKER_SERVER_IP,
            username=DOCKER_SERVER_USER,
            password=DOCKER_SERVER_PASSWORD
        )

        container_statuses = {}
        for container in CONTAINER_NAMES:
            # Check health status
            command_health = f"docker inspect --format='{{{{.State.Health.Status}}}}' {container}"
            stdin, stdout, stderr = ssh.exec_command(command_health)
            health_status = stdout.read().decode().strip()
            error_output = stderr.read().decode().strip()
            if error_output and "No such container" not in error_output:
                logging.warning(
                    f"Docker: Error inspecting health of {container}: {error_output}")
                health_status = "unknown"  # Indicate inspection failure

            # Check running status
            command_running = f"docker inspect --format='{{{{.State.Running}}}}' {container}"
            stdin, stdout, stderr = ssh.exec_command(command_running)
            running_status = stdout.read().decode().strip()
            error_output = stderr.read().decode().strip()
            if error_output and "No such container" not in error_output:
                logging.warning(
                    f"Docker: Error inspecting running status of {container}: {error_output}")
                running_status = "false"  # Assume not running if inspect fails

            # A container is considered 'healthy' if it's running AND (its health check is 'healthy' or it has no health check defined)
            container_statuses[container] = {
                "health": health_status,
                "running": running_status == "true"
            }

        return container_statuses

    except Exception as e:
        logging.error(f"Docker: Docker check failed: {e}")
        return None
    finally:
        if ssh:
            ssh.close()

# These functions will be assigned to commands later in bot.py


async def plex_status_command(interaction: discord.Interaction):
    """Check Plex Docker container status."""
    statuses = await asyncio.to_thread(check_docker_containers_sync)

    if statuses is None:
        await interaction.response.send_message("Failed to check Docker status. Check bot logs for SSH errors.")
        return

    broken_containers = []
    for container, status in statuses.items():
        if not status["running"] or (status["health"] not in ["healthy", ""] and status["health"] != "starting"):
            broken_containers.append(
                f"**{container}** (Running: {status['running']}, Health: {status['health'] if status['health'] else 'N/A'})"
            )

    # FIX: Corrected f-string usage for newlines
    if broken_containers:
        newline = '\n'  # Define newline character outside the f-string expression
        await interaction.response.send_message(f"Problem containers detected:{newline}{newline.join(broken_containers)}")
    else:
        await interaction.response.send_message("All monitored Docker containers are running and healthy! ✅")


async def restart_containers_command(interaction: discord.Interaction):
    """Restart specified Docker containers in order."""
    await interaction.response.send_message("Initiating container restart sequence...")
    success = await restart_containers_logic(RESTART_ORDER)

    if success:
        await interaction.followup.send("All specified containers have been restarted and are healthy! Enjoy! 🎉")
    else:
        await interaction.followup.send("Failed to restart one or more containers. Check bot logs for details. ❌")


async def restart_plex_command(interaction: discord.Interaction):
    """Restart just the 'plex' container."""
    await interaction.response.send_message("Initiating Plex container restart...")
    success = await restart_containers_logic(['plex'])  # Restart only 'plex'

    if success:
        await interaction.followup.send("Plex container has been restarted and is healthy! Enjoy! 🎉")
    else:
        await interaction.followup.send("Failed to restart Plex container. Check bot logs for details. ❌")
