'use strict';
const $ = id => document.getElementById(id);
let pc = null, ownedSession = '', productId = '', products = [], platforms = [];
let busy = false, connectionVersion = 0;
const phases = {stopped: '已停止', generating: '正在准备讲解', synthesizing: '正在合成语音', speaking: '正在播报', pause: '短暂停顿', error: '需要处理'};

function notice(text, error = false) {
    $('notice').textContent = text;
    $('notice').classList.toggle('error', error);
}
async function api(path, data) {
    const options = data === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)};
    const response = await fetch(path, options);
    const result = await response.json();
    if (!response.ok || result.code !== 0) throw new Error(result.msg || '请求失败');
    return result.data;
}
function action(id, fn) {
    $(id).addEventListener('click', async () => {
        $(id).disabled = true;
        try { await fn(); } catch (error) { notice(error.message, true); }
        finally { $(id).disabled = false; }
    });
}
function addOption(select, value, label) {
    const option = document.createElement('option');
    option.value = value; option.textContent = label; select.appendChild(option);
}
async function refreshSessions(preferred) {
    const previous = preferred || $('sessions').value;
    const data = await api('/api/admin/sessions');
    $('sessions').replaceChildren();
    addOption($('sessions'), '', '选择已连接的会话');
    for (const session of data.sessions) addOption($('sessions'), session.sessionid, `${session.sessionid.slice(0, 8)} · ${session.transport} · ${session.avatar_id}`);
    if ([...$('sessions').options].some(o => o.value === previous)) $('sessions').value = previous;
}
async function refreshProducts(selected = productId) {
    products = await api('/api/live/products');
    $('products').replaceChildren();
    addOption($('products'), '', '新增产品');
    for (const product of products) addOption($('products'), product.id, product.name);
    $('products').value = selected;
}
function editProduct(id) {
    productId = id;
    const product = products.find(p => p.id === id);
    $('name').value = product?.name || '';
    $('category').value = product?.category || '';
    $('facts').value = product?.facts || '';
    $('scripts').value = product?.scripts.join('\n') || '';
    $('products').value = id;
    $('productState').textContent = product ? '已加载；编辑后请保存' : '新增产品，保存后可开始讲解';
}
function bridgeState() {
    const platform = platforms.find(p => p.id === $('platform').value);
    $('bridgeState').textContent = `${platform?.name || ''}真实弹幕待接入：${platform?.ingest_configured ? '统一入口已配置密钥，需要平台适配器转发弹幕。' : '可先使用模拟弹幕测试。'}`;
}
async function disconnect() {
    connectionVersion++;
    try { if (ownedSession) await api('/api/live/stop', {sessionid: ownedSession}); }
    finally {
        if (pc) pc.close();
        pc = null; ownedSession = '';
        $('video').srcObject = null; $('audio').srcObject = null;
        $('connection').textContent = '未连接';
    }
}
async function connect() {
    await disconnect();
    const version = connectionVersion;
    const config = $('stun').checked ? {iceServers: [{urls: 'stun:stun.freeswitch.org:3478'}]} : {iceServers: []};
    const peer = new RTCPeerConnection(config);
    pc = peer;
    $('connection').textContent = '正在连接…';
    peer.addTransceiver('video', {direction: 'recvonly'});
    peer.addTransceiver('audio', {direction: 'recvonly'});
    peer.ontrack = event => {
        const media = event.track.kind === 'video' ? $('video') : $('audio');
        media.srcObject = event.streams[0] || new MediaStream([event.track]);
        media.play().catch(() => notice('连接已建立，请点击音频播放器的播放按钮开启声音。'));
    };
    peer.onconnectionstatechange = () => {
        if (pc !== peer) return;
        const state = peer.connectionState;
        $('connection').textContent = {connected: '已连接', connecting: '正在连接…', disconnected: '连接中断', failed: '连接失败', closed: '已断开'}[state] || state;
    };
    try {
        await peer.setLocalDescription(await peer.createOffer());
        if (peer.iceGatheringState !== 'complete') await new Promise((resolve, reject) => {
            const timeout = setTimeout(() => { peer.removeEventListener('icegatheringstatechange', changed); reject(new Error('连接协商超时，请检查网络或关闭 STUN 重试')); }, 20000);
            function changed() {
                if (peer.iceGatheringState === 'complete') { clearTimeout(timeout); peer.removeEventListener('icegatheringstatechange', changed); resolve(); }
            }
            peer.addEventListener('icegatheringstatechange', changed);
        });
        if (version !== connectionVersion) return;
        const response = await fetch('/offer', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({sdp: peer.localDescription.sdp, type: peer.localDescription.type})});
        const answer = await response.json();
        if (!response.ok || answer.code === -1 || !answer.sessionid) throw new Error(answer.msg || '数字人连接失败');
        await peer.setRemoteDescription({sdp: answer.sdp, type: answer.type});
        ownedSession = answer.sessionid;
        await refreshSessions(ownedSession);
        notice('数字人已连接。保存并选择产品后，即可开始讲解。');
    } catch (error) { await disconnect(); throw error; }
}
function session() {
    const id = $('sessions').value;
    if (!id) throw new Error('请先连接数字人或选择已有会话');
    return id;
}
action('connect', connect);
action('disconnect', async () => { await disconnect(); await refreshSessions(); });
action('refreshSessions', () => refreshSessions());
action('newProduct', () => editProduct(''));
$('products').addEventListener('change', () => editProduct($('products').value));
$('platform').addEventListener('change', bridgeState);
action('saveProduct', async () => {
    const scripts = $('scripts').value.split('\n').map(s => s.trim()).filter(Boolean);
    const saved = await api('/api/live/products', {id: productId, name: $('name').value, category: $('category').value, facts: $('facts').value, scripts});
    productId = saved.id; await refreshProducts();
    $('productState').textContent = '已保存'; notice('产品已保存。正在讲解时，点击“应用新设置”才会切换资料。');
});
action('start', async () => {
    if (!productId) throw new Error('请先保存并选择产品');
    await api('/api/live/start', {sessionid: session(), product_id: productId, platform: $('platform').value, room_id: $('room').value, mode: $('mode').value, style: $('style').value});
    notice('自动讲解已启动。可发送模拟弹幕，测试回答后继续讲解。');
});
action('stop', async () => { await api('/api/live/stop', {sessionid: session()}); notice('自动讲解已停止。'); });
action('sendComment', async () => {
    const data = await api('/api/live/comment', {sessionid: session(), username: $('username').value, text: $('comment').value});
    const labels = {accepted: '模拟弹幕已加入队列', duplicate: '相同弹幕已合并', queue_full: '弹幕队列已满，请稍后再试', stopped: '请先开始讲解'};
    notice(labels[data.result] || data.result);
    if (data.result === 'accepted') $('comment').value = '';
});
$('comment').addEventListener('keydown', event => { if (event.key === 'Enter' && !$('sendComment').disabled) $('sendComment').click(); });
async function poll() {
    if (busy) return;
    busy = true;
    const id = $('sessions').value;
    try {
        const status = id ? await api('/api/live/status?sessionid=' + encodeURIComponent(id)) : {phase: 'stopped'};
        if ($('sessions').value !== id) return;
        $('phase').textContent = phases[status.phase] || status.phase;
        $('pending').textContent = `待回答 ${status.pending_comments || 0}`;
        $('current').textContent = status.current_text || '等待开始……';
        $('progress').textContent = status.product ? `${status.product} · 第 ${status.cycle} 轮 · 第 ${status.segment} 段` : '';
        $('source').textContent = status.last_source === 'bridge' ? '最近收到：外部弹幕' : status.last_source === 'simulation' ? '最近收到：模拟弹幕' : '尚未接收弹幕';
        $('history').replaceChildren();
        for (const turn of (status.recent || []).slice().reverse()) {
            const p = document.createElement('p'), tag = document.createElement('b');
            tag.textContent = turn.kind === 'reply' ? '互动' : '讲解';
            p.append(tag, document.createTextNode(turn.text)); $('history').append(p);
        }
        if (status.error) notice(status.error, true);
    } catch (error) { notice('状态获取失败：' + error.message, true); }
    finally { busy = false; }
}
async function init() {
    try {
        platforms = await api('/api/live/platforms');
        for (const platform of platforms) addOption($('platform'), platform.id, platform.name);
        $('platform').value = 'douyin'; bridgeState();
        await refreshProducts(); await refreshSessions();
    } catch (error) { notice(error.message, true); }
    setInterval(poll, 1000);
}
window.addEventListener('pagehide', () => {
    if (ownedSession) navigator.sendBeacon('/api/live/stop', new Blob([JSON.stringify({sessionid: ownedSession})], {type: 'application/json'}));
    if (pc) pc.close();
});
init();
