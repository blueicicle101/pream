/**
 * Pream — Client-Side Application JavaScript
 */

let hlsInstance = null;

// Toast Helper
function showToast(message, isError = false) {
    const toastEl = document.getElementById('preamToast');
    const msgEl = document.getElementById('toastMessage');
    if (!toastEl || !msgEl) return;

    msgEl.textContent = message;
    toastEl.className = `toast align-items-center text-bg-${isError ? 'danger' : 'primary'} border-0 shadow`;
    const toast = new bootstrap.Toast(toastEl, { delay: 4000 });
    toast.show();
}

// Copy to Clipboard
function copyInput(elemId) {
    const input = document.getElementById(elemId);
    if (!input) return;
    input.select();
    input.setSelectionRange(0, 99999);
    window.navigator.clipboard.writeText(input.value).then(() => {
        showToast('Copied to clipboard!');
    });
}

// ── Stream Player (Video.js / HLS.js / mpegts.js + Desktop Launcher) ──
let videoJsPlayer = null;
let mpegtsPlayer = null;
let currentPlayingMeta = null;

async function playStream(streamUrl, streamTitle, itemType = '', itemId = null, containerExt = '') {
    const modalEl = document.getElementById('streamPlayerModal');
    const video = document.getElementById('preamVideo');
    const titleEl = document.getElementById('playerStreamTitle');
    const urlEl = document.getElementById('playerStreamUrl');
    const formatEl = document.getElementById('playerStreamFormat');
    const videoContainer = document.getElementById('playerVideoContainer');
    const fallbackContainer = document.getElementById('playerFallbackContainer');

    if (!modalEl || !video) return;

    currentPlayingMeta = {
        url: streamUrl,
        title: streamTitle || 'Stream Preview',
        type: itemType || (streamUrl.includes('/movie/') ? 'movie' : (streamUrl.includes('/series/') ? 'series' : 'live')),
        id: itemId,
        ext: (containerExt || (streamUrl.split('.').pop() || '')).toLowerCase().replace(/[^a-z0-9]/g, ''),
    };

    if (titleEl) titleEl.textContent = currentPlayingMeta.title;
    if (urlEl) urlEl.textContent = streamUrl;

    stopPlayer();

    // Reset view containers
    if (videoContainer) videoContainer.classList.remove('d-none');
    if (fallbackContainer) fallbackContainer.classList.add('d-none');

    // Update launcher links
    updatePlayerModalActionLinks();

    // Check if format is MKV (browsers cannot decode Matroska natively)
    const isMkv = currentPlayingMeta.ext === 'mkv' || streamUrl.toLowerCase().includes('.mkv');
    if (isMkv) {
        if (formatEl) formatEl.textContent = 'MKV (Matroska)';
        showPlayerFallback(
            'Matroska (.mkv) Format Detected',
            'Web browsers (Chrome, Firefox, Safari) cannot decode <code>.mkv</code> containers or Dolby/DTS audio natively. Use VLC or your desktop player to watch this stream with direct hardware decoding:',
            'MKV Container'
        );
        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        modal.show();
        return;
    }

    // Determine format tag
    const isLive = currentPlayingMeta.type === 'live' || streamUrl.includes('/live/');
    const isM3u8 = streamUrl.includes('.m3u8') || currentPlayingMeta.ext === 'm3u8';
    const isTs = streamUrl.includes('.ts') || currentPlayingMeta.ext === 'ts';

    if (formatEl) {
        formatEl.textContent = isM3u8 ? 'HLS (.m3u8)' : (isTs ? 'MPEG-TS (.ts)' : (currentPlayingMeta.ext ? currentPlayingMeta.ext.toUpperCase() : 'Video Stream'));
    }

    // 1. Try mpegts.js for raw MPEG-TS streams
    if (isTs && typeof mpegts !== 'undefined' && mpegts.isSupported()) {
        try {
            mpegtsPlayer = mpegts.createPlayer({
                type: 'mse',
                isLive: isLive,
                url: streamUrl,
            }, {
                enableWorker: true,
                lazyLoad: false,
            });
            mpegtsPlayer.attachMediaElement(video);
            mpegtsPlayer.load();
            mpegtsPlayer.play().catch(e => console.log('mpegts autoplay:', e));
            mpegtsPlayer.on(mpegts.Events.ERROR, (errType, errDetail) => {
                console.warn('mpegts error:', errType, errDetail);
                showPlayerFallback(
                    'Unable to Stream in Browser',
                    'The upstream stream could not be decoded by the browser or requires CORS headers. Open with VLC or your desktop player below:',
                    'Playback Issue',
                    true
                );
            });
            const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
            modal.show();
            return;
        } catch (e) {
            console.warn('mpegts.js init error:', e);
        }
    }

    // 2. Try HLS.js for .m3u8 streams
    if (isM3u8 && typeof Hls !== 'undefined' && Hls.isSupported()) {
        try {
            hlsInstance = new Hls({ enableWorker: true });
            hlsInstance.loadSource(streamUrl);
            hlsInstance.attachMedia(video);
            hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => {
                video.play().catch(e => console.log('HLS play deferred:', e));
            });
            hlsInstance.on(Hls.Events.ERROR, (event, data) => {
                if (data.fatal) {
                    console.warn('HLS fatal error:', data);
                    showPlayerFallback(
                        'HLS Stream Manifest Error',
                        'The upstream stream is either offline, requires authentication headers, or blocked by browser CORS restrictions. Open in your desktop IPTV player below:',
                        'HLS Error',
                        true
                    );
                }
            });
            const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
            modal.show();
            return;
        } catch (e) {
            console.warn('HLS.js init error:', e);
        }
    }

    // 3. Try Video.js / Native HTML5 for standard MP4 / WebM
    let mimeType = 'video/mp4';
    if (streamUrl.includes('.webm')) mimeType = 'video/webm';
    else if (isM3u8) mimeType = 'application/x-mpegURL';

    if (typeof videojs !== 'undefined') {
        try {
            if (!videoJsPlayer) {
                videoJsPlayer = videojs(video, {
                    controls: true,
                    autoplay: true,
                    preload: 'auto',
                    fluid: true,
                    responsive: true,
                });
                videoJsPlayer.on('error', () => {
                    const err = videoJsPlayer.error();
                    console.warn('Video.js player error:', err);
                    showPlayerFallback(
                        'Media Could Not Be Loaded',
                        'The stream format or audio codec is not supported by this browser, or the upstream provider blocked the request. Use VLC or desktop launcher below:',
                        'Browser Codec Error',
                        true
                    );
                });
            }
            videoJsPlayer.src({ src: streamUrl, type: mimeType });
            videoJsPlayer.play().catch(e => console.log('Autoplay deferred:', e));
        } catch (e) {
            console.warn('Video.js setup fallback:', e);
            fallbackNativePlay(video, streamUrl);
        }
    } else {
        fallbackNativePlay(video, streamUrl);
    }

    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();
}

function showPlayerFallback(title, message, badgeText = 'Format Notice', showProxyRetry = false) {
    const videoContainer = document.getElementById('playerVideoContainer');
    const fallbackContainer = document.getElementById('playerFallbackContainer');
    const titleEl = document.getElementById('playerFallbackTitle');
    const msgEl = document.getElementById('playerFallbackMessage');
    const badgeEl = document.getElementById('playerFallbackBadge');
    const retryBtn = document.getElementById('playerProxyRetryBtn');

    if (videoContainer) videoContainer.classList.add('d-none');
    if (fallbackContainer) fallbackContainer.classList.remove('d-none');

    if (titleEl) titleEl.textContent = title;
    if (msgEl) msgEl.innerHTML = message;
    if (badgeEl) badgeEl.innerHTML = `<i class="fa-solid fa-triangle-exclamation me-1"></i> ${badgeText}`;
    if (retryBtn) {
        if (showProxyRetry && currentPlayingMeta && currentPlayingMeta.id) {
            retryBtn.classList.remove('d-none');
        } else {
            retryBtn.classList.add('d-none');
        }
    }

    updatePlayerModalActionLinks();
}

function updatePlayerModalActionLinks() {
    if (!currentPlayingMeta) return;

    const streamUrl = currentPlayingMeta.url || '';
    const vlcBtn = document.getElementById('playerVlcBtn');
    const launcherBtn = document.getElementById('playerLauncherBtn');
    const footerLink = document.getElementById('footerLauncherLink');

    // vlc:// protocol URL
    if (vlcBtn) {
        const fullStreamUrl = streamUrl.startsWith('http') ? streamUrl : `${window.location.origin}${streamUrl}`;
        vlcBtn.href = `vlc://${fullStreamUrl}`;
    }

    // .m3u launcher endpoint
    let launcherUrl = streamUrl;
    if (currentPlayingMeta.id && currentPlayingMeta.type) {
        launcherUrl = `/api/proxy/stream-launcher/${currentPlayingMeta.type}/${currentPlayingMeta.id}`;
    }
    if (launcherBtn) launcherBtn.href = launcherUrl;
    if (footerLink) footerLink.href = launcherUrl;
}

function copyPlayerStreamUrl() {
    if (!currentPlayingMeta || !currentPlayingMeta.url) return;
    const fullUrl = currentPlayingMeta.url.startsWith('http') ? currentPlayingMeta.url : `${window.location.origin}${currentPlayingMeta.url}`;
    window.navigator.clipboard.writeText(fullUrl).then(() => {
        showToast('Stream URL copied to clipboard! Paste into VLC or your player.');
    });
}

function retryWithProxy() {
    if (!currentPlayingMeta || !currentPlayingMeta.id || !currentPlayingMeta.type) return;
    const proxyUrl = `/api/proxy/stream-preview/${currentPlayingMeta.type}/${currentPlayingMeta.id}`;
    showToast('Retrying playback via Pream preview proxy...');
    playStream(proxyUrl, currentPlayingMeta.title, currentPlayingMeta.type, currentPlayingMeta.id, currentPlayingMeta.ext);
}

