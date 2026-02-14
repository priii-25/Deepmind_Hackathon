/**
 * Eve Chat UI — main application logic.
 * Handles chat, streaming, file uploads, and fashion photographer special UI.
 */

const API_BASE = '';
const sessionId = () => localStorage.getItem('eve_session') || resetSession();

function resetSession() {
  const id = 'sess_' + Math.random().toString(36).slice(2, 14);
  localStorage.setItem('eve_session', id);
  return id;
}

// ── State ────────────────────────────────────────────────────────────
let currentAgent = 'eve_chat';
let isStreaming = false;
let conversations = [];
let pendingFiles = [];
let onboardingActive = false;
let onboardingStage = null;

const ONBOARDING_STAGES = ['brand_discovery', 'suggested_teammates', 'connect_world', 'personalization', 'completed'];

const AUTH_HEADERS = {
  'X-Tenant-ID': 'dev-tenant',
  'X-User-ID': 'dev-user',
};

// ── DOM refs ─────────────────────────────────────────────────────────
const $messages = document.getElementById('messages');
const $input = document.getElementById('chatInput');
const $sendBtn = document.getElementById('sendBtn');
const $uploadBtn = document.getElementById('uploadBtn');
const $fileInput = document.getElementById('fileInput');
const $uploadPreview = document.getElementById('uploadPreview');
const $welcome = document.getElementById('welcome');
const $agentName = document.getElementById('agentName');
const $agentStatus = document.getElementById('agentStatus');
const $headerAvatar = document.getElementById('headerAvatar');
const $agentBadge = document.getElementById('agentBadge');
const $onboardingProgress = document.getElementById('onboardingProgress');

// ── Conversation Management ──────────────────────────────────────────
async function loadConversations() {
  try {
    const resp = await fetch(`${API_BASE}/v1/conversations?limit=50`, { headers: AUTH_HEADERS });
    if (!resp.ok) return;
    conversations = await resp.json();
    renderConversationList();
  } catch (e) {
    console.error('[Chat] Failed to load conversations:', e);
  }
}

function renderConversationList() {
  const $list = document.getElementById('conversationsList');
  if (!$list) return;

  const currentSid = sessionId();

  // Always include the current session even if it's not yet in the list
  const hasCurrentInList = conversations.some(c => c.session_id === currentSid);

  let html = '';
  if (!hasCurrentInList) {
    html += `<div class="conversation-item active" data-sid="${currentSid}">
      <div class="dot"></div>
      <span>New Chat</span>
    </div>`;
  }

  html += conversations.map(c => {
    const isActive = c.session_id === currentSid;
    const title = c.title || 'Untitled';
    const escapedTitle = title.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return `<div class="conversation-item${isActive ? ' active' : ''}" data-sid="${c.session_id}">
      <div class="dot"></div>
      <span>${escapedTitle}</span>
      <button class="conv-delete" onclick="event.stopPropagation(); deleteConversation('${c.session_id}')" title="Delete">×</button>
    </div>`;
  }).join('');

  $list.innerHTML = html;

  // Bind click handlers (not on delete buttons)
  $list.querySelectorAll('.conversation-item').forEach(el => {
    el.addEventListener('click', () => {
      const sid = el.dataset.sid;
      if (sid && sid !== currentSid) switchConversation(sid);
    });
  });
}

async function loadCurrentSession() {
  const sid = sessionId();
  try {
    const resp = await fetch(`${API_BASE}/v1/conversations/${sid}`, { headers: AUTH_HEADERS });
    if (!resp.ok) return; // 404 = new session, no messages yet

    const data = await resp.json();
    if (data.messages && data.messages.length > 0) {
      // Hide welcome screen (use fresh lookup — $welcome may be stale after innerHTML swap)
      const welcomeEl = document.getElementById('welcome');
      if (welcomeEl) welcomeEl.style.display = 'none';

      // Render each message
      data.messages.forEach(m => {
        const opts = {};
        if (m.metadata) {
          opts.agent = m.metadata.agent;
          opts.media_urls = m.metadata.media_urls;
          opts.metadata = m.metadata;
        }
        addMessage(m.role, m.content, opts);
      });

      // Restore active agent
      if (data.active_agent) {
        setActiveAgent(data.active_agent);
      }
    }
  } catch (e) {
    console.log('[Chat] No existing session, starting fresh');
  }
}

async function switchConversation(sid) {
  localStorage.setItem('eve_session', sid);

  // Clear messages and show welcome temporarily
  $messages.innerHTML = `
    <div class="welcome" id="welcome" style="display:block">
      <img class="eve-avatar" src="${getAvatar('eve')}" alt="Eve"/>
      <h2>Loading...</h2>
    </div>`;

  setActiveAgent('eve_chat');
  pendingFiles = [];
  $uploadPreview.innerHTML = '';
  hideOnboardingProgress();

  // Load the session's messages
  await loadCurrentSession();

  // If no messages loaded, restore full welcome screen
  const welcomeEl = document.getElementById('welcome');
  if (welcomeEl && welcomeEl.style.display !== 'none') {
    restoreWelcomeScreen();
  }

  renderConversationList();
  $input.focus();
}

