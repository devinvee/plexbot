import { useState, useEffect } from 'react';
import './App.css';

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
	const [settings, setSettings] = useState(null);
	const [savingSettings, setSavingSettings] = useState(false);
	const [settingsResult, setSettingsResult] = useState(null);

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

	const fetchSettings = async () => {
		try {
			const response = await fetch(`${API_BASE}/settings`);
			const data = await response.json();
			setSettings(data);
		} catch (error) {
			console.error('Failed to fetch settings:', error);
		}
	};

	const saveSettings = async () => {
		setSavingSettings(true);
		setSettingsResult(null);
		try {
			const response = await fetch(`${API_BASE}/settings`, {
				method: 'PUT',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(settings),
			});
			const data = await response.json();
			setSettingsResult(data);
			if (data.success) {
				// Refresh status to show updated settings
				fetchStatus();
				setTimeout(() => setShowSettings(false), 1500);
			}
		} catch (error) {
			setSettingsResult({
				success: false,
				message: 'Failed to save settings',
			});
		} finally {
			setSavingSettings(false);
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
						onClick={() => {
							fetchSettings();
							setShowSettings(true);
						}}
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
										<h4>{notif.title}</h4>
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

			<Modal
				isOpen={showSettings}
				onClose={() => {
					setShowSettings(false);
					setSettingsResult(null);
				}}
				title="Settings"
			>
				<div className="settings-modal">
					{settings && (
						<>
							<div className="setting-item">
								<label>Plex Integration</label>
								<div className="setting-control">
									<label className="toggle-switch">
										<input
											type="checkbox"
											checked={settings.plex?.enabled ?? true}
											onChange={(e) =>
												setSettings({
													...settings,
													plex: {
														...settings.plex,
														enabled: e.target.checked,
													},
												})
											}
										/>
										<span className="toggle-slider"></span>
									</label>
									<span className="toggle-label">
										{settings.plex?.enabled ? 'Enabled' : 'Disabled'}
									</span>
								</div>
								<p className="setting-description">
									Enable Plex integration features
								</p>
							</div>

							<div className="setting-item">
								<label>Plex Auto-Scan</label>
								<div className="setting-control">
									<label className="toggle-switch">
										<input
											type="checkbox"
											checked={settings.plex?.scan_on_notification ?? true}
											onChange={(e) =>
												setSettings({
													...settings,
													plex: {
														...settings.plex,
														scan_on_notification: e.target.checked,
													},
												})
											}
											disabled={!settings.plex?.enabled}
										/>
										<span className="toggle-slider"></span>
									</label>
									<span className="toggle-label">
										{settings.plex?.scan_on_notification
											? 'Enabled'
											: 'Disabled'}
									</span>
								</div>
								<p className="setting-description">
									Automatically scan Plex libraries when notifications
									are received
								</p>
							</div>

							<div className="setting-item">
								<label>Target Library</label>
								<div className="setting-control">
									<select
										value={settings.plex?.library_name || ''}
										onChange={(e) =>
											setSettings({
												...settings,
												plex: {
													...settings.plex,
													library_name:
														e.target.value || null,
												},
											})
										}
										disabled={!settings.plex?.enabled}
									>
										<option value="">All Libraries</option>
										{status?.plex?.libraries?.map((lib) => (
											<option key={lib.key} value={lib.title}>
												{lib.title}
											</option>
										))}
									</select>
								</div>
								<p className="setting-description">
									Select a specific library to scan, or leave as "All
									Libraries" to scan all
								</p>
							</div>

							<div className="setting-item">
								<label>Notification Debounce</label>
								<div className="setting-value">
									{settings.debounce_seconds || 60} seconds
								</div>
								<p className="setting-description">
									Episodes are batched for this duration before sending
									notifications (read-only)
								</p>
							</div>

							{settingsResult && (
								<div
									className={`settings-result ${
										settingsResult.success ? 'success' : 'error'
									}`}
								>
									{settingsResult.message}
								</div>
							)}

							<div className="settings-actions">
								<button
									className="btn btn-primary"
									onClick={saveSettings}
									disabled={savingSettings}
								>
									{savingSettings ? 'Saving...' : 'Save Settings'}
								</button>
							</div>
						</>
					)}
					{!settings && (
						<div className="loading">Loading settings...</div>
					)}
				</div>
			</Modal>
		</div>
	);
}

export default App;