function fallbackNativePlay(video, streamUrl) {
    if ((streamUrl.includes('.m3u8') || streamUrl.includes('.ts')) && typeof Hls !== 'undefined' && Hls.isSupported()) {
        hlsInstance = new Hls({ enableWorker: true });
        hlsInstance.loadSource(streamUrl);
        hlsInstance.attachMedia(video);
        hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => {
            video.play().catch(e => console.log('Autoplay deferred:', e));
        });
        hlsInstance.on(Hls.Events.ERROR, (event, data) => {
            if (data.fatal) {
                console.warn('HLS fatal error:', data);
                showPlayerFallback(
                    'Stream Load Error',
                    'The stream could not be loaded in browser. Open directly in VLC or desktop player below:',
                    'Playback Error',
                    true
                );
            }
        });
    } else {
        video.src = streamUrl;
        video.play().catch(e => console.log('Autoplay deferred:', e));
    }
}

function stopPlayer() {
    if (mpegtsPlayer) {
        try {
            mpegtsPlayer.pause();
            mpegtsPlayer.unload();
            mpegtsPlayer.detachMediaElement();
            mpegtsPlayer.destroy();
        } catch (e) {}
        mpegtsPlayer = null;
    }
    if (videoJsPlayer) {
        try {
            videoJsPlayer.pause();
            videoJsPlayer.reset();
        } catch (e) {}
    }
    if (hlsInstance) {
        try {
            hlsInstance.destroy();
        } catch (e) {}
        hlsInstance = null;
    }
    const video = document.getElementById('preamVideo');
    if (video) {
        try {
            video.pause();
            video.removeAttribute('src');
            video.load();
        } catch (e) {}
    }
}

// ── Sources Operations ──
function toggleSourceFields() {
    const type = document.getElementById('sourceType')?.value;
    const creds = document.getElementById('xtreamCredentials');
    if (creds) {
        creds.style.display = (type === 'xtream') ? 'block' : 'none';
    }
}

function toggleEditSourceFields() {
    const type = document.getElementById('editSourceType')?.value;
    const creds = document.getElementById('editXtreamCredentials');
    if (creds) {
        creds.style.display = (type === 'xtream') ? 'block' : 'none';
    }
}

async function submitAddSource(e, selectCategoriesAfter = false) {
    if (e && e.preventDefault) e.preventDefault();
    const btn = document.getElementById('btnSaveSource');
    const btnCat = document.getElementById('btnSaveAndSelectCategories');
    if (btn) btn.disabled = true;
    if (btnCat) btnCat.disabled = true;

    const contentTypes = [];
    if (document.getElementById('ctLive').checked) contentTypes.push('live');
    if (document.getElementById('ctVod').checked) contentTypes.push('vod');
    if (document.getElementById('ctSeries').checked) contentTypes.push('series');

    const payload = {
        name: document.getElementById('sourceName').value.trim(),
        type: document.getElementById('sourceType').value,
        url: document.getElementById('sourceUrl').value.trim(),
        username: document.getElementById('sourceUsername')?.value.trim() || null,
        password: document.getElementById('sourcePassword')?.value.trim() || null,
        content_types: contentTypes,
        priority: parseInt(document.getElementById('sourcePriority').value, 10) || 10,
        user_agent: document.getElementById('sourceUA').value.trim() || null,
        enabled: document.getElementById('sourceEnabled').checked,
    };

    try {
        const resp = await fetch('/api/sources', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to save source');
        }

        const newSource = await resp.json();
        showToast('Source added successfully!');

        if (selectCategoriesAfter && newSource.type === 'xtream') {
            const addModalEl = document.getElementById('addSourceModal');
            if (addModalEl) {
                const addModal = bootstrap.Modal.getInstance(addModalEl);
                if (addModal) addModal.hide();
            }
            openCategoryFilterModal(newSource.id, newSource.name);
        } else {
            setTimeout(() => location.reload(), 600);
        }
    } catch (err) {
        showToast(err.message, true);
        if (btn) btn.disabled = false;
        if (btnCat) btnCat.disabled = false;
    }
}

// ── Test Source Connection ──
async function testSourceConnection(modalType) {
    const isEdit = (modalType === 'edit');
    const btn = document.getElementById(isEdit ? 'btnTestEditSource' : 'btnTestAddSource');
    const resultDiv = document.getElementById(isEdit ? 'editSourceTestResult' : 'addSourceTestResult');

    const prefix = isEdit ? 'editSource' : 'source';
    const type = document.getElementById(`${prefix}Type`).value;
    const url = document.getElementById(`${prefix}Url`).value.trim();
    const username = document.getElementById(`${prefix}Username`)?.value.trim() || null;
    const password = document.getElementById(`${prefix}Password`)?.value.trim() || null;
    const userAgent = document.getElementById(isEdit ? 'editSourceUA' : 'sourceUA')?.value.trim() || null;

    if (!url) {
        if (resultDiv) {
            resultDiv.innerHTML = `
                <div class="alert alert-warning py-2 px-3 fs-8 mb-0">
                    <i class="fa-solid fa-triangle-exclamation me-1"></i> Please enter a Provider URL before testing.
                </div>`;
        }
        return;
    }

    if (type === 'xtream' && !username) {
        if (resultDiv) {
            resultDiv.innerHTML = `
                <div class="alert alert-warning py-2 px-3 fs-8 mb-0">
                    <i class="fa-solid fa-triangle-exclamation me-1"></i> Please enter a Username for Xtream authentication.
                </div>`;
        }
        return;
    }

    const originalBtnHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin me-1"></i> Testing...`;
    }
    if (resultDiv) {
        resultDiv.innerHTML = `
            <div class="text-secondary fs-8 py-1">
                <i class="fa-solid fa-spinner fa-spin me-1 text-primary"></i> Connecting to provider...
            </div>`;
    }

    try {
        const resp = await fetch('/api/sources/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                type: type,
                url: url,
                username: username,
                password: password,
                user_agent: userAgent
            })
        });

        const data = await resp.json();
        if (data.status === 'ok') {
            let details = '';
            if (data.formatted_expiry || data.days_remaining !== null) {
                const daysText = data.days_remaining !== null 
                    ? (data.days_remaining > 0 ? `${data.days_remaining} days left` : (data.days_remaining === 0 ? 'Expires today' : 'Expired'))
                    : '';
                const dateDetail = data.formatted_expiry ? `(${data.formatted_expiry})` : '';
                details += `<div><strong>Expiry:</strong> <span class="badge bg-success-subtle text-success border border-success-subtle">${daysText || 'Active'}</span> <small class="text-secondary">${dateDetail}</small></div>`;
            }
            if (data.max_connections) {
                const active = data.active_connections ? `${data.active_connections} active / ` : '';
                details += `<div><strong>Connections:</strong> <span class="badge bg-light text-dark border">${active}${data.max_connections} allowed</span></div>`;
            }
            if (data.account_status) {
                details += `<div><strong>Account Status:</strong> <span class="text-capitalize text-success fw-semibold">${data.account_status}</span></div>`;
            }

            resultDiv.innerHTML = `
                <div class="alert alert-success py-2 px-3 fs-8 mb-0 border border-success-subtle">
                    <div class="fw-bold text-success mb-1">
                        <i class="fa-solid fa-circle-check me-1"></i> ${data.message}
                    </div>
                    ${details}
                </div>`;
        } else {
            resultDiv.innerHTML = `
                <div class="alert alert-danger py-2 px-3 fs-8 mb-0 border border-danger-subtle">
                    <div class="fw-bold text-danger mb-1">
                        <i class="fa-solid fa-circle-xmark me-1"></i> Connection Test Failed
                    </div>
                    <div class="text-secondary">${data.message || 'Authentication rejected by provider'}</div>
                </div>`;
        }
    } catch (err) {
        if (resultDiv) {
            resultDiv.innerHTML = `
                <div class="alert alert-danger py-2 px-3 fs-8 mb-0 border border-danger-subtle">
                    <div class="fw-bold text-danger mb-1">
                        <i class="fa-solid fa-triangle-exclamation me-1"></i> Network Error
                    </div>
                    <div class="text-secondary">${err.message}</div>
                </div>`;
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalBtnHtml;
        }
    }
}

async function openEditSourceModal(id) {
    try {
        const resp = await fetch(`/api/sources/${id}`);
        if (!resp.ok) throw new Error('Could not fetch source');
        const s = await resp.json();

        document.getElementById('editSourceId').value = s.id;
        document.getElementById('editSourceName').value = s.name;
        document.getElementById('editSourceType').value = s.type;
        document.getElementById('editSourceUrl').value = s.url;
        document.getElementById('editSourceUsername').value = s.username || '';
        document.getElementById('editSourcePassword').value = '';
        document.getElementById('editSourcePriority').value = s.priority || 10;
        document.getElementById('editSourceUA').value = s.user_agent || '';
        document.getElementById('editSourceEnabled').checked = s.enabled !== false;

        let cts = [];
        try {
            cts = typeof s.content_types === 'string' ? JSON.parse(s.content_types) : s.content_types;
        } catch (e) {
            cts = ['live', 'vod', 'series'];
        }
        document.getElementById('editCtLive').checked = cts.includes('live');
        document.getElementById('editCtVod').checked = cts.includes('vod');
        document.getElementById('editCtSeries').checked = cts.includes('series');

        toggleEditSourceFields();

        const modalEl = document.getElementById('editSourceModal');
        const modal = new bootstrap.Modal(modalEl);
        modal.show();
    } catch (err) {
        showToast(err.message, true);
    }
}