async function deleteConversation(sid) {
  try {
    await fetch(`${API_BASE}/v1/conversations/${sid}`, {
      method: 'DELETE',
      headers: AUTH_HEADERS,
    });
    conversations = conversations.filter(c => c.session_id !== sid);

    // If deleting the current session, start a new one
    if (sid === sessionId()) {
      newChat();
    } else {
      renderConversationList();
    }
  } catch (e) {
    console.error('[Chat] Failed to delete conversation:', e);
  }
}

function restoreWelcomeScreen() {
  const welcomeHTML = `
    <div class="welcome" id="welcome">
      <img class="eve-avatar" src="${getAvatar('eve')}" alt="Eve"/>
      <h2>Hey, I'm Eve</h2>
      <p>Your Chief of Staff. I manage your Teem Mates, coordinate workflows, and make sure everything runs smoothly. What are we working on?</p>
      <div class="capabilities">
        <div class="capability onboard-cta" data-prompt="I'd like to set up my workspace" data-onboard="true">✦ Set Up My Workspace</div>
        <div class="capability" data-prompt="I need some marketing videos for my product">🎬 Meet Kai — UGC Creator</div>
        <div class="capability" data-prompt="I need product photos for my fashion brand">📸 Meet Vera — Fashion Photographer</div>
        <div class="capability" data-prompt="I want to plan and schedule social media content">📱 Meet Chad — Social Media</div>
        <div class="capability" data-prompt="I need to create a presentation for investors">📊 Meet Noa — Presentations</div>
        <div class="capability" data-prompt="I need someone to take notes in my meetings">📝 Meet Ivy — Notetaker</div>
        <div class="capability" data-prompt="Look up brand info for Nike">🏷 Brand Research</div>
      </div>
    </div>`;
  $messages.innerHTML = welcomeHTML;
  bindCapabilityListeners();
}

function bindCapabilityListeners() {
  document.querySelectorAll('.capability').forEach(el => {
    el.addEventListener('click', () => {
      $input.value = el.dataset.prompt;
      sendMessage();
    });
  });
}

// ── Init ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  $headerAvatar.src = getAvatar('eve');
  $input.focus();

  $input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  $input.addEventListener('input', autoResize);
  $sendBtn.addEventListener('click', sendMessage);
  $uploadBtn.addEventListener('click', () => $fileInput.click());
  $fileInput.addEventListener('change', handleFileSelect);

  // Capability quick-starts
  bindCapabilityListeners();

  // Load sidebar conversations and current session messages
  loadConversations();
  loadCurrentSession();
});

function autoResize() {
  $input.style.height = 'auto';
  $input.style.height = Math.min($input.scrollHeight, 120) + 'px';
}

// ── Onboarding Progress ──────────────────────────────────────────────
function showOnboardingProgress(stage) {
  onboardingActive = true;
  onboardingStage = stage;
  if (!$onboardingProgress) return;
  $onboardingProgress.style.display = 'block';
  updateOnboardingProgress(stage);
}

function hideOnboardingProgress() {
  onboardingActive = false;
  onboardingStage = null;
  if ($onboardingProgress) $onboardingProgress.style.display = 'none';
}

function updateOnboardingProgress(stage) {
  if (!$onboardingProgress) return;
  const stageIdx = ONBOARDING_STAGES.indexOf(stage);
  if (stageIdx === -1) return;

  onboardingStage = stage;
  const steps = $onboardingProgress.querySelectorAll('.progress-step');
  const lines = $onboardingProgress.querySelectorAll('.progress-line');

  steps.forEach((step, i) => {
    step.classList.remove('active', 'done');
    if (i < stageIdx) step.classList.add('done');
    else if (i === stageIdx) step.classList.add('active');
  });

  lines.forEach((line, i) => {
    line.classList.remove('done');
    if (i < stageIdx) line.classList.add('done');
  });

  // If completed, hide after a short celebration
  if (stage === 'completed') {
    steps.forEach(s => s.classList.add('done'));
    lines.forEach(l => l.classList.add('done'));
    setTimeout(() => hideOnboardingProgress(), 5000);
  }
}

// ── Send Message ─────────────────────────────────────────────────────
async function sendMessage() {
  const text = $input.value.trim();
  if (!text || isStreaming) return;

  // Hide welcome
  if ($welcome) $welcome.style.display = 'none';

  // Add user message
  addMessage('user', text);
  $input.value = '';
  $input.style.height = 'auto';

  // Show typing
  const typingEl = addTypingIndicator();
  isStreaming = true;
  $sendBtn.disabled = true;

  try {
    // Try SSE streaming first, fallback to regular
    const useStream = true;

    if (useStream) {
      await streamMessage(text, typingEl);
    } else {
      await regularMessage(text, typingEl);
    }

    pendingFiles = [];
    $uploadPreview.innerHTML = '';

    // Refresh sidebar to show new/updated conversation title
    loadConversations();

  } catch (err) {
    typingEl.remove();
    addMessage('assistant', 'Connection error. Make sure Eve is running.', { agent: 'eve_chat' });
  }

  isStreaming = false;
  $sendBtn.disabled = false;
  $input.focus();
}

