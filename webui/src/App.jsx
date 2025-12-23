import { useState, useEffect } from 'react';
import './App.css';
import SettingsModal from './SettingsModal';

const API_BASE = '/api';

function Modal({ isOpen, onClose, title, children }) {
	if (!isOpen) return null;

	return (
		<div className="modal-overlay" onClick={onClose}>
			<div className="modal-content" onClick={(e) => e.stopPropagation()}>
				<div className="modal-header">
					<h2>{title}</h2>
					<button className="modal-close" onClick={onClose}>
						×
					</button>
				</div>
				<div className="modal-body">{children}</div>
			</div>
		</div>
	);
}

function App() {
	const [status, setStatus] = useState(null);
	const [notifications, setNotifications] = useState([]);
	const [loading, setLoading] = useState(true);
	const [showSettings, setShowSettings] = useState(false);
	const [showScanModal, setShowScanModal] = useState(false);
	const [scanning, setScanning] = useState(false);
	const [scanResult, setScanResult] = useState(null);

	useEffect(() => {
		fetchStatus();
		fetchNotifications();
		const interval = setInterval(() => {
			fetchStatus();
			fetchNotifications();
		}, 5000); // Refresh every 5 seconds
		return () => clearInterval(interval);
	}, []);

	const fetchStatus = async () => {
		try {
			const response = await fetch(`${API_BASE}/status`);
			const data = await response.json();
			setStatus(data);
		} catch (error) {
			console.error('Failed to fetch status:', error);
		} finally {
			setLoading(false);
		}
	};

	const fetchNotifications = async () => {
		try {
			const response = await fetch(`${API_BASE}/notifications`);
			const data = await response.json();
			setNotifications(data.notifications || []);
		} catch (error) {
			console.error('Failed to fetch notifications:', error);
		}
	};

	const triggerPlexScan = async (libraryName = null) => {
		setScanning(true);
		setScanResult(null);
		try {
			const response = await fetch(`${API_BASE}/plex/scan`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ library_name: libraryName }),
			});
			const data = await response.json();
			setScanResult(data);
			if (data.success) {
				setTimeout(() => setShowScanModal(false), 2000);
			}
		} catch (error) {
			setScanResult({
				success: false,
				message: 'Failed to trigger scan',
			});
		} finally {
			setScanning(false);
		}
	};

	if (loading) {
		return (
			<div className="app-container">
				<div className="loading">Loading...</div>
			</div>
		);
	}

	return (
		<div className="app-container">
			<header className="app-header">
				<h1>PlexBot Dashboard</h1>
				<div className="header-actions">
					<button
						className="btn btn-primary"
						onClick={() => setShowScanModal(true)}
					>
						Scan Plex Library
					</button>
					<button
						className="btn btn-secondary"
						onClick={() => setShowSettings(true)}
					>
						Settings
					</button>
				</div>
			</header>

			<main className="app-main">
				<section className="status-section">
					<h2>System Status</h2>
					<div className="status-grid">
						<div className="status-card">
							<h3>Plex</h3>
							<div
								className={`status-indicator ${
									status?.plex?.connected
										? 'online'
										: 'offline'
								}`}
							>
								{status?.plex?.connected
									? '✓ Connected'
									: '✗ Disconnected'}
							</div>
							{status?.plex?.name && (
								<p className="status-detail">
									{status.plex.name}
								</p>
							)}
						</div>
						<div className="status-card">
							<h3>Discord Bot</h3>
							<div
								className={`status-indicator ${
									status?.discord?.connected
										? 'online'
										: 'offline'
								}`}
							>
								{status?.discord?.connected
									? '✓ Connected'
									: '✗ Disconnected'}
							</div>
							{status?.discord?.username && (
								<p className="status-detail">
									{status.discord.username}
								</p>
							)}
						</div>
						<div className="status-card">
							<h3>Notifications</h3>
							<div className="status-indicator online">
								{notifications.length} Recent
							</div>
							<p className="status-detail">Last 24 hours</p>
						</div>
						<div className="status-card">
							<h3>Plex Scanning</h3>
							<div
								className={`status-indicator ${
									status?.plex?.scan_enabled
										? 'online'
										: 'offline'
								}`}
							>
								{status?.plex?.scan_enabled
									? '✓ Enabled'
									: '✗ Disabled'}
							</div>
							<p className="status-detail">
								Auto-scan on notifications
							</p>
						</div>
					</div>
				</section>

				<section className="notifications-section">
					<h2>Recent Notifications</h2>
					<div className="notifications-list">
						{notifications.length === 0 ? (
							<div className="empty-state">
								No notifications in the last 24 hours
							</div>
						) : (
							notifications.map((notif, idx) => (
								<div key={idx} className="notification-card">
									<div className="notification-header">
										<span
											className={`notification-type ${notif.type}`}
										>
											{notif.type}
										</span>
										<span className="notification-time">
											{new Date(
												notif.timestamp
											).toLocaleString()}
										</span>
									</div>
									<div className="notification-content">
										<div className="notification-media">
											{(notif.poster_url || notif.fanart_url || notif.backdrop_url) && (
												<div className="notification-image">
													<img
														src={notif.poster_url || notif.fanart_url || notif.backdrop_url}
														alt={notif.title}
														onError={(e) => {
															// Fallback if image fails to load
															e.target.style.display = 'none';
														}}
													/>
												</div>
											)}
											<div className="notification-text">
												<h4>{notif.title}{notif.year && ` (${notif.year})`}</h4>
												{notif.episode && (
													<p>
														S{notif.episode.season}E
														{notif.episode.number}:{' '}
														{notif.episode.title}
													</p>
												)}
												{notif.quality && (
													<p className="notification-quality">
														Quality: {notif.quality}
													</p>
												)}
												{notif.episode_count && notif.episode_count > 1 && (
													<p className="notification-batch">
														{notif.episode_count} episodes in this batch
													</p>
												)}
											</div>
										</div>
									</div>
								</div>
							))
						)}
					</div>
				</section>
			</main>

			<Modal
				isOpen={showScanModal}
				onClose={() => setShowScanModal(false)}
				title="Scan Plex Library"
			>
				<div className="scan-modal">
					<p>
						Trigger a manual scan of your Plex library. Leave empty
						to scan all libraries.
					</p>
					{scanResult && (
						<div
							className={`scan-result ${
								scanResult.success ? 'success' : 'error'
							}`}
						>
							{scanResult.message}
						</div>
					)}
					<div className="scan-actions">
						<button
							className="btn btn-primary"
							onClick={() => triggerPlexScan(null)}
							disabled={scanning}
						>
							{scanning ? 'Scanning...' : 'Scan All Libraries'}
						</button>
						{status?.plex?.libraries &&
							status.plex.libraries.length > 0 && (
								<div className="library-select">
									<label>Or scan specific library:</label>
									<select
										onChange={(e) =>
											triggerPlexScan(
												e.target.value || null
											)
										}
										disabled={scanning}
									>
										<option value="">
											Select library...
										</option>
										{status.plex.libraries.map((lib) => (
											<option
												key={lib.key}
												value={lib.title}
											>
												{lib.title}
											</option>
										))}
									</select>
								</div>
							)}
					</div>
				</div>
			</Modal>

			<SettingsModal
				isOpen={showSettings}
				onClose={() => setShowSettings(false)}
				status={status}
			/>
		</div>
	);
}

export default App;