async function submitEditSource(e) {
    e.preventDefault();
    const btn = document.getElementById('btnUpdateSource');
    if (btn) btn.disabled = true;

    const id = document.getElementById('editSourceId').value;
    const contentTypes = [];
    if (document.getElementById('editCtLive').checked) contentTypes.push('live');
    if (document.getElementById('editCtVod').checked) contentTypes.push('vod');
    if (document.getElementById('editCtSeries').checked) contentTypes.push('series');

    const payload = {
        name: document.getElementById('editSourceName').value.trim(),
        type: document.getElementById('editSourceType').value,
        url: document.getElementById('editSourceUrl').value.trim(),
        username: document.getElementById('editSourceUsername')?.value.trim() || null,
        content_types: contentTypes,
        priority: parseInt(document.getElementById('editSourcePriority').value, 10) || 10,
        user_agent: document.getElementById('editSourceUA').value.trim() || null,
        enabled: document.getElementById('editSourceEnabled').checked,
    };

    const pwd = document.getElementById('editSourcePassword')?.value.trim();
    if (pwd) payload.password = pwd;

    try {
        const resp = await fetch(`/api/sources/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to update source');
        }

        showToast('Source updated successfully!');
        setTimeout(() => location.reload(), 600);
    } catch (err) {
        showToast(err.message, true);
        if (btn) btn.disabled = false;
    }
}

let pendingDeleteSourceId = null;
let pendingDeleteSourceName = null;

async function deleteSource(id, name) {
    pendingDeleteSourceId = id;
    pendingDeleteSourceName = name;

    const modalEl = document.getElementById('deleteSourceModal');
    if (!modalEl) {
        if (!confirm(`Are you sure you want to delete source "${name}" and all its ingested channels?`)) {
            return;
        }
        return executeDeleteSource(id, name);
    }

    // Set modal display
    const nameEl = document.getElementById('deleteSourceName');
    if (nameEl) nameEl.textContent = `"${name}"`;

    const loadingEl = document.getElementById('deleteSourceStatsLoading');
    const statsEl = document.getElementById('deleteSourceStats');
    const confirmBtn = document.getElementById('btnConfirmDeleteSource');

    if (loadingEl) {
        loadingEl.classList.remove('d-none');
        loadingEl.innerHTML = `
            <i class="fa-solid fa-spinner fa-spin text-primary fs-3 mb-2"></i>
            <div class="text-muted fs-7">Calculating streams to be removed...</div>
        `;
    }
    if (statsEl) statsEl.classList.add('d-none');
    if (confirmBtn) confirmBtn.disabled = true;

    const bsModal = new bootstrap.Modal(modalEl);
    bsModal.show();

    try {
        const resp = await fetch(`/api/sources/${id}/counts`);
        if (!resp.ok) throw new Error('Could not fetch source stream counts');
        const data = await resp.json();

        const ch = (data.channels || 0).toLocaleString();
        const mv = (data.movies || 0).toLocaleString();
        const sr = (data.series || 0).toLocaleString();
        const tot = (data.total || 0).toLocaleString();

        const elCh = document.getElementById('delCountChannels');
        const elMv = document.getElementById('delCountMovies');
        const elSr = document.getElementById('delCountSeries');
        const elTot = document.getElementById('delCountTotal');

        if (elCh) elCh.textContent = ch;
        if (elMv) elMv.textContent = mv;
        if (elSr) elSr.textContent = sr;
        if (elTot) elTot.textContent = tot;

        const msgCh = document.getElementById('delMsgChannels');
        const msgMv = document.getElementById('delMsgMovies');
        const msgSr = document.getElementById('delMsgSeries');

        if (msgCh) msgCh.textContent = ch;
        if (msgMv) msgMv.textContent = mv;
        if (msgSr) msgSr.textContent = sr;

        if (loadingEl) loadingEl.classList.add('d-none');
        if (statsEl) statsEl.classList.remove('d-none');
        if (confirmBtn) confirmBtn.disabled = false;
    } catch (e) {
        if (loadingEl) {
            loadingEl.innerHTML = `<div class="text-danger fs-7 py-2"><i class="fa-solid fa-triangle-exclamation me-1"></i>Could not load item breakdown. Confirm deletion to proceed anyway.</div>`;
        }
        if (confirmBtn) confirmBtn.disabled = false;
    }
}

async function confirmDeleteSourceAction() {
    if (!pendingDeleteSourceId) return;
    const id = pendingDeleteSourceId;
    const name = pendingDeleteSourceName || `Source ${id}`;
    const btn = document.getElementById('btnConfirmDeleteSource');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin me-1"></i> Deleting...';
    }

    try {
        await executeDeleteSource(id, name);
        const modalEl = document.getElementById('deleteSourceModal');
        if (modalEl) {
            const bsModal = bootstrap.Modal.getInstance(modalEl);
            if (bsModal) bsModal.hide();
        }
    } catch (err) {
        showToast(err.message, true);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-trash me-1"></i> Delete Source & Purge Streams';
        }
        pendingDeleteSourceId = null;
        pendingDeleteSourceName = null;
    }
}

async function executeDeleteSource(id, name) {
    const resp = await fetch(`/api/sources/${id}`, { method: 'DELETE' });
    if (!resp.ok) throw new Error('Failed to delete source');

    showToast(`Source "${name}" and all associated streams deleted.`);
    const row = document.getElementById(`sourceRow-${id}`);
    if (row) row.remove();
    else location.reload();
}

// ── Category Filter Modal State & Handlers ──
let currentCategoryModalSourceId = null;
let currentCategoryModalSourceName = null;
let categoryModalData = {
    live: [],
    vod: [],
    series: []
};
let categoryModalSelection = {
    live: new Set(),
    vod: new Set(),
    series: new Set()
};

function openCategoriesFromEditModal() {
    const editModalEl = document.getElementById('editSourceModal');
    const id = document.getElementById('editSourceId')?.value;
    const name = document.getElementById('editSourceName')?.value || `Source ${id}`;
    if (!id) return;
    if (editModalEl) {
        const bsModal = bootstrap.Modal.getInstance(editModalEl);
        if (bsModal) bsModal.hide();
    }
    openCategoryFilterModal(id, name);
}

async function openCategoryFilterModal(sourceId, sourceName) {
    currentCategoryModalSourceId = sourceId;
    currentCategoryModalSourceName = sourceName || `Source ${sourceId}`;

    const modalEl = document.getElementById('categoryFilterModal');
    if (!modalEl) return;

    const bsModal = bootstrap.Modal.getOrCreateInstance(modalEl);
    bsModal.show();

    const subtitleEl = document.getElementById('catModalSourceSubtitle');
    if (subtitleEl) subtitleEl.textContent = `Source: ${currentCategoryModalSourceName} (ID: ${sourceId})`;

    const loadingEl = document.getElementById('catModalLoading');
    const contentEl = document.getElementById('catModalContent');
    const searchInput = document.getElementById('catSearchInput');
    if (searchInput) searchInput.value = '';

    if (loadingEl) loadingEl.classList.remove('d-none');
    if (contentEl) contentEl.classList.add('d-none');

    try {
        const resp = await fetch(`/api/sources/${sourceId}/categories`);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || 'Failed to fetch categories from provider');
        }

        const data = await resp.json();
        const cats = data.categories || {};
        categoryModalData = {
            live: cats.live || [],
            vod: cats.vod || [],
            series: cats.series || []
        };

        const saved = data.selected || null;
        categoryModalSelection = {
            live: new Set(),
            vod: new Set(),
            series: new Set()
        };

        ['live', 'vod', 'series'].forEach(type => {
            const typeCats = categoryModalData[type];
            if (saved && Array.isArray(saved[type])) {
                // If saved filter exists, initialize with saved category IDs
                saved[type].forEach(id => categoryModalSelection[type].add(String(id)));
            } else {
                // Default: intelligent preselection (recommended: all categories not hidden by rules)
                typeCats.forEach(c => {
                    if (c.recommended) {
                        categoryModalSelection[type].add(String(c.id));
                    }
                });
            }
        });

        renderCategoryTabs();
        if (loadingEl) loadingEl.classList.add('d-none');
        if (contentEl) contentEl.classList.remove('d-none');
    } catch (err) {
        if (loadingEl) {
            loadingEl.innerHTML = `
                <div class="alert alert-danger py-3 px-4 text-center">
                    <i class="fa-solid fa-triangle-exclamation fs-3 mb-2 d-block"></i>
                    <div class="fw-bold mb-1">Failed to load categories</div>
                    <div class="fs-8 text-break">${err.message}</div>
                </div>`;
        }
    }
}

function getActiveCategoryType() {
    const activeTab = document.querySelector('#catTypeTabs .nav-link.active');
    if (!activeTab) return 'live';
    const id = activeTab.id;
    if (id.includes('vod')) return 'vod';
    if (id.includes('series')) return 'series';
    return 'live';
}

function renderCategoryTabs() {
    ['live', 'vod', 'series'].forEach(type => renderCategoryList(type));
    updateCategoryBadgesAndSummary();
}

function renderCategoryList(type) {
    const listEl = document.getElementById(`catList${type.charAt(0).toUpperCase() + type.slice(1)}`);
    if (!listEl) return;

    const items = categoryModalData[type] || [];
    if (items.length === 0) {
        listEl.innerHTML = `<div class="col-12 text-center text-muted py-4 fs-7">No categories found for this content type.</div>`;
        return;
    }

    const query = (document.getElementById('catSearchInput')?.value || '').toLowerCase().trim();
    const selectedSet = categoryModalSelection[type];

    let html = '';
    items.forEach(cat => {
        const matchesSearch = !query || cat.name.toLowerCase().includes(query) || String(cat.id).includes(query);
        const isChecked = selectedSet.has(String(cat.id));
        const hiddenBadge = cat.hidden_by_rule
            ? `<span class="badge bg-danger-subtle text-danger border border-danger-subtle ms-1 fs-9" title="Category matches a group hide rule"><i class="fa-solid fa-eye-slash"></i></span>`
            : '';

        html += `
            <div class="col-md-6 col-12 cat-item-wrapper ${matchesSearch ? '' : 'd-none'}" data-name="${escapeHtml(cat.name.toLowerCase())}" data-id="${cat.id}">
                <div class="p-2 border rounded bg-white h-100 d-flex align-items-center justify-content-between">
                    <div class="form-check text-truncate pe-2">
                        <input class="form-check-input cat-check" type="checkbox" id="catCheck_${type}_${cat.id}"
                            value="${cat.id}" ${isChecked ? 'checked' : ''} onchange="toggleCategoryItem('${type}', '${cat.id}', this.checked)">
                        <label class="form-check-label text-truncate fs-7 fw-medium" for="catCheck_${type}_${cat.id}" title="${escapeHtml(cat.name)} (ID: ${cat.id})">
                            ${escapeHtml(cat.name)}
                        </label>
                    </div>
                    <div class="d-flex align-items-center gap-1 flex-shrink-0">
                        ${hiddenBadge}
                        <span class="badge bg-light text-secondary border font-monospace fs-9">#${cat.id}</span>
                    </div>
                </div>
            </div>`;
    });

    listEl.innerHTML = html;
}

function toggleCategoryItem(type, catId, checked) {
    if (checked) {
        categoryModalSelection[type].add(String(catId));
    } else {
        categoryModalSelection[type].delete(String(catId));
    }
    updateCategoryBadgesAndSummary();
}

function filterActiveCategoryList(query) {
    const currentType = getActiveCategoryType();
    const q = (query || '').toLowerCase().trim();
    const listEl = document.getElementById(`catList${currentType.charAt(0).toUpperCase() + currentType.slice(1)}`);
    if (!listEl) return;

    const items = listEl.querySelectorAll('.cat-item-wrapper');
    items.forEach(el => {
        const name = el.getAttribute('data-name') || '';
        const id = el.getAttribute('data-id') || '';
        if (!q || name.includes(q) || id.includes(q)) {
            el.classList.remove('d-none');
        } else {
            el.classList.add('d-none');
        }
    });
}

function clearCatSearch() {
    const input = document.getElementById('catSearchInput');
    if (input) {
        input.value = '';
        filterActiveCategoryList('');
    }
}

function bulkSelectCategories(mode) {
    const currentType = getActiveCategoryType();
    const items = categoryModalData[currentType] || [];
    const set = categoryModalSelection[currentType];

    if (mode === 'all') {
        items.forEach(c => set.add(String(c.id)));
    } else if (mode === 'none') {
        set.clear();
    } else if (mode === 'recommended') {
        set.clear();
        items.forEach(c => {
            if (c.recommended) set.add(String(c.id));
        });
    }

    renderCategoryList(currentType);
    updateCategoryBadgesAndSummary();
}

function updateCategoryBadgesAndSummary() {
    const liveCount = categoryModalSelection.live.size;
    const liveTotal = categoryModalData.live.length;
    const vodCount = categoryModalSelection.vod.size;
    const vodTotal = categoryModalData.vod.length;
    const seriesCount = categoryModalSelection.series.size;
    const seriesTotal = categoryModalData.series.length;

    const bLive = document.getElementById('catBadgeLive');
    if (bLive) bLive.textContent = `${liveCount}/${liveTotal}`;

    const bVod = document.getElementById('catBadgeVod');
    if (bVod) bVod.textContent = `${vodCount}/${vodTotal}`;

    const bSeries = document.getElementById('catBadgeSeries');
    if (bSeries) bSeries.textContent = `${seriesCount}/${seriesTotal}`;

    const totalSelected = liveCount + vodCount + seriesCount;
    const totalAvailable = liveTotal + vodTotal + seriesTotal;

    const summaryEl = document.getElementById('catSelectionSummary');
    if (summaryEl) {
        summaryEl.textContent = `Total selected: ${totalSelected} / ${totalAvailable} categories`;
    }
}

async function resetAllCategoriesToDefault() {
    if (!confirm('This will remove all category filters and set this source to import all categories from the provider. Proceed?')) {
        return;
    }
    const sourceId = currentCategoryModalSourceId;
    if (!sourceId) return;

    try {
        const resp = await fetch(`/api/sources/${sourceId}/categories`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ selected: null })
        });
        if (!resp.ok) throw new Error('Failed to reset categories');

        showToast('All category filters removed. Source will import everything.');
        const modalEl = document.getElementById('categoryFilterModal');
        if (modalEl) {
            const bsModal = bootstrap.Modal.getInstance(modalEl);
            if (bsModal) bsModal.hide();
        }
        setTimeout(() => location.reload(), 600);
    } catch (err) {
        showToast(err.message, true);
    }
}

async function saveCategoryFilters(refreshAfterSave = false) {
    const sourceId = currentCategoryModalSourceId;
    if (!sourceId) return;

    const btn = document.getElementById(refreshAfterSave ? 'btnSaveAndRefreshCategories' : 'btnSaveCategories');
    const origHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin me-1"></i> Saving...`;
    }

    const payload = {
        selected: {
            live: Array.from(categoryModalSelection.live),
            vod: Array.from(categoryModalSelection.vod),
            series: Array.from(categoryModalSelection.series)
        }
    };

    try {
        const resp = await fetch(`/api/sources/${sourceId}/categories`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || 'Failed to save category filters');
        }

        showToast('Category filters saved successfully!');

        const modalEl = document.getElementById('categoryFilterModal');
        if (modalEl) {
            const bsModal = bootstrap.Modal.getInstance(modalEl);
            if (bsModal) bsModal.hide();
        }

        if (refreshAfterSave) {
            await fetch(`/api/sources/${sourceId}/refresh`, { method: 'POST' });
            showToast('Source refresh started in background with category filters applied!');
            updateFooterTaskProgress();
            setTimeout(() => location.reload(), 1000);
        } else {
            setTimeout(() => location.reload(), 600);
        }
    } catch (err) {
        showToast(err.message, true);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origHtml;
        }
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

async function triggerRefreshSource(id, btn) {
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';

    try {
        const resp = await fetch(`/api/sources/${id}/refresh`, { method: 'POST' });
        const res = await resp.json();

        if (res.status === 'started' || res.status === 'ok') {
            showToast(res.message || 'Source refresh started in background');
            updateFooterTaskProgress();
        } else {
            showToast(`Error: ${res.error_message || res.detail || 'Failed to start refresh'}`, true);
        }
    } catch (err) {
        showToast(err.message, true);
    } finally {
        btn.innerHTML = originalHtml;
        btn.disabled = false;
    }
}

async function triggerRefreshAll(btn) {
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-arrows-rotate fa-spin me-1"></i> Scanning...';

    try {
        const resp = await fetch('/api/sources/refresh', { method: 'POST' });
        const res = await resp.json();
        if (res.status === 'started' || res.status === 'completed') {
            showToast(res.message || 'Parallel refresh started in background');
            updateFooterTaskProgress();
        } else {
            showToast(`Error: ${res.error_message || res.detail || 'Failed to start refresh'}`, true);
        }
    } catch (err) {
        showToast(err.message, true);
    } finally {
        btn.innerHTML = originalHtml;
        btn.disabled = false;
    }
}

async function triggerCancelAll(btn) {
    if (!confirm('Are you sure you want to cancel all running and queued tasks (including TMDB enrichment)?')) return;
    const originalHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin me-1"></i> Cancelling...';
    }

    try {
        // Cancel both source refreshes and TMDB enrichment
        const [resp1, resp2] = await Promise.allSettled([
            fetch('/api/sources/cancel-refresh', { method: 'POST' }),
            fetch('/api/tmdb/cancel', { method: 'POST' }),
        ]);
        showToast('Cancellation requested for active tasks and enrichment.');
        updateFooterTaskProgress();
    } catch (err) {
        showToast(err.message, true);
    } finally {
        if (btn) {
            btn.innerHTML = originalHtml;
            btn.disabled = false;
        }
    }
}