// ── Regular (non-streaming) request ──────────────────────────────
async function regularMessage(text, typingEl) {
  const resp = await fetch(`${API_BASE}/v1/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Tenant-ID': 'dev-tenant',
      'X-User-ID': 'dev-user',
    },
    body: JSON.stringify({
      message: text,
      session_id: sessionId(),
      files: pendingFiles.map(f => f.id),
    }),
  });

  const data = await resp.json();
  typingEl.remove();

  if (data.agent) setActiveAgent(data.agent);

  addMessage('assistant', data.content, {
    agent: data.agent,
    media_urls: data.media_urls,
    metadata: data.metadata,
    needs_input: data.needs_input,
  });

  if (data.agent === 'fashion_photo' && data.metadata) {
    handleFashionUI(data.metadata, data.needs_input);
  }
}

// ── SSE Streaming request ────────────────────────────────────────
async function streamMessage(text, typingEl) {
  const fileIds = pendingFiles.map(f => f.id).filter(Boolean);
  const resp = await fetch(`${API_BASE}/v1/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Tenant-ID': 'dev-tenant',
      'X-User-ID': 'dev-user',
    },
    body: JSON.stringify({
      message: text,
      session_id: sessionId(),
      files: fileIds.length ? fileIds : undefined,
    }),
  });

  if (!resp.ok) {
    // Fallback to non-streaming
    return regularMessage(text, typingEl);
  }

  typingEl.remove();

  // Create the message elements for streaming
  const msg = document.createElement('div');
  msg.className = 'message assistant';

  const avatar = document.createElement('img');
  avatar.className = 'avatar';
  avatar.src = getAvatar(currentAgent);

  const wrapper = document.createElement('div');
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = '';
  wrapper.appendChild(bubble);
  msg.appendChild(avatar);
  msg.appendChild(wrapper);
  $messages.appendChild(msg);

  let fullContent = '';
  let agentName = '';
  const toolEvents = [];

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop(); // keep incomplete line

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      try {
        const data = JSON.parse(line.slice(6));

        switch (data.type) {
          case 'agent':
            agentName = data.agent;
            setActiveAgent(agentName);
            avatar.src = getAvatar(agentName);
            break;

          case 'token':
            fullContent += data.content;
            bubble.innerHTML = formatContent(fullContent);
            $messages.scrollTop = $messages.scrollHeight;
            break;

          case 'generating':
            if (data.status === 'started') {
              showGeneratingDialog(wrapper, bubble, data.agent);
            } else if (data.status === 'done') {
              hideGeneratingDialog(wrapper);
            }
            break;

          case 'media':
            // Display generated images, videos, or file downloads
            if (data.url && (data.url.endsWith('.pptx') || data.url.includes('pptx'))) {
              appendDownloadFile(bubble, data.url, data.filename || 'presentation.pptx');
            } else {
              appendMedia(bubble, data.url);
            }
            $messages.scrollTop = $messages.scrollHeight;
            break;

          case 'tool_start':
            const ev = document.createElement('div');
            ev.className = 'tool-event active';
            ev.id = `tool-${data.name}`;
            ev.innerHTML = `<div class="spinner"></div> <span>${getToolIcon(data.name)} ${formatToolName(data.name)}</span>`;
            wrapper.insertBefore(ev, bubble);
            toolEvents.push(data.name);

            // Detect onboarding tools and show progress
            if (data.name === 'get_onboarding_state' || data.name === 'advance_onboarding') {
              if (!onboardingActive) showOnboardingProgress(onboardingStage || 'brand_discovery');
            }
            if (data.name === 'brand_lookup' && onboardingActive) {
              updateOnboardingProgress('brand_discovery');
            }
            break;

          case 'tool_result':
            const el = document.getElementById(`tool-${data.name}`);
            if (el) {
              el.classList.remove('active');
              el.querySelector('.spinner')?.remove();
            }

            // Detect onboarding stage advancement from tool results
            if (data.name === 'advance_onboarding' && data.result) {
              const stageMatch = data.result.match(/Advanced to: (\w+)/);
              if (stageMatch && ONBOARDING_STAGES.includes(stageMatch[1])) {
                updateOnboardingProgress(stageMatch[1]);
              }
              if (data.result.includes('Onboarding complete')) {
                updateOnboardingProgress('completed');
              }
            }
            if (data.name === 'get_onboarding_state' && data.result) {
              const currentMatch = data.result.match(/Current stage: (\w+)/);
              if (currentMatch && ONBOARDING_STAGES.includes(currentMatch[1])) {
                showOnboardingProgress(currentMatch[1]);
              }
              if (data.result.includes('No onboarding started')) {
                showOnboardingProgress('brand_discovery');
              }
            }
            break;

          case 'done':
            // Final cleanup
            if (data.metadata?.agent === 'fashion_photo') {
              handleFashionUI(data.metadata, data.metadata?.needs_input);
            }
            // Render PPTX download button for presentation agent
            if (data.metadata?.download_url) {
              appendDownloadFile(
                bubble,
                data.metadata.download_url,
                data.metadata.filename || 'presentation.pptx'
              );
            }
            break;

          case 'error':
            bubble.innerHTML = formatContent(data.content || 'An error occurred.');
            break;
        }
      } catch (e) {
        // Ignore parse errors from partial data
      }
    }
  }

  if (!fullContent) {
    bubble.innerHTML = '<em style="color:var(--text-muted)">No response</em>';
  }
}

