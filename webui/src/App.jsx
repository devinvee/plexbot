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
	const [showNotificationDetails, setShowNotificationDetails] = useState(false);
	const [selectedNotification, setSelectedNotification] = useState(null);
	const [scanning, setScanning] = useState(false);
	const [scanResult, setScanResult] = useState(null);
	const [notificationFilter, setNotificationFilter] = useState('all');
	const [notificationSearch, setNotificationSearch] = useState('');
	const [showBrowseModal, setShowBrowseModal] = useState(false);
	const [libraries, setLibraries] = useState([]);
	const [selectedLibrary, setSelectedLibrary] = useState(null);
	const [libraryItems, setLibraryItems] = useState([]);
	const [loadingItems, setLoadingItems] = useState(false);
	const [scanningAll, setScanningAll] = useState(false);
	const [scanAllProgress, setScanAllProgress] = useState(null);
	const [pendingScans, setPendingScans] = useState([]);
	const [plexActivities, setPlexActivities] = useState([]);

	useEffect(() => {
		fetchStatus();
		fetchNotifications();
		fetchPendingScans();
		fetchPlexActivities();
		const interval = setInterval(() => {
			fetchStatus();
			fetchNotifications();
			fetchPendingScans();
			fetchPlexActivities();
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

	const fetchLibraries = async () => {
		try {
			const response = await fetch(`${API_BASE}/plex/libraries`);
			const data = await response.json();
			if (data.success) {
				setLibraries(data.libraries || []);
			}
		} catch (error) {
			console.error('Failed to fetch libraries:', error);
		}
	};

	const fetchLibraryItems = async (libraryKey) => {
		setLoadingItems(true);
		setLibraryItems([]);
		try {
			const response = await fetch(`${API_BASE}/plex/library/${libraryKey}/items`);
			const data = await response.json();
			console.log('Library items response:', data);
			if (data.success) {
				setLibraryItems(data.items || []);
				if (!data.items || data.items.length === 0) {
					console.warn(`No items found for library key: ${libraryKey}`);
				}
			} else {
				console.error('Failed to fetch library items:', data.message);
				alert(`Error loading items: ${data.message || 'Unknown error'}`);
			}
		} catch (error) {
			console.error('Failed to fetch library items:', error);
			alert(`Error loading items: ${error.message}`);
		} finally {
			setLoadingItems(false);
		}
	};

	const handleLibrarySelect = (library) => {
		setSelectedLibrary(library);
		fetchLibraryItems(library.key);
	};

	const fetchPendingScans = async () => {
		try {
			const response = await fetch(`${API_BASE}/plex/pending-scans`);
			const data = await response.json();
			console.log('Pending scans response:', data);
			if (data.success) {
				setPendingScans(data.pending_scans || []);
			} else {
				console.error('Failed to fetch pending scans:', data.message);
			}
		} catch (error) {
			console.error('Failed to fetch pending scans:', error);
		}
	};

	const fetchPlexActivities = async () => {
		try {
			const response = await fetch(`${API_BASE}/plex/activities`);
			const data = await response.json();
			if (data.success) {
				setPlexActivities(data.activities || []);
			}
		} catch (error) {
			console.error('Failed to fetch Plex activities:', error);
		}
	};

	const handleItemScan = async (item) => {
		try {
			console.log(`Scanning item: ${item.title} (key: ${item.key})`);
			// Send item_key and item_name in request body
			const response = await fetch(`${API_BASE}/plex/item/scan`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ 
					item_key: item.key,
					item_name: item.title
				}),
			});
			
			if (!response.ok) {
				const errorText = await response.text();
				console.error('Scan request failed:', response.status, errorText);
				// Try to parse as JSON if possible, otherwise show raw text
				try {
					const errorData = JSON.parse(errorText);
					alert(`Failed to trigger scan: ${errorData.message || errorText}`);
				} catch {
					alert(`Failed to trigger scan: ${response.status} ${errorText.substring(0, 100)}`);
				}
				return;
			}
			
			const data = await response.json();
			console.log('Scan response:', data);
			if (data.success) {
				// Refresh pending scans
				fetchPendingScans();
				alert(`Successfully triggered scan for ${item.title}!`);
				setShowBrowseModal(false);
				setSelectedLibrary(null);
				setLibraryItems([]);
			} else {
				alert(`Error: ${data.message || 'Unknown error'}`);
			}
		} catch (error) {
			console.error('Error triggering scan:', error);
			alert(`Failed to trigger scan: ${error.message}`);
		}
	};

	// Filter notifications based on search and type filter
	const filteredNotifications = notifications.filter((notif) => {
		const matchesType =
			notificationFilter === 'all' || notif.type === notificationFilter;
		const matchesSearch =
			!notificationSearch ||
			notif.title.toLowerCase().includes(notificationSearch.toLowerCase()) ||
			(notif.episode?.title &&
				notif.episode.title.toLowerCase().includes(notificationSearch.toLowerCase())) ||
			(notif.episodes && notif.episodes.some(ep =>
				ep.title.toLowerCase().includes(notificationSearch.toLowerCase())
			));
		return matchesType && matchesSearch;
	});

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
				<section className="quick-actions-section">
					<h2>Quick Actions</h2>
					<div className="quick-actions-grid">
						<button
							className="quick-action-btn"
							onClick={async () => {
								setScanningAll(true);
								setScanAllProgress(null);
								try {
									const response = await fetch(`${API_BASE}/plex/scan-all`, {
										method: 'POST',
										headers: { 'Content-Type': 'application/json' },
									});
									const data = await response.json();
									setScanAllProgress(data);
									fetchPendingScans(); // Refresh pending scans
									if (data.success) {
										alert(`Successfully scanned ${data.scanned} of ${data.total} libraries!`);
									} else {
										alert(`Scan completed with issues: ${data.message || 'Some libraries failed to scan'}`);
									}
								} catch (error) {
									alert('Failed to trigger scan');
								} finally {
									setScanningAll(false);
								}
							}}
							disabled={scanningAll}
						>
							<div className="quick-action-icon">📚</div>
							<div className="quick-action-label">
								{scanningAll ? 'Scanning...' : 'Scan All Libraries'}
							</div>
						</button>
						<button
							className="quick-action-btn"
							onClick={() => {
								setShowBrowseModal(true);
								fetchLibraries();
							}}
						>
							<div className="quick-action-icon">🎬</div>
							<div className="quick-action-label">Scan a Show/Movie</div>
						</button>
					</div>
					{scanAllProgress && (
						<div className="scan-progress">
							<p>Progress: {scanAllProgress.scanned} / {scanAllProgress.total} libraries scanned</p>
							{scanAllProgress.results && (
								<ul className="scan-results-list">
									{scanAllProgress.results.map((result, idx) => (
										<li key={idx} className={result.success ? 'success' : 'error'}>
											{result.library}: {result.success ? '✓' : '✗'} {result.message || ''}
										</li>
									))}
								</ul>
							)}
						</div>
					)}
				</section>

				<section className="pending-scans-section">
					<h2>Pending Scans</h2>
					{pendingScans.length > 0 ? (
						<div className="pending-scans-list">
							{pendingScans.map((scan) => (
								<div key={scan.scan_id} className="pending-scan-card">
									<div className="pending-scan-header">
										<div className="pending-scan-info">
											<span className="pending-scan-type">{scan.type}</span>
											<span className="pending-scan-name">{scan.name}</span>
										</div>
										<span className={`pending-scan-status ${scan.status}`}>
											{scan.status === 'pending' ? '⏳ Pending' : scan.status === 'completed' ? '✓ Completed' : '✗ Failed'}
										</span>
									</div>
									<div className="pending-scan-time">
										Started: {new Date(scan.timestamp).toLocaleString()}
										{scan.completed_at && (
											<span> • Completed: {new Date(scan.completed_at).toLocaleString()}</span>
										)}
									</div>
								</div>
							))}
						</div>
					) : (
						<div className="empty-state">
							No pending scans. Trigger a scan to see it here.
						</div>
					)}
				</section>

				{plexActivities.length > 0 && (
					<section className="plex-activities-section">
						<h2>Plex Activities & Queued Scans</h2>
						<div className="activities-list">
							{plexActivities.map((activity, idx) => (
								<div key={activity.uuid || idx} className="activity-card">
									<div className="activity-header">
										<div className="activity-info">
											<span className="activity-type">{activity.type}</span>
											<span className="activity-title">{activity.title}</span>
											{activity.subtitle && (
												<span className="activity-subtitle">{activity.subtitle}</span>
											)}
											{activity.library_name && (
												<span className="activity-library">({activity.library_name})</span>
											)}
										</div>
										{activity.progress > 0 && (
											<span className="activity-progress">{activity.progress}%</span>
										)}
									</div>
									{activity.progress > 0 && (
										<div className="activity-progress-bar">
											<div 
												className="activity-progress-fill" 
												style={{ width: `${activity.progress}%` }}
											></div>
										</div>
									)}
								</div>
							))}
						</div>
					</section>
				)}

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
					<div className="notifications-header">
						<h2>Recent Notifications</h2>
						<div className="notifications-controls">
							<input
								type="text"
								className="notification-search"
								placeholder="Search notifications..."
								value={notificationSearch}
								onChange={(e) => setNotificationSearch(e.target.value)}
							/>
							<select
								className="notification-filter"
								value={notificationFilter}
								onChange={(e) => setNotificationFilter(e.target.value)}
							>
								<option value="all">All Types</option>
								<option value="sonarr">Sonarr</option>
								<option value="radarr">Radarr</option>
								<option value="readarr">Readarr</option>
							</select>
						</div>
					</div>
					<div className="notifications-list">
						{filteredNotifications.length === 0 ? (
							<div className="empty-state">
								{notifications.length === 0
									? 'No notifications in the last 24 hours'
									: 'No notifications match your filters'}
							</div>
						) : (
							filteredNotifications.map((notif, idx) => (
								<div
									key={idx}
									className="notification-card"
									onClick={() => {
										setSelectedNotification(notif);
										setShowNotificationDetails(true);
									}}
									style={{ cursor: 'pointer' }}
								>
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
												{notif.episodes && notif.episodes.length > 0 ? (
													<div className="notification-episodes-list">
														{notif.episodes.length === 1 ? (
															<p>
																S{notif.episodes[0].season.toString().padStart(2, '0')}E
																{notif.episodes[0].number.toString().padStart(2, '0')}:{' '}
																{notif.episodes[0].title}
															</p>
														) : (
															<>
																<p className="notification-batch">
																	{notif.episodes.length} episodes imported
																</p>
																<ul className="notification-episodes-preview">
																	{notif.episodes.slice(0, 3).map((ep, idx) => (
																		<li key={idx}>
																			S{ep.season.toString().padStart(2, '0')}E
																			{ep.number.toString().padStart(2, '0')}: {ep.title}
																		</li>
																	))}
																	{notif.episodes.length > 3 && (
																		<li className="episodes-more">
																			+{notif.episodes.length - 3} more...
																		</li>
																	)}
																</ul>
															</>
														)}
													</div>
												) : notif.episode && (
													<p>
														S{notif.episode.season.toString().padStart(2, '0')}E
														{notif.episode.number.toString().padStart(2, '0')}:{' '}
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

			<Modal
				isOpen={showBrowseModal}
				onClose={() => {
					setShowBrowseModal(false);
					setSelectedLibrary(null);
					setLibraryItems([]);
				}}
				title="Scan a Show/Movie"
			>
				<div className="browse-modal">
					{!selectedLibrary ? (
						<div className="library-selector">
							<p>Select a library to browse:</p>
							<div className="libraries-grid">
								{libraries.map((lib) => (
									<button
										key={lib.key}
										className="library-card"
										onClick={() => handleLibrarySelect(lib)}
									>
										<div className="library-icon">
											{lib.type === 'show' ? '📺' : lib.type === 'movie' ? '🎬' : '📚'}
										</div>
										<div className="library-name">{lib.title}</div>
										<div className="library-type">{lib.type}</div>
									</button>
								))}
							</div>
						</div>
					) : (
						<div className="items-browser">
							<div className="browser-header">
								<button
									className="btn btn-secondary"
									onClick={() => {
										setSelectedLibrary(null);
										setLibraryItems([]);
									}}
								>
									← Back to Libraries
								</button>
								<h3>{selectedLibrary.title}</h3>
							</div>
							{loadingItems ? (
								<div className="loading">Loading items...</div>
							) : (
								<div className="items-list">
									{libraryItems.length === 0 ? (
										<p>No items found in this library.</p>
									) : (
										libraryItems.map((item) => (
											<div
												key={item.key}
												className="item-card"
												onClick={() => handleItemScan(item)}
												style={{ cursor: 'pointer' }}
											>
												{item.thumb && (
													<img
														src={item.thumb}
														alt={item.title}
														className="item-thumb"
														onError={(e) => {
															e.target.style.display = 'none';
														}}
													/>
												)}
												<div className="item-info">
													<div className="item-title">{item.title}</div>
													{item.year && (
														<div className="item-year">{item.year}</div>
													)}
												</div>
											</div>
										))
									)}
								</div>
							)}
						</div>
					)}
				</div>
			</Modal>

			<Modal
				isOpen={showNotificationDetails}
				onClose={() => {
					setShowNotificationDetails(false);
					setSelectedNotification(null);
				}}
				title="Notification Details"
			>
				{selectedNotification && (
					<div className="notification-details">
						<div className="notification-details-header">
							<div className="notification-details-image">
								{(selectedNotification.poster_url ||
									selectedNotification.fanart_url ||
									selectedNotification.backdrop_url) && (
									<img
										src={
											selectedNotification.poster_url ||
											selectedNotification.fanart_url ||
											selectedNotification.backdrop_url
										}
										alt={selectedNotification.title}
										onError={(e) => {
											e.target.style.display = 'none';
										}}
									/>
								)}
							</div>
							<div className="notification-details-info">
								<h3>
									{selectedNotification.title}
									{selectedNotification.year && ` (${selectedNotification.year})`}
								</h3>
								<div className="notification-details-meta">
									<span
										className={`notification-type ${selectedNotification.type}`}
									>
										{selectedNotification.type}
									</span>
									<span className="notification-time">
										{new Date(
											selectedNotification.timestamp
										).toLocaleString()}
									</span>
								</div>
								{selectedNotification.quality && (
									<p className="notification-quality">
										<strong>Quality:</strong> {selectedNotification.quality}
									</p>
								)}
								{selectedNotification.episode_count &&
									selectedNotification.episode_count > 1 && (
										<p className="notification-batch">
											<strong>Batch:</strong>{' '}
											{selectedNotification.episode_count} episodes
										</p>
									)}
							</div>
						</div>
						{(selectedNotification.episodes || selectedNotification.episode) && (
							<div className="notification-details-episodes">
								<h4>
									{selectedNotification.episodes && selectedNotification.episodes.length > 1
										? `Imported Episodes (${selectedNotification.episodes.length})`
										: 'Episode Information'}
								</h4>
								{selectedNotification.episodes && selectedNotification.episodes.length > 0 ? (
									<div className="episodes-list">
										{selectedNotification.episodes.map((ep, idx) => (
											<div key={idx} className="episode-detail-card">
												<div className="episode-header">
													<strong>
														S{ep.season.toString().padStart(2, '0')}E
														{ep.number.toString().padStart(2, '0')}
													</strong>
													<span className="episode-title">{ep.title}</span>
												</div>
												{ep.airDate && ep.airDate !== 'N/A' && (
													<p className="episode-meta">
														<strong>Air Date:</strong> {ep.airDate}
													</p>
												)}
												{ep.overview && (
													<p className="episode-overview">{ep.overview}</p>
												)}
											</div>
										))}
									</div>
								) : selectedNotification.episode && (
									<div className="episode-detail-card">
										<div className="episode-header">
											<strong>
												S{selectedNotification.episode.season.toString().padStart(2, '0')}E
												{selectedNotification.episode.number.toString().padStart(2, '0')}
											</strong>
											<span className="episode-title">{selectedNotification.episode.title}</span>
										</div>
									</div>
								)}
							</div>
						)}
					</div>
				)}
			</Modal>
		</div>
	);
}

export default App;