function showErrorModal(sourceName, errorMsg) {
    const titleEl = document.getElementById('errorSourceTitle');
    const contentEl = document.getElementById('errorSourceContent');
    const modalEl = document.getElementById('sourceErrorModal');

    if (titleEl) titleEl.textContent = sourceName;
    if (contentEl) contentEl.textContent = errorMsg || 'Unknown error occurred.';
    if (modalEl) {
        const modal = new bootstrap.Modal(modalEl);
        modal.show();
    }
}

// ── Rules Operations ──
function toggleRuleInputs(typeId = 'ruleType', repGroupId = 'ruleReplacementGroup', newGrpId = 'ruleNewGroupContainer') {
    const typeEl = document.getElementById(typeId);
    if (!typeEl) return;
    const rType = typeEl.value;
    const repContainer = document.getElementById(repGroupId);
    const newGrpContainer = document.getElementById(newGrpId);

    const isCase = rType.includes('case') || rType.startsWith('upper') || rType.startsWith('lower') || rType.startsWith('capit') || rType.startsWith('title');

    if (rType.startsWith('hide_') || isCase) {
        if (repContainer) repContainer.classList.add('d-none');
        if (newGrpContainer) newGrpContainer.classList.add('d-none');
    } else if (rType === 'set_group') {
        if (repContainer) repContainer.classList.add('d-none');
        if (newGrpContainer) newGrpContainer.classList.remove('d-none');
    } else {
        if (repContainer) repContainer.classList.remove('d-none');
        if (newGrpContainer) newGrpContainer.classList.add('d-none');
    }

    // Auto-select match field based on rule target if present
    const fieldId = typeId === 'editRuleType' ? 'editRuleMatchField' : 'ruleMatchField';
    const fieldEl = document.getElementById(fieldId);
    if (fieldEl) {
        if (rType.endsWith('_group') && fieldEl.value !== 'group') {
            fieldEl.value = 'group';
        } else if (rType.endsWith('_channel') && fieldEl.value !== 'name') {
            fieldEl.value = 'name';
        }
    }
}

async function submitAddRule(e) {
    e.preventDefault();
    const payload = {
        rule_type: document.getElementById('ruleType').value,
        content_type: document.getElementById('ruleContentType').value,
        match_field: document.getElementById('ruleMatchField').value,
        match_pattern: document.getElementById('ruleMatchPattern').value.trim(),
        replacement: document.getElementById('ruleReplacement')?.value || null,
        new_group: document.getElementById('ruleNewGroup')?.value?.trim() || null,
        description: document.getElementById('ruleDescription')?.value?.trim() || null,
        enabled: document.getElementById('ruleEnabled').checked,
    };

    try {
        const resp = await fetch('/api/rules', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to save rule');
        }

        showToast('Rule created successfully.');
        setTimeout(() => location.reload(), 500);
    } catch (err) {
        showToast(err.message, true);
    }
}

async function openEditRuleModal(id) {
    try {
        const resp = await fetch(`/api/rules/${id}`);
        if (!resp.ok) throw new Error('Could not fetch rule');
        const r = await resp.json();

        document.getElementById('editRuleId').value = r.id;
        document.getElementById('editRuleType').value = r.rule_type;
        document.getElementById('editRuleContentType').value = r.content_type || 'all';
        document.getElementById('editRuleMatchField').value = r.match_field || 'group';
        document.getElementById('editRuleMatchPattern').value = r.match_pattern || '';
        document.getElementById('editRuleReplacement').value = r.replacement || '';
        document.getElementById('editRuleNewGroup').value = r.new_group || '';
        document.getElementById('editRuleDescription').value = r.description || '';
        document.getElementById('editRuleEnabled').checked = r.enabled !== false;

        toggleRuleInputs('editRuleType', 'editRuleReplacementGroup', 'editRuleNewGroupContainer');

        const modalEl = document.getElementById('editRuleModal');
        const modal = new bootstrap.Modal(modalEl);
        modal.show();
    } catch (err) {
        showToast(err.message, true);
    }
}

