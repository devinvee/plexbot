"""
Docker and SSH utility functions.
"""
import os
import asyncio
import logging
import paramiko
from typing import Optional

try:
    import docker
    from docker.client import DockerClient
except ImportError:
    docker = None
    DockerClient = None

logger = logging.getLogger(__name__)

DOCKER_SERVER_IP = os.environ.get("DOCKER_SERVER_IP")
DOCKER_SERVER_USER = os.environ.get("DOCKER_SERVER_USER")
SSH_PORT = os.environ.get("SSH_PORT", "22")
DOCKER_SERVER_PASSWORD = os.environ.get("DOCKER_SERVER_PASSWORD")

async def get_ssh_client() -> Optional[paramiko.SSHClient]:
    """
    Establishes an SSH connection to the Docker server.

    Returns:
        An SSH client object if the connection is successful, otherwise None.
    """
    if not all([DOCKER_SERVER_IP, DOCKER_SERVER_USER, DOCKER_SERVER_PASSWORD]):
        logger.error(
            "Docker: SSH connection details (IP, User, Password) are not fully set in environment variables.")
        return None

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    logger.info(
        f"Docker: Attempting to establish SSH connection to {DOCKER_SERVER_IP} as {DOCKER_SERVER_USER}...")
    try:
        await asyncio.to_thread(
            ssh.connect,
            DOCKER_SERVER_IP,
            port=int(SSH_PORT),
            username=DOCKER_SERVER_USER,
            password=DOCKER_SERVER_PASSWORD
        )
        logger.info(
            f"Docker: Successfully established SSH connection to {DOCKER_SERVER_IP}.")
        return ssh
    except paramiko.AuthenticationException:
        logger.error(
            f"Docker: SSH Authentication failed for {DOCKER_SERVER_USER}@{DOCKER_SERVER_IP}. Check credentials.")
    except paramiko.SSHException as e:
        logger.error(f"Docker: SSH negotiation failed or other SSH error: {e}")
    except Exception as e:
        logger.error(
            f"Docker: An unexpected error occurred during SSH connection: {e}", exc_info=True)
    return None

def get_docker_client() -> Optional[DockerClient]:
    """
    Gets the Docker client.

    Returns:
        The Docker client if available, otherwise None.
    """
    if not docker:
        logger.error("The 'docker' library is not installed. Docker commands will not work.")
        return None
    try:
        return docker.from_env()
    except Exception as e:
        logger.error(
            f"Could not connect to Docker daemon: {e}. Is Docker running and socket mounted correctly?")
        return None