// ── Message Rendering ────────────────────────────────────────────────
function addMessage(role, content, opts = {}) {
  const msg = document.createElement('div');
  msg.className = `message ${role}`;

  const avatar = document.createElement('img');
  avatar.className = 'avatar';
  avatar.src = role === 'user' ? getAvatar('user') : getAvatar(opts.agent || 'eve');

  const wrapper = document.createElement('div');

  // Agent label
  if (role === 'assistant' && opts.agent && opts.agent !== 'eve_chat') {
    const label = document.createElement('div');
    label.className = 'agent-label';
    const icon = document.createElement('img');
    icon.className = 'agent-icon';
    icon.src = getAvatar(opts.agent);
    label.appendChild(icon);
    label.appendChild(document.createTextNode(getAgentDisplayName(opts.agent)));
    wrapper.appendChild(label);
  }

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = formatContent(content);
  wrapper.appendChild(bubble);

  // Media (images, videos, file downloads)
  if (opts.media_urls && opts.media_urls.length) {
    opts.media_urls.forEach(url => {
      if (url && (url.endsWith('.pptx') || url.includes('pptx'))) {
        appendDownloadFile(bubble, url, 'presentation.pptx');
      } else {
        appendMedia(bubble, url);
      }
    });
  }
  // Download link from metadata
  if (opts.metadata?.download_url) {
    appendDownloadFile(bubble, opts.metadata.download_url, opts.metadata.filename || 'presentation.pptx');
  }

  // Tool calls metadata
  if (opts.metadata && opts.metadata.tool_calls && Array.isArray(opts.metadata.tool_calls)) {
    opts.metadata.tool_calls.forEach(tc => {
      const ev = document.createElement('div');
      ev.className = 'tool-event';
      ev.innerHTML = `<span style="font-size:14px">${getToolIcon(tc.tool)}</span> <span>${formatToolName(tc.tool)}</span>`;
      wrapper.insertBefore(ev, bubble);
    });
  }

  msg.appendChild(avatar);
  msg.appendChild(wrapper);
  $messages.appendChild(msg);
  $messages.scrollTop = $messages.scrollHeight;
  return msg;
}

function addTypingIndicator() {
  const msg = document.createElement('div');
  msg.className = 'message assistant';
  msg.innerHTML = `
    <img class="avatar" src="${getAvatar(currentAgent)}"/>
    <div>
      <div class="bubble">
        <div class="typing-indicator">
          <div class="dot"></div><div class="dot"></div><div class="dot"></div>
        </div>
      </div>
    </div>`;
  $messages.appendChild(msg);
  $messages.scrollTop = $messages.scrollHeight;
  return msg;
}

// ── Generating Dialog & Media ────────────────────────────────────────
function showGeneratingDialog(wrapper, bubble, agentName) {
  // Remove any existing generating dialog
  wrapper.querySelector('.generating-dialog')?.remove();

  const isVideo = agentName === 'ugc_video';
  const icon = isVideo ? '🎬' : '📸';
  const title = isVideo ? 'Generating your video' : 'Generating your preview';
  const sub = isVideo ? 'This may take 2-5 minutes...' : 'This may take a moment...';

  const dialog = document.createElement('div');
  dialog.className = 'generating-dialog';
  dialog.innerHTML = `
    <div class="generating-content">
      <div class="generating-spinner">
        <svg width="48" height="48" viewBox="0 0 48 48">
          <circle cx="24" cy="24" r="20" fill="none" stroke="url(#grad)" stroke-width="3" stroke-linecap="round"
                  stroke-dasharray="80 40" class="generating-ring"/>
          <defs>
            <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" style="stop-color:#a78bfa"/>
              <stop offset="100%" style="stop-color:#818cf8"/>
            </linearGradient>
          </defs>
        </svg>
        <span class="generating-icon">${icon}</span>
      </div>
      <div class="generating-text">
        <strong>${title}</strong>
        <span class="generating-sub">${sub}</span>
      </div>
      <div class="generating-dots">
        <div class="gdot"></div><div class="gdot"></div><div class="gdot"></div>
      </div>
    </div>
  `;
  wrapper.insertBefore(dialog, bubble);
  $messages.scrollTop = $messages.scrollHeight;
}