async function submitEditRule(e) {
    e.preventDefault();
    const id = document.getElementById('editRuleId').value;
    const payload = {
        rule_type: document.getElementById('editRuleType').value,
        content_type: document.getElementById('editRuleContentType').value,
        match_field: document.getElementById('editRuleMatchField').value,
        match_pattern: document.getElementById('editRuleMatchPattern').value.trim(),
        replacement: document.getElementById('editRuleReplacement')?.value || null,
        new_group: document.getElementById('editRuleNewGroup')?.value?.trim() || null,
        description: document.getElementById('editRuleDescription')?.value?.trim() || null,
        enabled: document.getElementById('editRuleEnabled').checked,
    };

    try {
        const resp = await fetch(`/api/rules/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to update rule');
        }

        showToast('Rule updated successfully.');
        setTimeout(() => location.reload(), 500);
    } catch (err) {
        showToast(err.message, true);
    }
}

async function deleteRule(id) {
    if (!confirm('Are you sure you want to delete this rule?')) return;
    try {
        const resp = await fetch(`/api/rules/${id}`, { method: 'DELETE' });
        if (!resp.ok) throw new Error('Failed to delete rule');
        showToast('Rule deleted.');
        document.getElementById(`ruleRow-${id}`)?.remove();
    } catch (err) {
        showToast(err.message, true);
    }
}

async function triggerApplyRules(btn) {
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin me-1"></i> Applying...';
    updateFooterTaskProgress();

    try {
        const resp = await fetch('/api/rules/apply', { method: 'POST' });
        const res = await resp.json();
        showToast(`Rules applied! Processed: ${res.channels_processed} TV (${res.channels_hidden} hidden), ${res.movies_processed} VOD.`);
        updateFooterTaskProgress();
        setTimeout(() => location.reload(), 1200);
    } catch (err) {
        showToast(err.message, true);
    } finally {
        btn.innerHTML = originalHtml;
        btn.disabled = false;
    }
}

async function moveRuleUp(ruleId) {
    const rows = Array.from(document.querySelectorAll('#rulesTableBody tr[data-rule-id]'));
    const ids = rows.map(r => parseInt(r.getAttribute('data-rule-id'), 10));
    const idx = ids.indexOf(ruleId);
    if (idx <= 0) return;

    // Swap
    [ids[idx - 1], ids[idx]] = [ids[idx], ids[idx - 1]];
    await postRuleReorder(ids);
}

async function moveRuleDown(ruleId) {
    const rows = Array.from(document.querySelectorAll('#rulesTableBody tr[data-rule-id]'));
    const ids = rows.map(r => parseInt(r.getAttribute('data-rule-id'), 10));
    const idx = ids.indexOf(ruleId);
    if (idx < 0 || idx >= ids.length - 1) return;

    // Swap
    [ids[idx], ids[idx + 1]] = [ids[idx + 1], ids[idx]];
    await postRuleReorder(ids);
}

async function postRuleReorder(ruleIds) {
    try {
        const resp = await fetch('/api/rules/reorder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rule_ids: ruleIds }),
        });
        if (resp.ok) location.reload();
    } catch (err) {
        showToast(err.message, true);
    }
}

async function runRuleTest() {
    const payload = {
        rule_type: document.getElementById('testRuleType').value,
        content_type: 'all',
        match_field: document.getElementById('testRuleType').value.includes('group') ? 'group' : 'name',
        match_pattern: document.getElementById('testPattern').value,
        replacement: document.getElementById('testReplacement').value,
        new_group: document.getElementById('testReplacement').value,
        sample_name: document.getElementById('testSampleName').value,
        sample_group: document.getElementById('testSampleGroup').value,
    };

    try {
        const resp = await fetch('/api/rules/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const res = await resp.json();

        const badge = document.getElementById('testMatchedBadge');
        if (badge) {
            badge.className = `badge bg-${res.matched ? 'success' : 'secondary'}`;
            badge.textContent = res.matched ? 'Matched' : 'No Match';
        }

        document.getElementById('testOutName').textContent = res.output_name;
        document.getElementById('testOutGroup').textContent = res.output_group;
        document.getElementById('testOutHidden').textContent = res.is_hidden ? 'Yes (Hidden)' : 'No';

        if (res.error) {
            showToast(res.error, true);
        }
    } catch (err) {
        showToast(err.message, true);
    }
}

// ── Rules 2-Column Live Diff Preview ──
async function loadRulesDiff(scope) {
    const tbody = document.getElementById('rulesDiffTableBody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="2" class="text-center py-4 text-muted"><div class="spinner-border spinner-border-sm me-2"></div>Loading comparison...</td></tr>';

    try {
        const resp = await fetch(`/api/rules/diff?scope=${encodeURIComponent(scope)}&limit=150`);
        const items = await resp.json();

        if (!items || items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="2" class="text-center py-4 text-muted">No items found for this scope.</td></tr>';
            return;
        }

        let html = '';
        for (const it of items) {
            let rowClass = '';
            if (it.is_hidden) rowClass = 'row-hidden';
            else if (it.changed) rowClass = 'row-changed';

            let badgeHtml = '<span class="badge bg-light text-secondary border">Unchanged</span>';
            if (it.is_hidden) {
                badgeHtml = '<span class="badge bg-danger">Hidden</span>';
            } else if (it.is_renamed && it.is_group_changed) {
                badgeHtml = '<span class="badge bg-success-subtle text-success border border-success-subtle">Renamed & Moved</span>';
            } else if (it.is_renamed) {
                badgeHtml = '<span class="badge bg-info-subtle text-info border border-info-subtle">Renamed</span>';
            } else if (it.is_group_changed) {
                badgeHtml = '<span class="badge bg-warning-subtle text-warning border border-warning-subtle">Group Moved</span>';
            }

            html += `
            <tr class="diff-row ${rowClass}" data-changed="${it.changed ? 'true' : 'false'}">
                <td>
                    <div class="fw-semibold text-dark">${escapeHtml(it.raw_name)}</div>
                    <small class="text-muted"><i class="fa-regular fa-folder me-1"></i>${escapeHtml(it.raw_group)}</small>
                </td>
                <td>
                    <div class="d-flex justify-content-between align-items-center">
                        <div>
                            <span class="fw-semibold text-dark">${escapeHtml(it.display_name)}</span>
                            <small class="text-secondary d-block"><i class="fa-regular fa-folder me-1"></i>${escapeHtml(it.display_group)}</small>
                        </div>
                        <div>${badgeHtml}</div>
                    </div>
                </td>
            </tr>`;
        }
        tbody.innerHTML = html;

        const onlyChanged = document.getElementById('onlyChangedSwitch')?.checked;
        if (onlyChanged) toggleOnlyChanged(true);
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="2" class="text-center py-4 text-danger">Failed to load preview: ${err.message}</td></tr>`;
    }
}

function filterDiffTable(query) {
    const q = (query || '').toLowerCase().trim();
    const rows = document.querySelectorAll('#rulesDiffTableBody tr.diff-row');
    rows.forEach(r => {
        const text = r.textContent.toLowerCase();
        if (!q || text.includes(q)) {
            r.style.display = '';
        } else {
            r.style.display = 'none';
        }
    });
}

function toggleOnlyChanged(onlyChanged) {
    const rows = document.querySelectorAll('#rulesDiffTableBody tr.diff-row');
    rows.forEach(r => {
        const isChanged = r.getAttribute('data-changed') === 'true';
        if (onlyChanged && !isChanged) {
            r.style.display = 'none';
        } else {
            r.style.display = '';
        }
    });
}

// ── Series Episodes Collection Operations ──
let currentSeriesEpisodesData = null;

async function openSeriesEpisodesModal(seriesId, seriesTitle = '') {
    const modalEl = document.getElementById('seriesEpisodesModal');
    if (!modalEl) return;

    const modal = new bootstrap.Modal(modalEl);
    modal.show();

    document.getElementById('seriesModalTitle').textContent = seriesTitle || 'Series Episodes';
    document.getElementById('seriesLoadingSpinner').classList.remove('d-none');
    document.getElementById('seriesModalContent').classList.add('d-none');

    try {
        const resp = await fetch(`/api/library/series/${seriesId}/episodes`);
        if (!resp.ok) throw new Error('Failed to load series episodes');
        const data = await resp.json();
        currentSeriesEpisodesData = data;

        document.getElementById('seriesModalTitle').textContent = data.name || seriesTitle || 'Series Episodes';
        document.getElementById('seriesModalPoster').src = data.poster_url || '/static/img/placeholder_channel.png';
        document.getElementById('seriesModalName').textContent = data.name;
        document.getElementById('seriesModalYear').textContent = data.year ? `Year: ${data.year}` : 'Year: N/A';
        document.getElementById('seriesModalRating').innerHTML = data.rating ? `<i class="fa-solid fa-star me-1 text-warning"></i>${data.rating.toFixed(1)}` : 'No rating';
        document.getElementById('seriesModalGenre').textContent = data.genre || 'Series';
        document.getElementById('seriesModalPlot').textContent = data.plot || 'No plot summary available.';

        // Render Season Pills
        const tabsContainer = document.getElementById('seriesSeasonsTabs');
        tabsContainer.innerHTML = '';
        const seasonKeys = Object.keys(data.seasons || {}).sort((a, b) => parseInt(a) - parseInt(b));

        if (seasonKeys.length === 0) {
            document.getElementById('seriesEpisodesList').innerHTML = '<div class="text-center py-4 text-muted">No episodes recorded for this collection yet.</div>';
        } else {
            seasonKeys.forEach((sn, idx) => {
                const li = document.createElement('li');
                li.className = 'nav-item';
                const count = (data.seasons[sn] || []).length;
                li.innerHTML = `
                    <button class="nav-link ${idx === 0 ? 'active' : ''} btn-sm me-1" 
                            onclick="selectSeriesSeason('${sn}', this)">
                        Season ${sn} <span class="badge bg-light text-dark ms-1">${count}</span>
                    </button>
                `;
                tabsContainer.appendChild(li);
            });
            renderSeriesEpisodesList(seasonKeys[0]);
        }

        document.getElementById('seriesLoadingSpinner').classList.add('d-none');
        document.getElementById('seriesModalContent').classList.remove('d-none');
    } catch (err) {
        document.getElementById('seriesLoadingSpinner').innerHTML = `<div class="text-danger py-4 text-center">${err.message}</div>`;
    }
}

function selectSeriesSeason(seasonNum, btn) {
    document.querySelectorAll('#seriesSeasonsTabs .nav-link').forEach(el => el.classList.remove('active'));
    btn.classList.add('active');
    renderSeriesEpisodesList(seasonNum);
}

function renderSeriesEpisodesList(seasonNum) {
    const listEl = document.getElementById('seriesEpisodesList');
    if (!listEl || !currentSeriesEpisodesData) return;

    const episodes = currentSeriesEpisodesData.seasons[seasonNum] || [];
    if (episodes.length === 0) {
        listEl.innerHTML = '<div class="text-center py-3 text-muted">No episodes found in this season.</div>';
        return;
    }

    let html = '';
    episodes.forEach(ep => {
        html += `
        <div class="card border p-2 d-flex flex-row align-items-center justify-content-between hover-shadow">
            <div class="d-flex align-items-center gap-3">
                <span class="badge bg-primary text-white rounded-pill px-2 py-1 fs-8">E${ep.episode_num}</span>
                <div>
                    <h6 class="mb-0 fs-7 fw-semibold text-dark">${escapeHtml(ep.title)}</h6>
                    <small class="text-muted fs-8 font-monospace">Format: ${ep.container_ext || 'mp4'}</small>
                </div>
            </div>
            <button class="btn btn-sm btn-primary rounded-pill px-3" onclick="playStream('${ep.stream_url}', '${escapeHtml(ep.title)}', 'series', ${ep.id}, '${ep.container_ext || 'mp4'}')">
                <i class="fa-solid fa-play me-1"></i> Play
            </button>
        </div>
        `;
    });
    listEl.innerHTML = html;
}

// ── Central Library Live Category and Media Search Filters ──
function filterCategories(query, useRegex = false) {
    useRegex = document.getElementById('regexMode').classList.contains('bg-primary');
    console.log("Regex Mode:",useRegex)
    const list = document.getElementById('categorySidebarList');
    if (!list) {
        return;
    }
    
    const items = list.querySelectorAll('.category-item');
    const q = (query || '').trim();
    
    let regex = null;
    if (q && useRegex) {
        try {
            // Case-insensitive regex matching
            regex = new RegExp(q, 'i');
        } catch (e) {
            console.warn("Invalid regex query:", e);
            return;
        }
    }

    const lowerQ = q.toLowerCase();

    items.forEach(item => {
        const catName = item.getAttribute('data-category-name') || '';
        // console.log(catName);
        
        // Always show 'all categories'
        if (catName.toLowerCase() === 'all categories') {
            item.style.display = '';
            return;
        }
        
        // if (!q) {
        //     item.style.display = '';
        //     return;
        // }

        let isMatch = false;
        if (useRegex && regex) {
            // Regex mode (uses original catName so \b works correctly)
            isMatch = regex.test(catName);
        } else {
            // Standard substring mode (case-insensitive)
            isMatch = catName.toLowerCase().includes(lowerQ);
        }

        if (isMatch) {
            item.style.display = 'block';
            item.classList.remove('hide');
            
        } else {
            item.style.display = 'none !important';
            item.classList.add('hide');
        }
    });
}

function filterMediaCards(query) {
    const grid = document.getElementById('mediaCardsGrid');
    if (!grid) return;
    const cards = grid.querySelectorAll('.media-card-col');
    const q = (query || '').toLowerCase().trim();

    let visibleCount = 0;
    cards.forEach(col => {
        const title = (col.getAttribute('data-media-title') || '').toLowerCase();
        const raw = (col.getAttribute('data-media-raw') || '').toLowerCase();
        if (!q || title.includes(q) || raw.includes(q)) {
            col.style.display = '';
            visibleCount++;
        } else {
            col.style.display = 'none';
        }
    });

    const noMatchesEl = document.getElementById('noMediaMatchesAlert');
    if (noMatchesEl) {
        if (visibleCount === 0 && q) {
            noMatchesEl.classList.remove('d-none');
        } else {
            noMatchesEl.classList.add('d-none');
        }
    }
}

// ── TMDB & Telegram Operations ──
async function triggerEnrichTMDB(btn) {
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin me-1"></i> Enriching...';
    updateFooterTaskProgress();

    try {
        const resp = await fetch('/api/tmdb/enrich?batch_size=100&continuous=true', { method: 'POST' });
        const res = await resp.json();
        updateFooterTaskProgress();
        if (res.status === 'ok') {
            const remainingTxt = res.catalog_remaining > 0 ? ` (${res.catalog_remaining} remaining)` : ' (All media enriched!)';
            showToast(`TMDB enrichment completed: ${res.movies_enriched} movies, ${res.series_enriched} series updated${remainingTxt}.`);
        } else {
            showToast(`TMDB enrichment skipped: ${res.reason || 'Check API key or settings'}`, true);
        }
    } catch (err) {
        showToast(err.message, true);
    } finally {
        btn.innerHTML = originalHtml;
        btn.disabled = false;
        updateFooterTaskProgress();
    }
}

async function enrichCurrentGroup(groupName, contentType, btn) {
    if (!confirm(`Enrich all pending ${contentType === 'series' ? 'series' : 'movies'} in "${groupName}" via TMDB?`)) return;
    const originalHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin me-1"></i> Enriching Category...';
    }
    updateFooterTaskProgress();

    try {
        const resp = await fetch(`/api/tmdb/enrich?group=${encodeURIComponent(groupName)}&content_type=${encodeURIComponent(contentType)}&batch_size=100`, { method: 'POST' });
        const res = await resp.json();
        updateFooterTaskProgress();
        if (res.status === 'ok') {
            showToast(`Category enriched! ${res.movies_enriched || res.series_enriched || 0} items updated.`);
            setTimeout(() => window.location.reload(), 1200);
        } else {
            showToast(`Enrichment skipped: ${res.reason || 'Error'}`, true);
        }
    } catch (err) {
        showToast(err.message, true);
    } finally {
        if (btn) {
            btn.innerHTML = originalHtml;
            btn.disabled = false;
        }
    }
}

