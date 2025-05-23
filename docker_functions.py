import discord
import paramiko
import os
import asyncio
import logging

# Get a named logger for this module
logger = logging.getLogger(__name__)

# Environment variables for this module
DOCKER_SERVER_IP = os.environ.get("DOCKER_SERVER_IP")
DOCKER_SERVER_USER = os.environ.get("DOCKER_SERVER_USER")
# Consider using SSH keys for better security
DOCKER_SERVER_PASSWORD = os.environ.get("DOCKER_SERVER_PASSWORD")
CONTAINER_NAMES = os.environ.get("CONTAINER_NAMES").split(
    ",") if os.environ.get("CONTAINER_NAMES") else []
RESTART_ORDER = os.environ.get("RESTART_ORDER").split(
    ",") if os.environ.get("RESTART_ORDER") else []

# Log initial configuration values (sensitive info like passwords should be masked/omitted in logs)
logger.info(f"Docker: DOCKER_SERVER_IP: {DOCKER_SERVER_IP}")
logger.info(f"Docker: DOCKER_SERVER_USER: {DOCKER_SERVER_USER}")
logger.info(f"Docker: CONTAINER_NAMES configured: {CONTAINER_NAMES}")
logger.info(f"Docker: RESTART_ORDER configured: {RESTART_ORDER}")

# Centralized SSH client connection logic


async def get_ssh_client():
    """Establishes and returns an SSH client connection asynchronously."""
    if not all([DOCKER_SERVER_IP, DOCKER_SERVER_USER, DOCKER_SERVER_PASSWORD]):
        # Error level
        logger.error(
            "Docker: SSH connection details (IP, User, Password) are not fully set in environment variables.")
        return None

    ssh = paramiko.SSHClient()
    # AutoAddPolicy for convenience, but for production consider WarningPolicy or host key verification
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # Info level
    logger.info(
        f"Docker: Attempting to establish SSH connection to {DOCKER_SERVER_IP} as {DOCKER_SERVER_USER}...")
    try:
        await asyncio.to_thread(
            ssh.connect,
            DOCKER_SERVER_IP,
            username=DOCKER_SERVER_USER,
            password=DOCKER_SERVER_PASSWORD
        )
        # Info level
        logger.info(
            f"Docker: Successfully established SSH connection to {DOCKER_SERVER_IP}.")
        return ssh
    except paramiko.AuthenticationException:
        # Error level
        logger.error(
            f"Docker: SSH Authentication failed for {DOCKER_SERVER_USER}@{DOCKER_SERVER_IP}. Check credentials.")
    except paramiko.SSHException as e:
        # Error level
        logger.error(f"Docker: SSH negotiation failed or other SSH error: {e}")
    except Exception as e:
        # Error level, include traceback for unexpected
        logger.error(
            f"Docker: An unexpected error occurred during SSH connection: {e}", exc_info=True)
    return None


async def check_container_health(ssh, container):
    """Checks if a container is running and healthy (asynchronous)."""
    if not ssh:
        # Warning level
        logger.warning(
            f"Docker: No active SSH connection to check health of {container}. Skipping.")
        return False
    # Info level
    logger.info(
        f"Docker: Checking health and running status of container: {container}...")

    # Check health status
    command_health = f"docker inspect --format='{{{{.State.Health.Status}}}}' {container}"
    stdin, stdout, stderr = await asyncio.to_thread(ssh.exec_command, command_health)
    health_status = (await asyncio.to_thread(stdout.read)).decode().strip()
    error_output = (await asyncio.to_thread(stderr.read)).decode().strip()

    if error_output:
        if "No such container" in error_output or "Error: No such object" in error_output:
            # Warning level
            logger.warning(
                f"Docker: Container '{container}' does not exist or cannot be inspected.")
        else:
            # Error level
            logger.error(
                f"Docker: Error checking health of {container}: {error_output}")
        return False

    # Check running status
    command_running = f"docker inspect --format='{{{{.State.Running}}}}' {container}"
    stdin, stdout, stderr = await asyncio.to_thread(ssh.exec_command, command_running)
    running_status_str = (await asyncio.to_thread(stdout.read)).decode().strip()
    error_output = (await asyncio.to_thread(stderr.read)).decode().strip()

    if error_output:
        if "No such container" in error_output or "Error: No such object" in error_output:
            # Warning level
            logger.warning(
                f"Docker: Container '{container}' does not exist or cannot be inspected for running status.")
        else:
            # Error level
            logger.error(
                f"Docker: Error checking running status of {container}: {error_output}")
        return False

    running_status = (running_status_str.lower() == "true")

    # Debug level for raw output
    logger.debug(
        f"Docker: {container} - Raw Running: '{running_status_str}', Health: '{health_status}'")
    # Info level
    logger.info(
        f"Docker: {container} - Is Running: {running_status}, Health Status: {health_status if health_status else 'N/A (no healthcheck)'}")

    # A container is considered 'healthy' if it's running AND (its health check is 'healthy' or it has no health check defined)
    return running_status and (health_status == "healthy" or health_status == "")


