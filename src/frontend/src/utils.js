export const normalizeRepoUrl = (url) => {
    const trimmed = url.trim();
    if (!trimmed) return '';
    if (/^https?:\/\//i.test(trimmed) || trimmed.startsWith('git@')) return trimmed.replace(/\/$/, '');
    return `https://${trimmed.replace(/\/$/, '')}`;
};

export const repoShortName = (url) => {
    if (!url) return '';
    try {
        const parts = new URL(url).pathname.split('/').filter(Boolean);
        return parts.length >= 2 ? parts.slice(-2).join('/') : parts[parts.length - 1] || url;
    } catch {
        return url.split('/').slice(-2).join('/') || url;
    }
};

// localStorage throws outright when storage is blocked (Safari private mode,
// hardened profiles, some embedded webviews). The session id was read in a
// useState initializer, so a throw there white-screened the app on first
// render. Every access is guarded.
export const safeStorage = {
    get(key) {
        try {
            return window.localStorage.getItem(key);
        } catch {
            return null;
        }
    },
    set(key, value) {
        try {
            window.localStorage.setItem(key, value);
        } catch {
            /* non-fatal: the id just won't survive a reload */
        }
    },
};

// crypto.randomUUID is restricted to secure contexts, so it is undefined on
// http://<lan-ip>:5173 — a normal way to demo this app on a second device.
export const newSessionId = () =>
    globalThis.crypto?.randomUUID?.()
    ?? `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