function hideGeneratingDialog(wrapper) {
  const dialog = wrapper.querySelector('.generating-dialog');
  if (dialog) {
    dialog.classList.add('fade-out');
    setTimeout(() => dialog.remove(), 300);
  }
}

function appendMedia(bubble, url) {
  const isVideo = url.endsWith('.mp4') || url.endsWith('.webm') || url.endsWith('.mov');
  const container = document.createElement('div');
  container.className = 'generated-image-container';

  if (isVideo) {
    container.innerHTML = `
      <div class="generated-video-wrapper">
        <video controls playsinline class="generated-video">
          <source src="${url}" type="video/mp4"/>
          Your browser does not support video playback.
        </video>
        <div class="generated-image-actions">
          <button class="img-action-btn" onclick="downloadMedia('${url}', 'kai-video.mp4')" title="Download">⬇️ Download</button>
        </div>
      </div>
    `;
  } else {
    container.innerHTML = `
      <div class="generated-image-wrapper">
        <img src="${url}" alt="Generated preview" class="generated-image" onclick="openImagePreview(this.src)"/>
        <div class="generated-image-actions">
          <button class="img-action-btn" onclick="openImagePreview('${url}')" title="View full size">🔍 View</button>
          <button class="img-action-btn" onclick="downloadMedia('${url}', 'vera-preview.png')" title="Download">⬇️ Download</button>
        </div>
      </div>
    `;
  }
  bubble.appendChild(container);
}

// Keep backward compat
function appendMediaImage(bubble, url) { appendMedia(bubble, url); }

function openImagePreview(src) {
  const overlay = document.createElement('div');
  overlay.className = 'image-preview-overlay';
  overlay.onclick = () => overlay.remove();
  overlay.innerHTML = `
    <div class="image-preview-content" onclick="event.stopPropagation()">
      <button class="preview-close" onclick="this.closest('.image-preview-overlay').remove()">✕</button>
      <img src="${src}" alt="Preview"/>
    </div>
  `;
  document.body.appendChild(overlay);
}

function downloadMedia(url, filename) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || 'download';
  a.click();
}
// Keep backward compat
function downloadImage(url) { downloadMedia(url, 'vera-preview.png'); }

function appendDownloadFile(bubble, url, filename) {
  // Create a styled download card for non-image files (PPTX, etc.)
  const container = document.createElement('div');
  container.className = 'file-download-container';
  container.innerHTML = `
    <div class="file-download-card">
      <div class="file-download-icon">
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="12" y1="18" x2="12" y2="12"/>
          <polyline points="9 15 12 18 15 15"/>
        </svg>
      </div>
      <div class="file-download-info">
        <div class="file-download-name">${filename}</div>
        <div class="file-download-type">PowerPoint Presentation</div>
      </div>
      <a href="${url}" download="${filename}" class="file-download-btn" title="Download">
        Download
      </a>
    </div>
  `;
  bubble.appendChild(container);
}

// ── Fashion Photo Special UI ─────────────────────────────────────────
function handleFashionUI(metadata, needsInput) {
  const step = metadata.current_step;
  if (!step) return;

  if (step === 'avatar_category' || step === 'avatar_select') {
    showAvatarPicker();
  } else if (step === 'avatar_upload') {
    showFileUpload('avatar');
  } else if (step === 'product_upload') {
    showFileUpload('product');
  } else if (step === 'scene_category' || step === 'scene_select') {
    showScenePicker(step);
  }
}