async def restart_containers_logic(container_list):
    """Handles the core logic for stopping, starting, and health-checking containers."""
    if not container_list:
        # Info level
        logger.info("Docker: No containers specified for restart. Skipping.")
        return True  # Considered successful if nothing to do

    # Info level
    logger.info(
        f"Docker: Initiating restart sequence for containers: {', '.join(container_list)}")
    ssh = await get_ssh_client()
    if not ssh:
        # Error level
        logger.error(
            "Docker: Cannot perform restart due to failed SSH connection.")
        return False

    try:
        for container in container_list:
            # Info level
            logger.info(
                f"Docker: Attempting to stop container: {container}...")
            stop_command = f"docker stop {container}"
            stdin, stdout, stderr = await asyncio.to_thread(ssh.exec_command, stop_command)
            stdout_output = (await asyncio.to_thread(stdout.read)).decode().strip()
            stderr_output = (await asyncio.to_thread(stderr.read)).decode().strip()

            if stderr_output and "No such container" not in stderr_output and "is already stopped" not in stderr_output:
                # Error level
                logger.error(
                    f"Docker: Error stopping {container}: {stderr_output}. Stdout: {stdout_output}")
                # Decide if a stop error should halt the entire restart. For now, it will.
                return False
            else:
                # Info level
                logger.info(
                    f"Docker: Stopped {container}. Output: {stdout_output if stdout_output else 'Already stopped/No output'}")

            # Info level
            logger.info(
                f"Docker: Attempting to start container: {container}...")
            start_command = f"docker start {container}"
            stdin, stdout, stderr = await asyncio.to_thread(ssh.exec_command, start_command)
            stdout_output = (await asyncio.to_thread(stdout.read)).decode().strip()
            stderr_output = (await asyncio.to_thread(stderr.read)).decode().strip()
            if stderr_output:
                # Error level
                logger.error(
                    f"Docker: Error starting {container}: {stderr_output}. Stdout: {stdout_output}")
                return False  # Critical error if start fails

            # Info level
            logger.info(
                f"Docker: Started {container}. Output: {stdout_output}")

            # Wait for container to become healthy
            max_attempts = 60  # 60 attempts * 10 seconds = 10 minutes timeout
            attempts = 0
            while attempts < max_attempts:
                # Debug level
                logger.debug(
                    f"Docker: Checking health of {container}, attempt {attempts + 1}/{max_attempts}...")
                if await check_container_health(ssh, container):
                    # Info level
                    logger.info(
                        f"Docker: Container {container} is now healthy!")
                    break
                await asyncio.sleep(10)  # Wait 10 seconds between checks
                attempts += 1

            if attempts == max_attempts:
                # Error level
                logger.error(
                    f"Docker: Container {container} did not become healthy within {max_attempts * 10} seconds. Restart failed for this container.")
                return False  # Indicate failure if a container doesn't become healthy

        # Info level
        logger.info(
            f"Docker: All containers in list {', '.join(container_list)} restarted successfully and are healthy.")
        return True

    except Exception as e:
        # Critical level
        logger.critical(
            f"Docker: A critical unexpected error occurred during container restart logic for {container_list}: {e}", exc_info=True)
        return False
    finally:
        if ssh:
            # Ensure SSH connection is closed
            # Info level
            logger.info(
                "Docker: Closing SSH connection after restart operation.")
            await asyncio.to_thread(ssh.close)


@discord.app_commands.command(name="restart_all_containers", description="Restart ALL configured Docker containers.")
async def restart_all_containers(interaction: discord.Interaction):
    """Restart all Docker containers listed in CONTAINER_NAMES."""
    logger.info(
        # Info level
        f"Docker: Received /restart_all_containers command from user {interaction.user.id}.")
    await interaction.response.defer()  # Defer the response

    if not CONTAINER_NAMES:
        # Warning level
        logger.warning(
            "Docker: CONTAINER_NAMES is empty. No containers to restart for /restart_all_containers command.")
        await interaction.followup.send("No Docker containers configured in `CONTAINER_NAMES` environment variable to restart.")
        return

    await interaction.followup.send(f"Initiating restart sequence for ALL configured containers: {', '.join(CONTAINER_NAMES)}...")

    # Use CONTAINER_NAMES here
    success = await restart_containers_logic(CONTAINER_NAMES)

    if success:
        # Info level
        logger.info(
            f"Docker: Successfully restarted all containers in CONTAINER_NAMES.")
        await interaction.followup.send("All configured containers have been restarted and are healthy! Enjoy! 🎉")
    else:
        # Error level
        logger.error(
            f"Docker: Failed to restart one or more containers in CONTAINER_NAMES.")
        await interaction.followup.send("Failed to restart one or more containers. Check bot logs for details. ❌")