async function enrichSingleItem(contentType, itemId, btn) {
    const originalHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
    }

    try {
        const endpoint = contentType === 'series' ? `/api/tmdb/enrich/series/${itemId}` : `/api/tmdb/enrich/movie/${itemId}`;
        const resp = await fetch(endpoint, { method: 'POST' });
        const res = await resp.json();
        if (res.status === 'ok') {
            const dupMsg = res.duplicates_synced > 0 ? ` (and synced ${res.duplicates_synced} duplicate stream(s))` : '';
            showToast(`Enriched "${res.title}" via TMDB${dupMsg}!`);
            setTimeout(() => window.location.reload(), 800);
        } else if (res.status === 'not_found') {
            showToast(`No automatic match for "${res.title}". Opening search...`);
            openTmdbMatchModal(contentType, itemId, res.title, '');
        } else {
            showToast(`Enrichment skipped: ${res.reason || 'Check settings'}`, true);
        }
    } catch (err) {
        showToast(err.message, true);
    } finally {
        if (btn) {
            btn.innerHTML = originalHtml;
            btn.disabled = false;
        }
    }
}

// ----------------------------------------------------
// TMDB Manual Match Modal Functions
// ----------------------------------------------------

function openTmdbMatchModal(contentType, itemId, initialQuery = '', initialYear = '') {
    const modalEl = document.getElementById('tmdbManualMatchModal');
    if (!modalEl) return;

    // Set form fields
    const mediaTypeInput = document.getElementById('manualMatchMediaType');
    const itemIdInput = document.getElementById('manualMatchItemId');
    const queryInput = document.getElementById('manualMatchQuery');
    const yearInput = document.getElementById('manualMatchYear');

    if (mediaTypeInput) mediaTypeInput.value = contentType;
    if (itemIdInput) itemIdInput.value = itemId;
    if (queryInput) queryInput.value = initialQuery;
    if (yearInput) yearInput.value = initialYear;

    // Reset results and empty state
    const resultsContainer = document.getElementById('manualMatchResults');
    const emptyState = document.getElementById('manualMatchEmptyState');
    const spinner = document.getElementById('manualMatchSpinner');

    if (resultsContainer) {
        resultsContainer.innerHTML = '';
        resultsContainer.classList.add('d-none');
    }
    if (spinner) spinner.classList.add('d-none');
    if (emptyState) emptyState.classList.remove('d-none');

    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();

    // If query provided, run search automatically
    if (initialQuery) {
        triggerManualTmdbSearch();
    }
}