function showAvatarPicker() {
  const avatars = [
    { name: 'Sofia', style: 'Female, editorial', img: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=200&h=280&fit=crop' },
    { name: 'Marcus', style: 'Male, streetwear', img: 'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=200&h=280&fit=crop' },
    { name: 'Aisha', style: 'Female, modest', img: 'https://images.unsplash.com/photo-1531746020798-e6953c6e8e04?w=200&h=280&fit=crop' },
    { name: 'Kai', style: 'Male, sport', img: 'https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?w=200&h=280&fit=crop' },
    { name: 'Luna', style: 'Female, luxury', img: 'https://images.unsplash.com/photo-1524504388940-b1c1722653e1?w=200&h=280&fit=crop' },
    { name: 'Dev', style: 'Male, editorial', img: 'https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=200&h=280&fit=crop' },
  ];

  const container = document.createElement('div');
  container.className = 'message assistant';
  container.innerHTML = `
    <img class="avatar" src="${getAvatar('fashion_photo')}"/>
    <div>
      <div class="agent-label"><img class="agent-icon" src="${getAvatar('fashion_photo')}"/> Vera</div>
      <div class="fashion-grid">${avatars.map(a => `
        <div class="avatar-card" onclick="selectAvatar('${a.name}', this)">
          <div class="check">✓</div>
          <img src="${a.img}" alt="${a.name}" loading="lazy"/>
          <div class="label">${a.name}<br/><span style="color:var(--text-secondary);font-size:10px">${a.style}</span></div>
        </div>`).join('')}
      </div>
    </div>`;
  $messages.appendChild(container);
  $messages.scrollTop = $messages.scrollHeight;
}

function selectAvatar(name, el) {
  document.querySelectorAll('.avatar-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  $input.value = `I'll go with ${name}`;
  setTimeout(() => sendMessage(), 300);
}

function showFileUpload(type) {
  // type = 'product' or 'avatar'
  const isAvatar = type === 'avatar';
  const label = isAvatar ? 'Upload your model photo' : 'Upload your product';
  const hint = isAvatar ? 'A clear photo of your model' : 'Front + back recommended';
  const icon = isAvatar ? '🧍' : '📸';
  const autoMsg = isAvatar ? "Here's my model" : "Here's my product image";
  console.log(`[Vera] showFileUpload(${type}) called`);

  const container = document.createElement('div');
  container.className = 'message assistant';
  container.innerHTML = `
    <img class="avatar" src="${getAvatar('fashion_photo')}"/>
    <div>
      <div class="drop-zone">
        <div class="dz-content">
          <div class="icon">${icon}</div>
          <div class="text"><strong>${label}</strong><br/>Drag & drop or click to browse<br/>${hint}</div>
        </div>
        <div class="dz-preview" style="display:none"></div>
        <div class="dz-progress" style="display:none">
          <div class="spinner"></div> <span>Uploading...</span>
        </div>
        <input type="file" accept="image/*" style="display:none" class="product-file-input"/>
      </div>
    </div>`;
  $messages.appendChild(container);
  $messages.scrollTop = $messages.scrollHeight;

  const dz = container.querySelector('.drop-zone');
  const fileInput = container.querySelector('.product-file-input');
  if (!dz || !fileInput) return;

  dz.querySelector('.dz-content').addEventListener('click', (e) => {
    e.stopPropagation();
    fileInput.click();
  });

  fileInput.addEventListener('change', e => {
    if (e.target.files.length) uploadFileToServer(e.target.files[0], dz, autoMsg);
  });

  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('dragging'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('dragging'));
  dz.addEventListener('drop', e => {
    e.preventDefault();
    dz.classList.remove('dragging');
    const file = e.dataTransfer.files[0];
    if (file && file.type.startsWith('image/')) uploadFileToServer(file, dz, autoMsg);
  });
}

async function uploadFileToServer(file, dropZone, autoMsg) {
  console.log('[Vera] uploadFileToServer:', file.name, file.size, 'bytes');

  const dzContent = dropZone.querySelector('.dz-content');
  const dzProgress = dropZone.querySelector('.dz-progress');
  const dzPreview = dropZone.querySelector('.dz-preview');

  dzContent.style.display = 'none';
  dzProgress.style.display = 'flex';

  try {
    const base64Data = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });

    const resp = await fetch(`${API_BASE}/v1/upload`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Tenant-ID': 'dev-tenant',
        'X-User-ID': 'dev-user',
      },
      body: JSON.stringify({
        data: base64Data,
        filename: file.name,
      }),
    });

    if (!resp.ok) {
      const errText = await resp.text();
      throw new Error(`Upload failed: ${resp.status} ${errText}`);
    }

    const result = await resp.json();
    console.log('[Vera] Upload success:', result);

    dzProgress.style.display = 'none';
    dzPreview.style.display = 'flex';
    dzPreview.innerHTML = `
      <img src="${base64Data}" alt="${file.name}" class="product-preview-img"/>
      <div class="product-preview-info">
        <span class="product-preview-name">${file.name}</span>
        <span class="product-preview-check">✓ Uploaded</span>
      </div>
    `;

    pendingFiles.push({ id: result.file_id, data: base64Data, name: file.name, url: result.url });

    const waitForReady = () => new Promise(resolve => {
      const check = () => {
        if (!isStreaming) return resolve();
        setTimeout(check, 200);
      };
      check();
    });
    await waitForReady();

    $input.value = autoMsg;
    console.log('[Vera] Sending message with file_id:', result.file_id);
    sendMessage();

  } catch (err) {
    console.error('[Vera] Upload error:', err);
    dzProgress.style.display = 'none';
    dzContent.style.display = 'flex';
    dzContent.innerHTML = `
      <div class="icon" style="color:var(--error)">⚠️</div>
      <div class="text"><strong>Upload failed</strong><br/>Click to try again</div>
    `;
  }
}

function showScenePicker(step) {
  const scenes = step === 'scene_category' ? [
    { icon: '🎨', name: 'Studio', desc: 'Clean e-commerce look' },
    { icon: '🏛', name: 'Premium Indoor', desc: 'Luxury hotel/marble' },
    { icon: '🌆', name: 'Street', desc: 'Urban edge' },
    { icon: '🌿', name: 'Outdoor', desc: 'Natural vibes' },
    { icon: '✨', name: 'Editorial', desc: 'High fashion' },
    { icon: '🎭', name: 'Cultural', desc: 'Local flavor' },
  ] : [
    { icon: '⬜', name: 'White infinity wall', desc: 'Classic clean look' },
    { icon: '🔲', name: 'Gray backdrop', desc: 'Sophisticated neutral' },
    { icon: '🟣', name: 'Bold color pop', desc: 'Vibrant and modern' },
    { icon: '🪵', name: 'Natural textures', desc: 'Wood & stone' },
    { icon: '🌙', name: 'Neon night', desc: 'Dramatic urban glow' },
  ];

  const container = document.createElement('div');
  container.className = 'message assistant';
  container.innerHTML = `
    <img class="avatar" src="${getAvatar('fashion_photo')}"/>
    <div>
      <div class="scene-grid">${scenes.map(s => `
        <div class="scene-card" onclick="selectScene('${s.name}', this)">
          <div class="icon">${s.icon}</div>
          <div class="name">${s.name}</div>
          <div class="desc">${s.desc}</div>
        </div>`).join('')}
      </div>
    </div>`;
  $messages.appendChild(container);
  $messages.scrollTop = $messages.scrollHeight;
}

function selectScene(name, el) {
  document.querySelectorAll('.scene-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  $input.value = name;
  setTimeout(() => sendMessage(), 300);
}

// ── File Upload (attachment icon) ────────────────────────────────────
function handleFileSelect(e) {
  const files = e.target.files;
  if (!files || !files.length) return;
  // Reset input so the same file can be re-selected
  handleFiles(files).finally(() => { $fileInput.value = ''; });
}

async function handleFiles(files) {
  const ALLOWED_TYPES = ['image/', 'video/'];
  const MAX_BASE64_SIZE = 20 * 1024 * 1024;  // 20MB — use base64 for small files
  // Videos and large files use multipart upload

  for (const file of Array.from(files)) {
    const isAllowed = ALLOWED_TYPES.some(t => file.type.startsWith(t));
    if (!isAllowed) {
      showUploadStatus('Please select an image or video file', true);
      continue;
    }

    const isVideo = file.type.startsWith('video/');
    const sizeMB = (file.size / (1024 * 1024)).toFixed(1);

    // Show immediate feedback
    showUploadStatus(isVideo ? `Uploading video (${sizeMB} MB)...` : 'Reading file...');

    try {
      let result;

      if (isVideo || file.size > MAX_BASE64_SIZE) {
        // ── Multipart upload for videos and large files ────────
        showUploadStatus(`Uploading ${isVideo ? 'video' : 'file'} (${sizeMB} MB)...`);

        const formData = new FormData();
        formData.append('file', file);

        const resp = await fetch(`${API_BASE}/v1/upload/file`, {
          method: 'POST',
          headers: {
            'X-Tenant-ID': 'dev-tenant',
            'X-User-ID': 'dev-user',
          },
          body: formData,
        });

        if (!resp.ok) {
          const errText = await resp.text().catch(() => resp.status);
          throw new Error(`Upload failed (${resp.status}): ${errText}`);
        }

        result = await resp.json();

      } else {
        // ── Base64 upload for small images ─────────────────────
        const base64Data = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result);
          reader.onerror = () => reject(new Error('Could not read file'));
          reader.readAsDataURL(file);
        });

        showUploadStatus('Uploading...');

        const resp = await fetch(`${API_BASE}/v1/upload`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Tenant-ID': 'dev-tenant',
            'X-User-ID': 'dev-user',
          },
          body: JSON.stringify({ data: base64Data, filename: file.name }),
        });

        if (!resp.ok) {
          const errText = await resp.text().catch(() => resp.status);
          throw new Error(`Upload failed (${resp.status}): ${errText}`);
        }

        result = await resp.json();
      }

      console.log('[Upload] Attachment uploaded:', result.file_id, result.filename);

      // Use a data URL preview for images, or a placeholder for videos
      const previewData = isVideo ? null : await getBase64Preview(file);
      pendingFiles.push({
        id: result.file_id,
        data: previewData,
        name: file.name,
        url: result.url,
        isVideo: isVideo,
      });
      renderUploadPreviews();
      hideUploadStatus();

      // Auto-send — set the message and send
      if (!$input.value.trim()) {
        $input.value = isVideo ? "Here's my video" : "Here's my uploaded image";
      }

      // If currently streaming, wait for it to finish before sending
      if (isStreaming) {
        console.log('[Upload] Waiting for current stream to finish before sending...');
        await new Promise(resolve => {
          const check = setInterval(() => {
            if (!isStreaming) { clearInterval(check); resolve(); }
          }, 200);
          // Timeout after 15s
          setTimeout(() => { clearInterval(check); resolve(); }, 15000);
        });
      }
      sendMessage();

    } catch (err) {
      console.error('[Upload] Attachment upload failed:', err);
      showUploadStatus('Upload failed: ' + err.message, true);
    }
  }

  // Helper: get base64 preview for images (not used for videos)
  function getBase64Preview(file) {
    return new Promise((resolve) => {
      if (!file.type.startsWith('image/')) { resolve(null); return; }
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => resolve(null);
      reader.readAsDataURL(file);
    });
  }
}

function showUploadStatus(msg, isError) {
  let el = document.getElementById('uploadStatus');
  if (!el) {
    el = document.createElement('div');
    el.id = 'uploadStatus';
    el.style.cssText = 'padding:6px 14px;margin:4px 0;border-radius:8px;font-size:13px;text-align:center;';
    $uploadPreview.parentElement.insertBefore(el, $uploadPreview);
  }
  el.style.background = isError ? 'rgba(239,68,68,0.15)' : 'rgba(124,92,252,0.15)';
  el.style.color = isError ? '#f87171' : '#a78bfa';
  el.textContent = msg;
  el.style.display = 'block';
  if (isError) setTimeout(() => hideUploadStatus(), 4000);
}

function hideUploadStatus() {
  const el = document.getElementById('uploadStatus');
  if (el) el.style.display = 'none';
}

function renderUploadPreviews() {
  $uploadPreview.innerHTML = pendingFiles.map((f, i) => {
    if (f.isVideo) {
      return `<div class="preview-item" style="display:flex;align-items:center;gap:6px;padding:4px 10px;background:rgba(255,255,255,0.08);border-radius:8px;">
        <span style="font-size:20px;">🎬</span>
        <span style="font-size:12px;opacity:0.8;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${f.name}</span>
        <button class="remove" onclick="removeFile(${i})">×</button>
      </div>`;
    }
    return `<div class="preview-item">
      <img src="${f.data}" alt="${f.name}"/>
      <button class="remove" onclick="removeFile(${i})">×</button>
    </div>`;
  }).join('');
}

function removeFile(i) {
  pendingFiles.splice(i, 1);
  renderUploadPreviews();
}

// ── Agent Management ─────────────────────────────────────────────────
function setActiveAgent(name) {
  currentAgent = name;
  $headerAvatar.src = getAvatar(name);
  $agentName.textContent = getAgentDisplayName(name);
  $agentStatus.textContent = name === 'eve_chat' ? 'Chief of Staff' : 'Teem Mate';
  $agentBadge.querySelector('span:last-child').textContent = getAgentDisplayName(name);
}

function getAgentDisplayName(name) {
  const names = {
    eve_chat: 'Eve — Chief of Staff',
    fashion_photo: 'Vera — Fashion Photographer',
    ugc_video: 'Kai — UGC Creator',
    social_media: 'Chad — Social Media Manager',
    presentation: 'Noa — Presentation Maker',
    notetaker: 'Ivy — Notetaker',
  };
  return names[name] || name;
}

function getToolIcon(name) {
  const icons = {
    brand_lookup: '🏷',
    web_search: '🔍',
    doc_search: '📄',
    db_query: '🗃',
    meeting_search: '📅',
    get_onboarding_state: '🚀',
    advance_onboarding: '✅',
    agent_fashion_photo: '📸',
    agent_ugc_video: '🎬',
    agent_social_media: '📱',
    agent_presentation: '📊',
    agent_notetaker: '📝',
    photo_gallery: '🖼',
    conversation_history: '💬',
  };
  return icons[name] || '⚡';
}

function formatToolName(name) {
  const names = {
    brand_lookup: 'Looking up brand info',
    web_search: 'Searching the web',
    doc_search: 'Searching documents',
    db_query: 'Querying database',
    meeting_search: 'Searching meetings',
    get_onboarding_state: 'Checking onboarding status',
    advance_onboarding: 'Saving progress',
    agent_fashion_photo: 'Connecting with Vera',
    agent_ugc_video: 'Connecting with Kai',
    agent_social_media: 'Connecting with Chad',
    agent_presentation: 'Connecting with Noa',
    agent_notetaker: 'Connecting with Ivy',
    photo_gallery: 'Looking up your photos',
    conversation_history: 'Checking past conversations',
  };
  return names[name] || name;
}

// ── Content Formatting ───────────────────────────────────────────────
function formatContent(text) {
  if (!text) return '';

  // Basic markdown: bold, italic, newlines, lists
  let html = text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`(.+?)`/g, '<code style="background:var(--bg-active);padding:1px 5px;border-radius:4px;font-size:12px">$1</code>')
    .replace(/^[-•] (.+)$/gm, '<li style="margin-left:16px;list-style:disc">$1</li>')
    .replace(/\n/g, '<br/>');

  return html;
}

// Agent name to avatar mapping for upsell cards
const TEEM_MATE_AGENTS = {
  'kai': 'ugc_video',
  'vera': 'fashion_photo',
  'chad': 'social_media',
  'noa': 'presentation',
  'ivy': 'notetaker',
};

// ── New Chat ─────────────────────────────────────────────────────────
function newChat() {
  resetSession();
  hideOnboardingProgress();
  restoreWelcomeScreen();
  setActiveAgent('eve_chat');
  pendingFiles = [];
  $uploadPreview.innerHTML = '';
  renderConversationList();
  $input.focus();
}
