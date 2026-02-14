/**
 * Animated SVG avatars for Eve and all agents.
 * Each returns an SVG data URI for use as img src.
 */

const Avatars = {
  eve: () => `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80">
    <defs>
      <linearGradient id="eg" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:#7c5cfc"/>
        <stop offset="100%" style="stop-color:#f472b6"/>
      </linearGradient>
    </defs>
    <circle cx="40" cy="40" r="38" fill="url(#eg)"/>
    <circle cx="40" cy="40" r="35" fill="#1c1c26" opacity="0.15"/>
    <!-- Face -->
    <circle cx="40" cy="36" r="16" fill="#fff" opacity="0.95"/>
    <!-- Eyes -->
    <circle cx="34" cy="34" r="2.5" fill="#1c1c26">
      <animate attributeName="r" values="2.5;2.5;0.5;2.5" dur="4s" repeatCount="indefinite"/>
    </circle>
    <circle cx="46" cy="34" r="2.5" fill="#1c1c26">
      <animate attributeName="r" values="2.5;2.5;0.5;2.5" dur="4s" repeatCount="indefinite"/>
    </circle>
    <!-- Smile -->
    <path d="M34 40 Q40 45 46 40" fill="none" stroke="#1c1c26" stroke-width="1.8" stroke-linecap="round"/>
    <!-- Sparkle -->
    <circle cx="58" cy="18" r="3" fill="#fbbf24" opacity="0.9">
      <animate attributeName="opacity" values="0.9;0.3;0.9" dur="2s" repeatCount="indefinite"/>
      <animate attributeName="r" values="3;2;3" dur="2s" repeatCount="indefinite"/>
    </circle>
    <text x="40" y="68" text-anchor="middle" font-size="8" fill="white" font-family="sans-serif" font-weight="600">EVE</text>
  </svg>`)}`,

  fashion_photo: () => `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80">
    <defs>
      <linearGradient id="fg" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:#f472b6"/>
        <stop offset="100%" style="stop-color:#a78bfa"/>
      </linearGradient>
    </defs>
    <circle cx="40" cy="40" r="38" fill="url(#fg)"/>
    <!-- Camera body -->
    <rect x="22" y="28" width="36" height="24" rx="4" fill="white" opacity="0.95"/>
    <!-- Lens -->
    <circle cx="40" cy="40" r="8" fill="none" stroke="#1c1c26" stroke-width="2.5"/>
    <circle cx="40" cy="40" r="4" fill="#1c1c26">
      <animate attributeName="r" values="4;5;4" dur="3s" repeatCount="indefinite"/>
    </circle>
    <!-- Flash -->
    <rect x="30" y="26" width="8" height="4" rx="1" fill="#1c1c26" opacity="0.7"/>
    <!-- Flash animation -->
    <circle cx="34" cy="28" r="12" fill="white" opacity="0">
      <animate attributeName="opacity" values="0;0;0.6;0" dur="5s" repeatCount="indefinite"/>
    </circle>
    <text x="40" y="68" text-anchor="middle" font-size="7" fill="white" font-family="sans-serif" font-weight="600">VERA</text>
  </svg>`)}`,

  ugc_video: () => `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80">
    <defs>
      <linearGradient id="ug" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:#f87171"/>
        <stop offset="100%" style="stop-color:#fbbf24"/>
      </linearGradient>
    </defs>
    <circle cx="40" cy="40" r="38" fill="url(#ug)"/>
    <!-- Play button -->
    <polygon points="33,26 33,54 56,40" fill="white" opacity="0.95">
      <animate attributeName="opacity" values="0.95;0.7;0.95" dur="2s" repeatCount="indefinite"/>
    </polygon>
    <!-- Record dot -->
    <circle cx="58" cy="22" r="4" fill="#f87171">
      <animate attributeName="opacity" values="1;0.3;1" dur="1.5s" repeatCount="indefinite"/>
    </circle>
    <text x="40" y="68" text-anchor="middle" font-size="7" fill="white" font-family="sans-serif" font-weight="600">UGC</text>
  </svg>`)}`,

  social_media: () => `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80">
    <defs>
      <linearGradient id="sg" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:#60a5fa"/>
        <stop offset="100%" style="stop-color:#34d399"/>
      </linearGradient>
    </defs>
    <circle cx="40" cy="40" r="38" fill="url(#sg)"/>
    <!-- Share icon -->
    <circle cx="30" cy="40" r="5" fill="white" opacity="0.95"/>
    <circle cx="52" cy="28" r="5" fill="white" opacity="0.95"/>
    <circle cx="52" cy="52" r="5" fill="white" opacity="0.95"/>
    <line x1="34" y1="38" x2="48" y2="30" stroke="white" stroke-width="2" opacity="0.8"/>
    <line x1="34" y1="42" x2="48" y2="50" stroke="white" stroke-width="2" opacity="0.8"/>
    <!-- Notification -->
    <circle cx="58" cy="20" r="6" fill="#f87171">
      <animate attributeName="r" values="6;7;6" dur="2s" repeatCount="indefinite"/>
    </circle>
    <text x="58" y="23" text-anchor="middle" font-size="8" fill="white" font-family="sans-serif" font-weight="700">1</text>
    <text x="40" y="68" text-anchor="middle" font-size="6" fill="white" font-family="sans-serif" font-weight="600">SOCIAL</text>
  </svg>`)}`,

  presentation: () => `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80">
    <defs>
      <linearGradient id="pg" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:#fbbf24"/>
        <stop offset="100%" style="stop-color:#f97316"/>
      </linearGradient>
    </defs>
    <circle cx="40" cy="40" r="38" fill="url(#pg)"/>
    <!-- Slide -->
    <rect x="20" y="24" width="40" height="28" rx="3" fill="white" opacity="0.95"/>
    <!-- Chart bars -->
    <rect x="25" y="40" width="6" height="8" rx="1" fill="#fbbf24"/>
    <rect x="33" y="36" width="6" height="12" rx="1" fill="#f97316"/>
    <rect x="41" y="32" width="6" height="16" rx="1" fill="#fbbf24"/>
    <rect x="49" y="28" width="6" height="20" rx="1" fill="#f97316">
      <animate attributeName="height" values="20;16;20" dur="3s" repeatCount="indefinite"/>
      <animate attributeName="y" values="28;32;28" dur="3s" repeatCount="indefinite"/>
    </rect>
    <!-- Stand -->
    <line x1="40" y1="52" x2="40" y2="58" stroke="white" stroke-width="2"/>
    <line x1="32" y1="58" x2="48" y2="58" stroke="white" stroke-width="2" stroke-linecap="round"/>
    <text x="40" y="68" text-anchor="middle" font-size="6" fill="white" font-family="sans-serif" font-weight="600">SLIDES</text>
  </svg>`)}`,

  notetaker: () => `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80">
    <defs>
      <linearGradient id="ng" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:#34d399"/>
        <stop offset="100%" style="stop-color:#60a5fa"/>
      </linearGradient>
    </defs>
    <circle cx="40" cy="40" r="38" fill="url(#ng)"/>
    <!-- Notepad -->
    <rect x="24" y="20" width="32" height="38" rx="3" fill="white" opacity="0.95"/>
    <!-- Lines -->
    <line x1="30" y1="30" x2="50" y2="30" stroke="#ccc" stroke-width="1.5"/>
    <line x1="30" y1="36" x2="46" y2="36" stroke="#ccc" stroke-width="1.5"/>
    <line x1="30" y1="42" x2="48" y2="42" stroke="#ccc" stroke-width="1.5"/>
    <!-- Pen writing -->
    <circle cx="42" cy="42" r="2" fill="#34d399">
      <animate attributeName="cx" values="30;48;30" dur="4s" repeatCount="indefinite"/>
    </circle>
    <!-- Mic -->
    <circle cx="58" cy="22" r="6" fill="rgba(255,255,255,0.3)"/>
    <rect x="56" y="18" width="4" height="7" rx="2" fill="white"/>
    <path d="M54 25 Q58 29 62 25" fill="none" stroke="white" stroke-width="1.5"/>
    <text x="40" y="68" text-anchor="middle" font-size="6" fill="white" font-family="sans-serif" font-weight="600">NOTES</text>
  </svg>`)}`,

  user: () => `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80">
    <circle cx="40" cy="40" r="38" fill="#3a3a50"/>
    <circle cx="40" cy="32" r="12" fill="#686880"/>
    <path d="M18 68 Q18 50 40 50 Q62 50 62 68" fill="#686880"/>
  </svg>`)}`,
};

// Get avatar for an agent by name
function getAvatar(agentName) {
  return (Avatars[agentName] || Avatars.eve)();
}
