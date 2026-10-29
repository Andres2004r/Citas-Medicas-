document.querySelectorAll('[data-open]').forEach(function (b) {
  b.addEventListener('click', function () {
    var m = document.getElementById(b.dataset.open);
    var f = m.querySelector('form');
    f.reset();
    var item = b.dataset.item ? JSON.parse(b.dataset.item) : {};
    f.querySelectorAll('[name]').forEach(function (el) {
      if (item[el.name] !== undefined && item[el.name] !== null) el.value = item[el.name];
      else if (el.type === 'hidden') el.value = '';
    });
    var t = m.querySelector('[data-title]');
    if (t && b.dataset.title) t.textContent = b.dataset.title;
    m.classList.add('open');
  });
});
document.querySelectorAll('.modal').forEach(function (m) {
  m.addEventListener('click', function (e) {
    if (e.target === m || e.target.closest('[data-close]')) m.classList.remove('open');
  });
});
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') document.querySelectorAll('.modal.open').forEach(function (m) { m.classList.remove('open'); });
});
var auto = document.querySelector('[data-autoopen]');
if (auto) auto.click();