def check_docker_containers_sync():
    """Synchronous function to check container statuses for commands.
    This function blocks the event loop, so it's run via asyncio.to_thread.
    """
    logger.info(
        "Docker: Initiating synchronous check of all configured containers.")  # Info level
    ssh = None
    try:
        if not all([DOCKER_SERVER_IP, DOCKER_SERVER_USER, DOCKER_SERVER_PASSWORD]):
            logger.error(
                "Docker: SSH connection details (IP, User, Password) are not fully set for synchronous check.")
            return None

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        # Debug level
        logger.debug(
            f"Docker: Synchronous check: Attempting SSH connect to {DOCKER_SERVER_IP} as {DOCKER_SERVER_USER}...")
        ssh.connect(
            DOCKER_SERVER_IP,
            username=DOCKER_SERVER_USER,
            password=DOCKER_SERVER_PASSWORD
        )
        # Info level
        logger.info(
            f"Docker: Synchronous check: Successfully connected via SSH.")

        container_statuses = {}
        if not CONTAINER_NAMES:
            logger.warning(
                "Docker: No CONTAINER_NAMES configured. Returning empty statuses.")
            return {}

        for container in CONTAINER_NAMES:
            # Debug level
            logger.debug(
                f"Docker: Synchronous check: Inspecting {container}...")
            health_status = ""
            running_status = "false"  # Default to false

            # Check health status
            command_health = f"docker inspect --format='{{{{.State.Health.Status}}}}' {container}"
            stdin, stdout, stderr = ssh.exec_command(command_health)
            health_status_raw = stdout.read().decode().strip()
            error_output_health = stderr.read().decode().strip()

            if error_output_health:
                if "No such container" in error_output_health or "Error: No such object" in error_output_health:
                    # Warning level
                    logger.warning(
                        f"Docker: Sync check: Container '{container}' not found or cannot be inspected for health.")
                    health_status = "not found"
                else:
                    # Error level
                    logger.error(
                        f"Docker: Sync check: Error inspecting health of {container}: {error_output_health}")
                    health_status = "error"
            else:
                health_status = health_status_raw

            # Check running status
            command_running = f"docker inspect --format='{{{{.State.Running}}}}' {container}"
            stdin, stdout, stderr = ssh.exec_command(command_running)
            running_status_raw = stdout.read().decode().strip()
            error_output_running = stderr.read().decode().strip()

            if error_output_running:
                if "No such container" in error_output_running or "Error: No such object" in error_output_running:
                    # Warning level
                    logger.warning(
                        f"Docker: Sync check: Container '{container}' not found or cannot be inspected for running status.")
                    running_status = "not found"  # Indicate it doesn't exist
                else:
                    # Error level
                    logger.error(
                        f"Docker: Sync check: Error inspecting running status of {container}: {error_output_running}")
                    running_status = "error"  # Indicate an error occurred
            else:
                running_status = running_status_raw

            container_statuses[container] = {
                "health": health_status,
                "running": (running_status.lower() == "true") if running_status not in ["not found", "error"] else False
            }
            # Debug level
            logger.debug(
                f"Docker: Sync check: Status for {container}: {container_statuses[container]}")

        # Info level
        logger.info(
            f"Docker: Synchronous container check completed for {len(container_statuses)} containers.")
        return container_statuses

    except paramiko.AuthenticationException:
        logger.error(
            f"Docker: Sync check: SSH Authentication failed for {DOCKER_SERVER_USER}@{DOCKER_SERVER_IP}. Check credentials.")
        return None
    except paramiko.SSHException as e:
        logger.error(
            f"Docker: Sync check: SSH negotiation failed or other SSH error: {e}")
        return None
    except Exception as e:
        # Critical level
        logger.critical(
            f"Docker: Sync check: An unexpected critical error occurred: {e}", exc_info=True)
        return None
    finally:
        if ssh:
            # Info level
            logger.info("Docker: Sync check: Closing SSH connection.")
            ssh.close()


