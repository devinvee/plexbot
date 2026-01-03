import { useState, useEffect } from 'react';
import './App.css';

const API_BASE = '/api';

export default function SettingsModal({ isOpen, onClose, status, embedded = false }) {
	const [config, setConfig] = useState(null);
	const [activeTab, setActiveTab] = useState('plex');
	const [saving, setSaving] = useState(false);
	const [result, setResult] = useState(null);
	const [loading, setLoading] = useState(true);

	useEffect(() => {
		if (isOpen) {
			fetchConfig();
		}
	}, [isOpen]);

	const fetchConfig = async () => {
		setLoading(true);
		try {
			const response = await fetch(`${API_BASE}/config`);
			const data = await response.json();
			if (data.success) {
				setConfig(data.config);
			}
		} catch (error) {
			console.error('Failed to fetch config:', error);
			setResult({ success: false, error: 'Failed to load configuration' });
		} finally {
			setLoading(false);
		}
	};

	const saveConfig = async () => {
		setSaving(true);
		setResult(null);
		try {
			const response = await fetch(`${API_BASE}/config`, {
				method: 'PUT',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ config }),
			});
			const data = await response.json();
			setResult(data);
			if (data.success) {
				setTimeout(() => {
					onClose();
					window.location.reload(); // Reload to show updated status
				}, 1500);
			}
		} catch (error) {
			setResult({
				success: false,
				error: 'Failed to save configuration',
			});
		} finally {
			setSaving(false);
		}
	};

	const updateConfig = (path, value) => {
		const keys = path.split('.');
		const newConfig = { ...config };
		let current = newConfig;

		for (let i = 0; i < keys.length - 1; i++) {
			if (!current[keys[i]]) {
				current[keys[i]] = {};
			}
			current = current[keys[i]];
		}

		current[keys[keys.length - 1]] = value;
		setConfig(newConfig);
	};

	const updateArrayItem = (path, index, value) => {
		const keys = path.split('.');
		const newConfig = { ...config };
		let current = newConfig;

		for (const key of keys) {
			current = current[key];
		}

		current[index] = { ...current[index], ...value };
		setConfig(newConfig);
	};

	const addArrayItem = (path, template) => {
		const keys = path.split('.');
		const newConfig = { ...config };
		let current = newConfig;

		for (const key of keys) {
			current = current[key];
		}

		current.push({ ...template });
		setConfig(newConfig);
	};

	const removeArrayItem = (path, index) => {
		const keys = path.split('.');
		const newConfig = { ...config };
		let current = newConfig;

		for (const key of keys) {
			current = current[key];
		}

		current.splice(index, 1);
		setConfig(newConfig);
	};

	if (!isOpen) return null;

	const tabs = [
		{ id: 'plex', label: 'Plex' },
		{ id: 'discord', label: 'Discord' },
		{ id: 'sonarr', label: 'Sonarr' },
		{ id: 'radarr', label: 'Radarr' },
		{ id: 'overseerr', label: 'Overseerr' },
		{ id: 'tmdb', label: 'TMDB' },
		{ id: 'users', label: 'User Mappings' },
		{ id: 'general', label: 'General' },
	];

	const content = (
		<>
			{!embedded && (
				<div className="modal-header">
					<h2>Configuration</h2>
					<button className="modal-close" onClick={onClose}>
						×
					</button>
				</div>
			)}
			<div className={embedded ? "settings-content" : "modal-body"}>
					{loading ? (
						<div className="loading">Loading configuration...</div>
					) : config ? (
						<>
							<div className="settings-tabs">
								{tabs.map((tab) => (
									<button
										key={tab.id}
										className={`settings-tab ${activeTab === tab.id ? 'active' : ''}`}
										onClick={() => setActiveTab(tab.id)}
									>
										{tab.label}
									</button>
								))}
							</div>

							<div className="settings-content">
								{activeTab === 'plex' && (
									<div className="settings-section">
										<h3>Plex Settings</h3>
										<SettingItem
											label="Plex Integration"
											description="Enable Plex integration features"
										>
											<Toggle
												checked={config.plex?.enabled ?? true}
												onChange={(e) =>
													updateConfig('plex.enabled', e.target.checked)
												}
											/>
										</SettingItem>
										<SettingItem
											label="Auto-Scan on Notifications"
											description="Automatically scan Plex libraries when notifications are received"
										>
											<Toggle
												checked={config.plex?.scan_on_notification ?? true}
												onChange={(e) =>
													updateConfig('plex.scan_on_notification', e.target.checked)
												}
												disabled={!config.plex?.enabled}
											/>
										</SettingItem>
										<SettingItem
											label="Target Library"
											description="Select a specific library to scan, or leave empty for all libraries"
										>
											<select
												value={config.plex?.library_name || ''}
												onChange={(e) =>
													updateConfig('plex.library_name', e.target.value || null)
												}
												disabled={!config.plex?.enabled}
											>
												<option value="">All Libraries</option>
												{status?.plex?.libraries?.map((lib) => (
													<option key={lib.key} value={lib.title}>
														{lib.title}
													</option>
												))}
											</select>
										</SettingItem>
									</div>
								)}

								{activeTab === 'discord' && (
									<div className="settings-section">
										<h3>Discord Settings</h3>
										<SettingItem
											label="Sonarr Notification Channel ID"
											description="Discord channel ID for Sonarr notifications"
										>
											<input
												type="text"
												value={config.discord?.sonarr_notification_channel_id || ''}
												onChange={(e) =>
													updateConfig('discord.sonarr_notification_channel_id', e.target.value)
												}
												placeholder="Channel ID"
											/>
										</SettingItem>
										<SettingItem
											label="Radarr Notification Channel ID"
											description="Discord channel ID for Radarr notifications"
										>
											<input
												type="text"
												value={config.discord?.radarr_notification_channel_id || ''}
												onChange={(e) =>
													updateConfig('discord.radarr_notification_channel_id', e.target.value)
												}
												placeholder="Channel ID"
											/>
										</SettingItem>
										<SettingItem
											label="DM Notifications"
											description="Enable direct message notifications"
										>
											<Toggle
												checked={config.discord?.dm_notifications_enabled ?? true}
												onChange={(e) =>
													updateConfig('discord.dm_notifications_enabled', e.target.checked)
												}
											/>
										</SettingItem>
										<SettingItem
											label="New User Invite - Enabled"
											description="Enable new user invite feature"
										>
											<Toggle
												checked={config.discord?.new_user_invite?.enabled ?? false}
												onChange={(e) =>
													updateConfig('discord.new_user_invite.enabled', e.target.checked)
												}
											/>
										</SettingItem>
										<SettingItem
											label="New User Role ID"
											description="Discord role ID to assign to new users"
										>
											<input
												type="text"
												value={config.discord?.new_user_invite?.role_id || ''}
												onChange={(e) =>
													updateConfig('discord.new_user_invite.role_id', e.target.value)
												}
												placeholder="Role ID"
												disabled={!config.discord?.new_user_invite?.enabled}
											/>
										</SettingItem>
										<SettingItem
											label="Invite Link"
											description="Invite link for new users"
										>
											<input
												type="text"
												value={config.discord?.new_user_invite?.invite_link || ''}
												onChange={(e) =>
													updateConfig('discord.new_user_invite.invite_link', e.target.value)
												}
												placeholder="https://..."
												disabled={!config.discord?.new_user_invite?.enabled}
											/>
										</SettingItem>
									</div>
								)}

								{activeTab === 'sonarr' && (
									<div className="settings-section">
										<h3>Sonarr Instances</h3>
										<p className="setting-description" style={{ marginBottom: '1.5rem' }}>
											Configure Sonarr instances for webhook notifications and API access. Sonarr instances are used to receive download notifications and fetch metadata.
										</p>
										{config.sonarr_instances && config.sonarr_instances.length > 0 ? (
											config.sonarr_instances.map((instance, index) => (
												<div key={index} className="array-item">
													<div className="array-item-header">
														<h4>Instance {index + 1}</h4>
														<button
															className="btn btn-danger btn-small"
															onClick={() => removeArrayItem('sonarr_instances', index)}
														>
															Remove
														</button>
													</div>
													<SettingItem label="Name" description="Display name for this Sonarr instance">
														<input
															type="text"
															value={instance.name || ''}
															onChange={(e) =>
																updateArrayItem('sonarr_instances', index, {
																	name: e.target.value,
																})
															}
															placeholder="Sonarr"
														/>
													</SettingItem>
													<SettingItem label="URL" description="Sonarr instance URL">
														<input
															type="text"
															value={instance.url || ''}
															onChange={(e) =>
																updateArrayItem('sonarr_instances', index, {
																	url: e.target.value,
																})
															}
															placeholder="http://sonarr:8989"
														/>
													</SettingItem>
													<SettingItem label="API Key" description="Sonarr API key">
														<input
															type="password"
															value={instance.api_key || ''}
															onChange={(e) =>
																updateArrayItem('sonarr_instances', index, {
																	api_key: e.target.value,
																})
															}
															placeholder="API Key"
														/>
													</SettingItem>
													<SettingItem label="Enabled" description="Enable this Sonarr instance">
														<Toggle
															checked={instance.enabled ?? false}
															onChange={(e) =>
																updateArrayItem('sonarr_instances', index, {
																	enabled: e.target.checked,
																})
															}
														/>
													</SettingItem>
												</div>
											))
										) : (
											<div className="empty-state" style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)', fontStyle: 'italic', marginBottom: '1rem' }}>
												No Sonarr instances configured. Add an instance to enable Sonarr integration.
											</div>
										)}
										<button
											className="btn btn-secondary"
											onClick={() => {
												if (!config.sonarr_instances) {
													updateConfig('sonarr_instances', []);
												}
												addArrayItem('sonarr_instances', {
													name: '',
													url: '',
													api_key: '',
													enabled: false,
												});
											}}
										>
											+ Add Sonarr Instance
										</button>
									</div>
								)}

								{activeTab === 'radarr' && (
									<div className="settings-section">
										<h3>Radarr Configuration</h3>
										<p className="setting-description" style={{ marginBottom: '1.5rem' }}>
											Radarr notifications are received via webhooks at <code>/webhook/radarr</code>. You can optionally configure Radarr instances here for future API integration.
										</p>
										{config.radarr_instances && config.radarr_instances.length > 0 ? (
											config.radarr_instances.map((instance, index) => (
												<div key={index} className="array-item">
													<div className="array-item-header">
														<h4>Instance {index + 1}</h4>
														<button
															className="btn btn-danger btn-small"
															onClick={() => removeArrayItem('radarr_instances', index)}
														>
															Remove
														</button>
													</div>
													<SettingItem label="Name" description="Display name for this Radarr instance">
														<input
															type="text"
															value={instance.name || ''}
															onChange={(e) =>
																updateArrayItem('radarr_instances', index, {
																	name: e.target.value,
																})
															}
															placeholder="Radarr"
														/>
													</SettingItem>
													<SettingItem label="URL" description="Radarr instance URL">
														<input
															type="text"
															value={instance.url || ''}
															onChange={(e) =>
																updateArrayItem('radarr_instances', index, {
																	url: e.target.value,
																})
															}
															placeholder="http://radarr:7878"
														/>
													</SettingItem>
													<SettingItem label="API Key" description="Radarr API key">
														<input
															type="password"
															value={instance.api_key || ''}
															onChange={(e) =>
																updateArrayItem('radarr_instances', index, {
																	api_key: e.target.value,
																})
															}
															placeholder="API Key"
														/>
													</SettingItem>
													<SettingItem label="Enabled" description="Enable this Radarr instance">
														<Toggle
															checked={instance.enabled ?? false}
															onChange={(e) =>
																updateArrayItem('radarr_instances', index, {
																	enabled: e.target.checked,
																})
															}
														/>
													</SettingItem>
												</div>
											))
										) : (
											<div className="empty-state" style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)', fontStyle: 'italic', marginBottom: '1rem' }}>
												No Radarr instances configured. Radarr notifications work via webhooks and don't require instance configuration.
											</div>
										)}
										<button
											className="btn btn-secondary"
											onClick={() => {
												if (!config.radarr_instances) {
													updateConfig('radarr_instances', []);
												}
												addArrayItem('radarr_instances', {
													name: '',
													url: '',
													api_key: '',
													enabled: false,
												});
											}}
										>
											+ Add Radarr Instance
										</button>
									</div>
								)}

								{activeTab === 'overseerr' && (
									<div className="settings-section">
										<h3>Overseerr Settings</h3>
										<SettingItem
											label="Enabled"
											description="Enable Overseerr integration"
										>
											<Toggle
												checked={config.overseerr?.enabled ?? false}
												onChange={(e) =>
													updateConfig('overseerr.enabled', e.target.checked)
												}
											/>
										</SettingItem>
										<SettingItem
											label="Base URL"
											description="Overseerr base URL"
										>
											<input
												type="text"
												value={config.overseerr?.base_url || ''}
												onChange={(e) =>
													updateConfig('overseerr.base_url', e.target.value)
												}
												placeholder="http://overseerr:5055"
												disabled={!config.overseerr?.enabled}
											/>
										</SettingItem>
										<SettingItem
											label="API Key"
											description="Overseerr API key"
										>
											<input
												type="password"
												value={config.overseerr?.api_key || ''}
												onChange={(e) =>
													updateConfig('overseerr.api_key', e.target.value)
												}
												placeholder="API Key"
												disabled={!config.overseerr?.enabled}
											/>
										</SettingItem>
										<SettingItem
											label="Refresh Interval (minutes)"
											description="How often to sync users from Overseerr"
										>
											<input
												type="number"
												value={config.overseerr?.refresh_interval_minutes || 60}
												onChange={(e) =>
													updateConfig('overseerr.refresh_interval_minutes', parseInt(e.target.value) || 60)
												}
												min="1"
												disabled={!config.overseerr?.enabled}
											/>
										</SettingItem>
									</div>
								)}

								{activeTab === 'tmdb' && (
									<div className="settings-section">
										<h3>TMDB Settings</h3>
										<SettingItem
											label="API Key"
											description="The Movie Database API key"
										>
											<input
												type="password"
												value={config.tmdb?.api_key || ''}
												onChange={(e) =>
													updateConfig('tmdb.api_key', e.target.value)
												}
												placeholder="API Key"
											/>
										</SettingItem>
									</div>
								)}

								{activeTab === 'users' && (
									<div className="settings-section">
										<h3>User Mappings</h3>
										<p className="setting-description" style={{ marginBottom: '1.5rem' }}>
											Map Plex usernames to Discord user IDs to enable personalized notifications. When a Plex user is mentioned in notifications, the bot will DM the corresponding Discord user.
										</p>
										{config.user_mappings?.plex_to_discord && Object.keys(config.user_mappings.plex_to_discord).length > 0 ? (
											Object.entries(config.user_mappings.plex_to_discord).map(
												([plexUser, discordId], index) => (
													<div key={index} className="array-item">
														<div className="array-item-header">
															<h4>{plexUser || 'Unnamed User'}</h4>
															<button
																className="btn btn-danger btn-small"
																onClick={() => {
																	const newMappings = { ...(config.user_mappings?.plex_to_discord || {}) };
																	delete newMappings[plexUser];
																	updateConfig('user_mappings.plex_to_discord', newMappings);
																}}
															>
																Remove
															</button>
														</div>
														<SettingItem 
															label="Plex Username" 
															description="Plex username (read-only)"
														>
															<input
																type="text"
																value={plexUser}
																disabled
																style={{ opacity: 0.7 }}
															/>
														</SettingItem>
														<SettingItem 
															label="Discord User ID" 
															description="Discord user ID to receive notifications"
														>
															<input
																type="text"
																value={discordId || ''}
																onChange={(e) => {
																	const newMappings = { ...(config.user_mappings?.plex_to_discord || {}) };
																	newMappings[plexUser] = e.target.value;
																	updateConfig('user_mappings.plex_to_discord', newMappings);
																}}
																placeholder="123456789012345678"
															/>
														</SettingItem>
													</div>
												)
											)
										) : (
											<div className="empty-state" style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)', fontStyle: 'italic', marginBottom: '1rem' }}>
												No user mappings configured. Add mappings to enable personalized Discord notifications.
											</div>
										)}
										<button
											className="btn btn-secondary"
											onClick={() => {
												const plexUser = prompt('Enter Plex username:');
												if (plexUser && plexUser.trim()) {
													const newMappings = {
														...(config.user_mappings?.plex_to_discord || {}),
														[plexUser.trim()]: '',
													};
													if (!config.user_mappings) {
														updateConfig('user_mappings', { plex_to_discord: newMappings });
													} else {
														updateConfig('user_mappings.plex_to_discord', newMappings);
													}
												}
											}}
										>
											+ Add User Mapping
										</button>
									</div>
								)}

								{activeTab === 'general' && (
									<div className="settings-section">
										<h3>General Settings</h3>
										<SettingItem
											label="Log Level"
											description="Logging verbosity level"
										>
											<select
												value={config.log_level || 'info'}
												onChange={(e) => updateConfig('log_level', e.target.value)}
											>
												<option value="debug">Debug</option>
												<option value="info">Info</option>
												<option value="warning">Warning</option>
												<option value="error">Error</option>
												<option value="critical">Critical</option>
											</select>
										</SettingItem>
									</div>
								)}
							</div>

							{result && (
								<div
									className={`settings-result ${result.success ? 'success' : 'error'}`}
								>
									{result.message || result.error}
								</div>
							)}

							<div className="settings-actions">
								<button
									className="btn btn-primary"
									onClick={saveConfig}
									disabled={saving}
								>
									{saving ? 'Saving...' : 'Save Configuration'}
								</button>
								<button
									className="btn btn-secondary"
									onClick={onClose}
									disabled={saving}
								>
									Cancel
								</button>
							</div>
						</>
					) : (
						<div className="error">Failed to load configuration</div>
					)}
				</div>
		</>
	);

	if (embedded) {
		return <div className="settings-embedded">{content}</div>;
	}

	return (
		<div className="modal-overlay" onClick={onClose}>
			<div className="modal-content settings-modal-large" onClick={(e) => e.stopPropagation()}>
				{content}
			</div>
		</div>
	);
}

function SettingItem({ label, description, children }) {
	return (
		<div className="setting-item">
			<label>{label}</label>
			<div className="setting-control">{children}</div>
			{description && <p className="setting-description">{description}</p>}
		</div>
	);
}

function Toggle({ checked, onChange, disabled }) {
	return (
		<label className="toggle-switch">
			<input
				type="checkbox"
				checked={checked}
				onChange={onChange}
				disabled={disabled}
			/>
			<span className="toggle-slider"></span>
		</label>
	);
}