async function triggerManualTmdbSearch() {
    const mediaType = document.getElementById('manualMatchMediaType')?.value || 'vod';
    const query = document.getElementById('manualMatchQuery')?.value.trim();
    const year = document.getElementById('manualMatchYear')?.value.trim();
    const language = document.getElementById('manualMatchLanguage')?.value;
    const searchBtn = document.getElementById('manualMatchSearchBtn');

    if (!query) return;

    const spinner = document.getElementById('manualMatchSpinner');
    const emptyState = document.getElementById('manualMatchEmptyState');
    const resultsContainer = document.getElementById('manualMatchResults');

    if (spinner) spinner.classList.remove('d-none');
    if (emptyState) emptyState.classList.add('d-none');
    if (resultsContainer) {
        resultsContainer.classList.add('d-none');
        resultsContainer.innerHTML = '';
    }
    if (searchBtn) searchBtn.disabled = true;

    try {
        let url = `/api/tmdb/search?query=${encodeURIComponent(query)}&media_type=${encodeURIComponent(mediaType)}`;
        if (year) url += `&year=${encodeURIComponent(year)}`;
        if (language) url += `&language=${encodeURIComponent(language)}`;

        const resp = await fetch(url);
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to search TMDB');
        }
        const data = await resp.json();
        const results = data.results || [];

        if (results.length === 0) {
            if (emptyState) {
                emptyState.innerHTML = `
                    <i class="fa-solid fa-face-frown fs-2 mb-2 text-secondary opacity-50"></i>
                    <p class="fs-7 mb-0">No matches found for "${escapeHtml(query)}". Try another title or language.</p>
                `;
                emptyState.classList.remove('d-none');
            }
            return;
        }

        let html = '';
        const currentItemId = document.getElementById('manualMatchItemId')?.value;

        for (const item of results) {
            const posterHtml = item.poster_url 
                ? `<img src="${item.poster_url}" class="rounded shadow-xs" style="width: 60px; height: 90px; object-fit: cover;" alt="Poster">`
                : `<div class="rounded bg-light border d-flex align-items-center justify-content-center text-muted" style="width: 60px; height: 90px;"><i class="fa-solid fa-film opacity-25"></i></div>`;

            const ratingHtml = item.vote_average 
                ? `<span class="badge bg-warning-subtle text-warning-emphasis border border-warning-subtle fs-9"><i class="fa-solid fa-star me-1 text-warning"></i>${item.vote_average.toFixed(1)}</span>`
                : '';

            const yearHtml = item.year ? `<span class="badge bg-light text-secondary border fs-9">${escapeHtml(item.year)}</span>` : '';
            const overviewSnippet = item.overview ? escapeHtml(item.overview.substring(0, 110)) + (item.overview.length > 110 ? '...' : '') : '<span class="text-muted fst-italic">No plot summary available.</span>';

            html += `
            <div class="col">
                <div class="card h-100 border shadow-xs p-2 d-flex flex-row gap-3 align-items-start">
                    <div class="flex-shrink-0">
                        ${posterHtml}
                    </div>
                    <div class="flex-grow-1 min-w-0 d-flex flex-column justify-content-between h-100">
                        <div>
                            <h6 class="fw-bold fs-7 mb-1 text-dark text-truncate" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</h6>
                            <div class="d-flex align-items-center gap-1 mb-1">
                                ${yearHtml}
                                ${ratingHtml}
                            </div>
                            <p class="fs-9 text-secondary mb-2 line-clamp-2">${overviewSnippet}</p>
                        </div>
                        <button type="button" class="btn btn-sm btn-outline-primary fw-semibold fs-8 align-self-start mt-auto"
                                onclick="applyManualTmdbMatch('${mediaType}', '${currentItemId}', ${item.id}, this)">
                            <i class="fa-solid fa-check me-1"></i> Apply Match
                        </button>
                    </div>
                </div>
            </div>`;
        }

        if (resultsContainer) {
            resultsContainer.innerHTML = html;
            resultsContainer.classList.remove('d-none');
        }
    } catch (err) {
        showToast(err.message, true);
        if (emptyState) {
            emptyState.innerHTML = `<p class="fs-7 text-danger mb-0">${escapeHtml(err.message)}</p>`;
            emptyState.classList.remove('d-none');
        }
    } finally {
        if (spinner) spinner.classList.add('d-none');
        if (searchBtn) searchBtn.disabled = false;
    }
}

async function applyManualTmdbMatch(mediaType, itemId, tmdbId, btn) {
    const originalHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin me-1"></i> Applying...';
    }

    const language = document.getElementById('manualMatchLanguage')?.value || '';

    try {
        let url = `/api/tmdb/apply-match/${encodeURIComponent(mediaType)}/${encodeURIComponent(itemId)}?tmdb_id=${tmdbId}`;
        if (language) url += `&language=${encodeURIComponent(language)}`;

        const resp = await fetch(url, { method: 'POST' });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to apply TMDB match');
        }
        const data = await resp.json();
        showToast(`Matched "${data.title}" successfully!`);

        const modalEl = document.getElementById('tmdbManualMatchModal');
        if (modalEl) {
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        }

        setTimeout(() => window.location.reload(), 800);
    } catch (err) {
        showToast(err.message, true);
        if (btn) {
            btn.innerHTML = originalHtml;
            btn.disabled = false;
        }
    }
}

async function sendTestTelegram() {
    try {
        const resp = await fetch('/api/notifications/test-telegram', { method: 'POST' });
        const res = await resp.json();
        if (res.status === 'ok') {
            showToast('Telegram test notification sent!');
        } else {
            showToast(`Telegram notification error: ${res.error_message}`, true);
        }
    } catch (err) {
        showToast(err.message, true);
    }
}

// ── Footer Task Status Poller ──
async function updateFooterTaskProgress() {
    try {
        const resp = await fetch('/api/system/task-progress');
        if (!resp.ok) return;
        const data = await resp.json();

        const dot = document.getElementById('footerStatusDot');
        const title = document.getElementById('footerTaskTitle');
        const msg = document.getElementById('footerTaskMessage');
        const pWrapper = document.getElementById('footerProgressWrapper');
        const pBar = document.getElementById('footerProgressBar');
        const pPercent = document.getElementById('footerProgressPercent');
        const singleProgress = document.getElementById('footerSingleProgress');
        const multiWorkers = document.getElementById('footerMultiWorkers');

        if (!dot || !title || !msg) return;

        title.textContent = data.task || 'Idle';
        msg.textContent = data.message || 'System ready';

        dot.className = `status-dot status-dot-${data.status || 'idle'}`;

        const isRunning = data.status === 'running';
        const isCompletedWithProgress = data.status === 'completed' && (data.progress || 0) >= 100;

        if (isRunning || isCompletedWithProgress) {
            if (pWrapper) {
                pWrapper.classList.remove('d-none');
                pWrapper.classList.add('d-flex');
            }

            // Check if multiple workers are active in parallel
            if (multiWorkers && data.workers && data.workers.length > 1) {
                if (singleProgress) singleProgress.classList.add('d-none');
                multiWorkers.classList.remove('d-none');
                multiWorkers.classList.add('d-flex');

                let workersHtml = '';
                data.workers.forEach(w => {
                    const pct = w.progress || 0;
                    workersHtml += `
                        <div class="d-flex align-items-center gap-1 border rounded px-2 py-0 bg-light-subtle shadow-xs" title="${escapeHtml(w.label)}: ${escapeHtml(w.message)} (${pct}%)">
                            <span class="fs-9 fw-semibold text-truncate text-body" style="max-width: 80px;">${escapeHtml(w.label)}</span>
                            <div class="progress" style="height: 5px; width: 45px;">
                                <div class="progress-bar progress-bar-striped progress-bar-animated bg-primary" role="progressbar" style="width: ${pct}%"></div>
                            </div>
                            <span class="fs-9 text-primary fw-bold">${pct}%</span>
                        </div>
                    `;
                });
                multiWorkers.innerHTML = workersHtml;
            } else {
                if (multiWorkers) {
                    multiWorkers.classList.add('d-none');
                    multiWorkers.classList.remove('d-flex');
                }
                if (singleProgress) singleProgress.classList.remove('d-none');
                if (pBar) pBar.style.width = `${isRunning ? (data.progress || 0) : 100}%`;
                if (pPercent) pPercent.textContent = `${isRunning ? (data.progress || 0) : 100}%`;
            }
        } else {
            if (pWrapper) {
                pWrapper.classList.remove('d-flex');
                pWrapper.classList.add('d-none');
            }
            if (multiWorkers) {
                multiWorkers.classList.add('d-none');
                multiWorkers.classList.remove('d-flex');
            }
            if (singleProgress) singleProgress.classList.remove('d-none');
        }
    } catch (e) {
        // Silently skip polling failures
    }
}

// ── Stream Health & Auto Re-prioritization ──
async function runStreamHealthCheck(btn) {
    if (!btn) btn = document.getElementById('btnStreamHealth');
    const originalHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin me-1"></i> Testing Streams...';
    }

    try {
        const countInput = document.getElementById('streamHealthCountInput');
        const countVal = countInput ? countInput.value : '';
        const url = countVal ? `/api/sources/health-check?count=${encodeURIComponent(countVal)}` : '/api/sources/health-check';

        const resp = await fetch(url, { method: 'POST' });
        const res = await resp.json();

        if (res.status === 'ok') {
            showToast(`Stream health test completed (${res.duration_sec}s). Re-prioritized sources!`);
            renderStreamHealthModal(res);
        } else {
            showToast(`Health check error: ${res.detail || res.message || 'Failed to complete test'}`, true);
        }
    } catch (err) {
        showToast(`Stream health check failed: ${err.message}`, true);
    } finally {
        if (btn) {
            btn.innerHTML = originalHtml;
            btn.disabled = false;
        }
    }
}

function renderStreamHealthModal(data) {
    const modalEl = document.getElementById('streamHealthResultsModal');
    if (!modalEl) return;

    const contentEl = document.getElementById('streamHealthResultsContent');
    if (!contentEl) return;

    if (!data.sources || data.sources.length === 0) {
        contentEl.innerHTML = '<div class="text-center py-4 text-muted">No sources were tested. Ensure at least one source is enabled.</div>';
    } else {
        let html = `
        <div class="d-flex justify-content-between align-items-center mb-3 p-3 bg-light rounded border">
            <div>
                <span class="fs-7 text-secondary">Streams Tested:</span>
                <strong class="text-dark fs-7">${data.sample_count} per source</strong>
            </div>
            <div>
                <span class="fs-7 text-secondary">Duration:</span>
                <strong class="text-dark fs-7">${data.duration_sec}s</strong>
            </div>
            <div>
                <span class="fs-7 text-secondary">Telegram Report:</span>
                <span class="badge ${data.telegram_sent ? 'bg-success-subtle text-success border border-success-subtle' : 'bg-secondary-subtle text-secondary'}">
                    ${data.telegram_sent ? 'Sent' : 'Not configured'}
                </span>
            </div>
        </div>
        <div class="list-group">`;

        for (const s of data.sources) {
            const isSuccess = s.success_rate >= 80;
            const isDegraded = s.success_rate > 0 && s.success_rate < 80;
            const badgeClass = isSuccess ? 'bg-success' : (isDegraded ? 'bg-warning text-dark' : 'bg-danger');
            
            let prioShift = `<span class="badge bg-light text-secondary border">Priority #${s.new_priority}</span>`;
            if (s.new_priority < s.old_priority) {
                prioShift = `<span class="badge bg-success-subtle text-success border border-success-subtle"><i class="fa-solid fa-arrow-up me-1"></i>#${s.old_priority} ➔ #${s.new_priority} (Promoted)</span>`;
            } else if (s.new_priority > s.old_priority) {
                prioShift = `<span class="badge bg-danger-subtle text-danger border border-danger-subtle"><i class="fa-solid fa-arrow-down me-1"></i>#${s.old_priority} ➔ #${s.new_priority} (Demoted)</span>`;
            }

            html += `
            <div class="list-group-item p-3">
                <div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-2">
                    <h6 class="mb-0 fw-bold text-dark d-flex align-items-center gap-2">
                        <i class="fa-solid fa-server text-primary"></i> ${escapeHtml(s.source_name)}
                    </h6>
                    <div class="d-flex align-items-center gap-2">
                        ${prioShift}
                        <span class="badge ${badgeClass} fs-8">${s.success_rate}% Success</span>
                    </div>
                </div>

                <div class="row g-2 text-secondary fs-8 mb-2">
                    <div class="col-6 col-sm-3">Passed: <strong class="text-success">${s.passed_count}/${s.tested_count}</strong></div>
                    <div class="col-6 col-sm-3">Avg Latency: <strong class="text-dark">${s.avg_latency_ms}ms</strong></div>
                </div>`;

            if (s.details && s.details.length > 0) {
                html += `<div class="table-responsive"><table class="table table-sm table-borderless fs-8 mb-0 align-middle">
                    <thead class="text-secondary border-bottom">
                        <tr>
                            <th style="width: 20%;">Type</th>
                            <th>Stream</th>
                            <th style="width: 25%;">Status</th>
                            <th style="width: 15%;" class="text-end">Latency</th>
                        </tr>
                    </thead>
                    <tbody>`;
                for (const d of s.details) {
                    const ok = d.status === 'ok';
                    html += `
                    <tr>
                        <td><span class="badge bg-light text-dark border text-uppercase">${escapeHtml(d.type)}</span></td>
                        <td class="text-truncate" style="max-width: 220px;" title="${escapeHtml(d.name)}">${escapeHtml(d.name)}</td>
                        <td>
                            ${ok ? '<span class="badge bg-success-subtle text-success border border-success-subtle"><i class="fa-solid fa-check me-1"></i>Passed</span>' : `<span class="badge bg-danger-subtle text-danger border border-danger-subtle" title="${escapeHtml(d.error || '')}"><i class="fa-solid fa-xmark me-1"></i>Failed</span>`}
                        </td>
                        <td class="text-end font-monospace">${d.latency_ms}ms</td>
                    </tr>`;
                }
                html += `</tbody></table></div>`;
            }

            html += `</div>`;
        }

        html += `</div>`;
        contentEl.innerHTML = html;
    }

    const modal = new bootstrap.Modal(modalEl);
    modal.show();
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/[&<>"']/g, m => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;',
    }[m]));
}

