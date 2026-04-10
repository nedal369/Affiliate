/**
 * sidebar.js — مساعدات الـ Sidebar
 */

// تحديث عداد قائمة الانتظار
function updateQueueBadge(count) {
  const badge = document.getElementById('queue-badge');
  if (!badge) return;
  if (count > 0) {
    badge.textContent = count;
    badge.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
  }
}

// دوال مساعدة مشتركة
function formatNumber(n) {
  if (!n && n !== 0) return '—';
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(0) + 'K';
  return n.toLocaleString('ar');
}

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('ar', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function renderStars(rating) {
  const full = Math.floor(rating);
  const half = rating - full >= 0.5;
  let html = '';
  for (let i = 0; i < 5; i++) {
    if (i < full) html += '★';
    else if (i === full && half) html += '⭑';
    else html += '☆';
  }
  return `<span class="stars">${html}</span> <span class="text-secondary text-sm">${rating.toFixed(1)}</span>`;
}

function statusBadge(status) {
  const map = {
    pending:   { label: 'انتظار',   cls: 'badge-warning' },
    approved:  { label: 'موافق',    cls: 'badge-info' },
    published: { label: 'منشور',    cls: 'badge-success' },
    rejected:  { label: 'مرفوض',   cls: 'badge-danger' },
    failed:    { label: 'فشل',      cls: 'badge-danger' },
  };
  const s = map[status] || { label: status, cls: 'badge-neutral' };
  return `<span class="badge ${s.cls}">${s.label}</span>`;
}

function sourceBadge(source) {
  const map = {
    tiktok:    '🎵 TikTok',
    instagram: '📸 Instagram',
    keyword:   '🔍 كلمة مفتاحية',
  };
  return map[source] || source || '—';
}
