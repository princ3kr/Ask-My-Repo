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
