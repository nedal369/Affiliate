/**
 * modal.js — نوافذ Modal
 */
function showModal({ title, body, onConfirm, confirmText = 'تأكيد', confirmClass = 'btn-primary', showCancel = true }) {
  const container = document.getElementById('modal-container');
  container.innerHTML = `
    <div class="modal-overlay" id="active-modal">
      <div class="modal">
        <div class="modal-header">
          <h3 class="modal-title">${title}</h3>
          <button class="modal-close" onclick="closeModal()">✕</button>
        </div>
        <div class="modal-body">${body}</div>
        <div class="modal-footer">
          ${showCancel ? '<button class="btn btn-secondary" onclick="closeModal()">إلغاء</button>' : ''}
          ${onConfirm ? `<button class="btn ${confirmClass}" id="modal-confirm-btn">${confirmText}</button>` : ''}
        </div>
      </div>
    </div>`;

  if (onConfirm) {
    document.getElementById('modal-confirm-btn').onclick = () => {
      onConfirm();
      closeModal();
    };
  }

  // إغلاق بالنقر خارج
  document.getElementById('active-modal').addEventListener('click', e => {
    if (e.target.id === 'active-modal') closeModal();
  });
}

function closeModal() {
  const container = document.getElementById('modal-container');
  container.innerHTML = '';
}

function showEditModal({ title, content, onSave }) {
  showModal({
    title,
    body: `<textarea class="form-control" id="edit-textarea" rows="10">${content}</textarea>`,
    confirmText: 'حفظ',
    confirmClass: 'btn-primary',
    onConfirm: () => {
      const val = document.getElementById('edit-textarea').value;
      if (onSave) onSave(val);
    },
  });
}

function showConfirmModal({ title, message, onConfirm, confirmText = 'تأكيد', confirmClass = 'btn-danger' }) {
  showModal({
    title,
    body: `<p style="color: var(--text-secondary); line-height: 1.6;">${message}</p>`,
    onConfirm,
    confirmText,
    confirmClass,
  });
}
