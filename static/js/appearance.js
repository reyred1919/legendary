const families = {
  system: '"Vazirmatn", Tahoma, "Segoe UI", system-ui, sans-serif',
  tahoma: 'Tahoma, "Vazirmatn", Arial, sans-serif',
  arial: 'Arial, Tahoma, "Vazirmatn", sans-serif',
};
export function initAppearance() {
  const form = document.querySelector('[data-appearance-form]');
  const preview = document.querySelector('[data-appearance-preview]');
  if (!form || !preview) return;
  const update = () => {
    const value = (name) => form.elements.namedItem(name)?.value;
    preview.style.setProperty('--brand-primary', value('primary_color'));
    preview.style.setProperty('--brand-accent', value('accent_color'));
    preview.style.setProperty('--brand-base-size', `${value('base_font_size')}px`);
    preview.style.setProperty('--brand-font', families[value('font_family')] || families.system);
    preview.querySelector('[data-preview-name]').textContent = value('app_name') || 'نام سامانه';
  };
  form.addEventListener('input', update);
  form.addEventListener('change', update);
  update();
}