# These functions will be assigned to commands later in bot.py
async def plex_status_command(interaction: discord.Interaction):
    """Check Plex Docker container status."""
    logger.info(
        # Info level
        f"Docker: Received /plex_status command from user {interaction.user.id}.")
    # Defer the response as SSH operations can take time
    await interaction.response.defer()

    statuses = await asyncio.to_thread(check_docker_containers_sync)

    if statuses is None:
        # Error level
        logger.error(
            "Docker: Failed to get Docker status for /plex_status command. SSH connection failed.")
        await interaction.followup.send("Failed to check Docker status. Check bot logs for SSH errors.")
        return

    if not statuses:
        # Warning level
        logger.warning(
            "Docker: No container statuses returned by check_docker_containers_sync. Is CONTAINER_NAMES configured?")
        await interaction.followup.send("No Docker containers configured or found to check. Please check `CONTAINER_NAMES` in your `.env`.")
        return

    broken_containers = []
    healthy_containers = []

    for container_name in CONTAINER_NAMES:  # Iterate over expected containers for a clear message
        status = statuses.get(container_name)
        if status:
            if not status["running"] or (status["health"] not in ["healthy", ""] and status["health"] != "starting"):
                broken_containers.append(
                    f"**{container_name}** (Running: {status['running']}, Health: {status['health'] if status['health'] else 'N/A'})"
                )
            else:
                healthy_containers.append(
                    f"**{container_name}** (Running: {status['running']}, Health: {status['health'] if status['health'] else 'N/A'})")
        else:
            broken_containers.append(
                f"**{container_name}** (Status Unknown - Not found or error during inspection)")
            # Warning level
            logger.warning(
                f"Docker: Status for configured container '{container_name}' not found in results.")

    if broken_containers:
        newline = '\n'
        message = f"**Problem containers detected:**{newline}{newline.join(broken_containers)}"
        if healthy_containers:
            message += f"\n\n**Healthy containers:**{newline}{newline.join(healthy_containers)}"
        # Warning level
        logger.warning(
            f"Docker: Problem containers found: {', '.join([c.split(' ')[0].replace('**', '') for c in broken_containers])}")
        await interaction.followup.send(message)
    else:
        # Info level
        logger.info(
            "Docker: All monitored Docker containers are running and healthy.")
        await interaction.followup.send("All monitored Docker containers are running and healthy! ✅")


async def restart_containers_command(interaction: discord.Interaction):
    """Restart specified Docker containers in order."""
    logger.info(
        # Info level
        f"Docker: Received /restart_containers command from user {interaction.user.id}.")
    await interaction.response.defer()  # Defer the response

    if not RESTART_ORDER:
        # Warning level
        logger.warning(
            "Docker: RESTART_ORDER is empty. No containers to restart for /restart_containers command.")
        await interaction.followup.send("No containers specified in `RESTART_ORDER` environment variable to restart.")
        return

    await interaction.followup.send(f"Initiating container restart sequence for: {', '.join(RESTART_ORDER)}...")

    success = await restart_containers_logic(RESTART_ORDER)

    if success:
        # Info level
        logger.info(
            f"Docker: Successfully restarted all containers in RESTART_ORDER.")
        await interaction.followup.send("All specified containers have been restarted and are healthy! Enjoy! 🎉")
    else:
        # Error level
        logger.error(
            f"Docker: Failed to restart one or more containers in RESTART_ORDER.")
        await interaction.followup.send("Failed to restart one or more containers. Check bot logs for details. ❌")


async def restart_plex_command(interaction: discord.Interaction):
    """Restart just the 'plex' container."""
    logger.info(
        # Info level
        f"Docker: Received /restart_plex command from user {interaction.user.id}.")
    await interaction.response.defer()  # Defer the response

    if not DOCKER_SERVER_IP or not DOCKER_SERVER_USER or not DOCKER_SERVER_PASSWORD:
        # Error level
        logger.error(
            "Docker: SSH connection details missing for /restart_plex command.")
        await interaction.followup.send("SSH connection details not fully configured. Cannot restart Plex.")
        return

    # Check if 'plex' is actually in CONTAINER_NAMES for consistency (optional but good)
    if 'plex' not in CONTAINER_NAMES:
        # Warning level
        logger.warning(
            "Docker: 'plex' container not found in CONTAINER_NAMES, but attempting to restart it via /restart_plex.")
        # You might want to prevent this or inform the user that 'plex' isn't monitored
        # For now, we'll proceed as it's a direct request for 'plex'

    await interaction.followup.send("Initiating Plex container restart...")

    success = await restart_containers_logic(['plex'])  # Restart only 'plex'

    if success:
        # Info level
        logger.info("Docker: Successfully restarted 'plex' container.")
        await interaction.followup.send("Plex container has been restarted and is healthy! Enjoy! 🎉")
    else:
        # Error level
        logger.error("Docker: Failed to restart 'plex' container.")
        await interaction.followup.send("Failed to restart Plex container. Check bot logs for details. ❌")