// ── Theme Management (Dark Mode) ──
function toggleDarkMode(isDark) {
    const theme = isDark ? 'dark' : 'light';
    document.documentElement.setAttribute('data-bs-theme', theme);
    localStorage.setItem('pream_theme', theme);
    const toggle = document.getElementById('darkModeToggle');
    if (toggle) toggle.checked = isDark;
}

function initThemeToggle() {
    const currentTheme = localStorage.getItem('pream_theme') || 'light';
    const isDark = currentTheme === 'dark';
    document.documentElement.setAttribute('data-bs-theme', currentTheme);
    const toggle = document.getElementById('darkModeToggle');
    if (toggle) toggle.checked = isDark;
}

// ── Rules Pipeline Accordion State ──
function initRulesAccordion() {
    const pipelineCollapse = document.getElementById('collapsePipeline');
    if (!pipelineCollapse) return;

    const savedState = localStorage.getItem('pream_rule_pipeline_open');
    if (savedState === 'false') {
        const bsCollapse = bootstrap.Collapse.getOrCreateInstance(pipelineCollapse, { toggle: false });
        bsCollapse.hide();
    }

    pipelineCollapse.addEventListener('shown.bs.collapse', () => {
        localStorage.setItem('pream_rule_pipeline_open', 'true');
    });
    pipelineCollapse.addEventListener('hidden.bs.collapse', () => {
        localStorage.setItem('pream_rule_pipeline_open', 'false');
    });
}

// ── Settings Backup & Restore (Encrypted JSON) ──
function openExportModal() {
    const modalEl = document.getElementById('exportBackupModal');
    if (modalEl) {
        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        modal.show();
    }
}

function openImportModal() {
    const modalEl = document.getElementById('importBackupModal');
    if (modalEl) {
        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        modal.show();
    }
}

async function runExportBackup() {
    const pwd = document.getElementById('backupExportPassword')?.value || '';
    const btn = document.getElementById('btnRunExport');
    const originalText = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Encrypting...';
    }

    try {
        const resp = await fetch('/api/system/backup/export', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pwd }),
        });
        if (!resp.ok) throw new Error('Backup export failed');

        const blob = await resp.blob();
        const disposition = resp.headers.get('Content-Disposition') || '';
        let filename = 'pream_backup.json';
        const match = disposition.match(/filename="?([^"]+)"?/);
        if (match && match[1]) filename = match[1];

        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);

        const modalEl = document.getElementById('exportBackupModal');
        if (modalEl) {
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        }
        showToast('Backup exported and downloaded successfully!');
    } catch (err) {
        showToast(err.message, true);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
}

async function submitImportBackup(e) {
    e.preventDefault();
    const fileInput = document.getElementById('backupImportFile');
    const pwdInput = document.getElementById('backupImportPassword');
    const modeInput = document.getElementById('backupImportMode');
    const btn = document.getElementById('btnRunImport');

    if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
        showToast('Please select a backup file.', true);
        return;
    }

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    formData.append('password', pwdInput?.value || '');
    formData.append('mode', modeInput?.value || 'merge');

    const originalText = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Restoring...';
    }

    try {
        const resp = await fetch('/api/system/backup/import', {
            method: 'POST',
            body: formData,
        });
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail || 'Failed to restore backup');
        }

        const modalEl = document.getElementById('importBackupModal');
        if (modalEl) {
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        }

        showToast(`Restore completed! Restored ${data.sources_restored} sources and ${data.rules_restored} rules.`);
        setTimeout(() => location.reload(), 1200);
    } catch (err) {
        showToast(err.message, true);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
}

// ── First Login Onboarding Wizard ──
async function dismissOnboarding() {
    try {
        await fetch('/api/system/onboarding/dismiss', { method: 'POST' });
        const modalEl = document.getElementById('onboardingWizardModal');
        if (modalEl) {
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        }
        showToast('Welcome to Pream! Explore the dashboard to get started.');
    } catch (err) {
        console.error('Failed to dismiss onboarding:', err);
    }
}

function startImportFromWizard() {
    const obModal = document.getElementById('onboardingWizardModal');
    if (obModal) {
        const modal = bootstrap.Modal.getInstance(obModal);
        if (modal) modal.hide();
    }
    openImportModal();
}

// ── Stream Logs / Playback History (30 Days) ──
let currentLogsFilter = '';

function openStreamLogsModal() {
    const modalEl = document.getElementById('streamLogsModal');
    if (!modalEl) return;
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();
    loadStreamLogs(currentLogsFilter);
}

function filterLogsByType(streamType) {
    currentLogsFilter = streamType;
    ['btnFilterAll', 'btnFilterMovies', 'btnFilterLive', 'btnFilterSeries'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.remove('active');
    });

    if (!streamType) {
        document.getElementById('btnFilterAll')?.classList.add('active');
    } else if (streamType === 'movie') {
        document.getElementById('btnFilterMovies')?.classList.add('active');
    } else if (streamType === 'live') {
        document.getElementById('btnFilterLive')?.classList.add('active');
    } else if (streamType === 'series') {
        document.getElementById('btnFilterSeries')?.classList.add('active');
    }

    loadStreamLogs(streamType);
}

async function loadStreamLogs(streamType = '') {
    const tableBody = document.getElementById('streamLogsTableBody');
    if (!tableBody) return;
    tableBody.innerHTML = '<tr><td colspan="5" class="text-center py-4 text-muted"><span class="spinner-border spinner-border-sm me-2"></span>Loading playback history...</td></tr>';

    try {
        const url = streamType ? `/api/system/stream-logs?stream_type=${encodeURIComponent(streamType)}&limit=100` : '/api/system/stream-logs?limit=100';
        const resp = await fetch(url);
        if (!resp.ok) throw new Error('Failed to load playback logs');
        const data = await resp.json();

        if (!data.logs || data.logs.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="5" class="text-center py-4 text-muted"><i class="fa-solid fa-clock-rotate-left fs-3 mb-2 d-block opacity-50"></i>No streams accessed in the last 30 days yet.</td></tr>';
            return;
        }

        let html = '';
        for (const l of data.logs) {
            const dateStr = l.created_at ? new Date(l.created_at).toLocaleString() : '—';
            const failoverBadge = l.is_failover ? '<span class="badge bg-warning-subtle text-warning border border-warning-subtle fs-9 ms-1">Failover</span>' : '';
            const typeBadge = l.stream_type === 'movie' ? '<span class="badge bg-success-subtle text-success">VOD Movie</span>' : (l.stream_type === 'live' ? '<span class="badge bg-primary-subtle text-primary">Live TV</span>' : '<span class="badge bg-info-subtle text-info">Series</span>');

            html += `<tr>
                <td>${typeBadge}</td>
                <td><strong class="text-dark">${escapeHtml(l.stream_title)}</strong>${failoverBadge}</td>
                <td><code class="text-secondary bg-light px-2 py-1 rounded fs-8">${escapeHtml(l.client_ip)}</code></td>
                <td><small class="text-muted text-truncate d-inline-block" style="max-width: 200px;" title="${escapeHtml(l.user_agent || '')}">${escapeHtml(l.user_agent || 'Unknown')}</small></td>
                <td class="text-end text-secondary fs-8">${dateStr}</td>
            </tr>`;
        }
        tableBody.innerHTML = html;
    } catch (err) {
        tableBody.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-danger">${escapeHtml(err.message)}</td></tr>`;
    }
}

async function triggerPurgeLogs() {
    if (!confirm('Purge playback logs older than 30 days?')) return;
    try {
        const resp = await fetch('/api/system/stream-logs/cleanup?days=30', { method: 'POST' });
        const data = await resp.json();
        showToast(`Purge completed: removed ${data.deleted} old log records.`);
        loadStreamLogs(currentLogsFilter);
    } catch (err) {
        showToast('Failed to purge stream logs: ' + err.message, true);
    }
}

// Auto-poll and initialization on page load
document.addEventListener('DOMContentLoaded', () => {
    initThemeToggle();
    initRulesAccordion();
    updateFooterTaskProgress();
    setInterval(updateFooterTaskProgress, 1500);
});
