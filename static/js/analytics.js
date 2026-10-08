const source = document.getElementById('report-chart-data');
const faDigits = (value) => String(value ?? '').replace(/\d/g, (digit) => '۰۱۲۳۴۵۶۷۸۹'[digit]);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const formatNumber = (value) => faDigits(new Intl.NumberFormat('fa-IR', { maximumFractionDigits: 1 }).format(value ?? 0));
const dateLabel = (value) => {
  if (!window.JalaliDate?.toJ || !value) return faDigits(value);
  const jalali = window.JalaliDate.toJ(new Date(`${value}T12:00:00`));
  return `${faDigits(jalali.jy)}/${faDigits(String(jalali.jm).padStart(2, '0'))}/${faDigits(String(jalali.jd).padStart(2, '0'))}`;
};

if (source) {
  const data = JSON.parse(source.textContent);
  const charts = [];
  const rootStyle = getComputedStyle(document.documentElement);
  const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || false;
  const palette = {
    primary: rootStyle.getPropertyValue('--primary').trim() || '#1764a5',
    accent: rootStyle.getPropertyValue('--accent').trim() || '#0d786c',
    success: rootStyle.getPropertyValue('--success').trim() || '#13745c',
    warning: rootStyle.getPropertyValue('--warning').trim() || '#87540c',
    danger: rootStyle.getPropertyValue('--danger').trim() || '#a83a39',
    text: rootStyle.getPropertyValue('--text-secondary').trim() || '#49647c',
    muted: rootStyle.getPropertyValue('--text-muted').trim() || '#62798e',
    border: rootStyle.getPropertyValue('--border').trim() || '#dbe4ec',
    surface: rootStyle.getPropertyValue('--surface').trim() || '#fff',
    font: rootStyle.getPropertyValue('--font-body').trim() || 'Vazirmatn, sans-serif',
  };
  const chartValues = (key) => Array.isArray(data[key]) ? data[key] : [];
  const chartText = {
    trend: 'درخواست‌های جدید و تکمیل‌شده در بازهٔ انتخابی',
    flow: 'تعداد درخواست‌ها در مراحل ثبت، بررسی، تأیید، اجرا و تکمیل',
    sla: 'درصد پایبندی و تعداد نمونه‌های قابل سنجش',
    department: 'تقسیم صف باز به‌موقع و متأخر، در کنار تکمیل‌شده‌ها',
    program: 'تقاضای ثبت‌شده به تفکیک طرح',
    project: 'تقاضای ثبت‌شده به تفکیک پروژه',
    owner: 'تقسیم صف باز مسئولان، با تفکیک درخواست‌های متأخر',
    service: 'ده خدمت پرتکرار در فیلترهای فعلی',
    family: 'خانواده‌های خدمت با بیشترین تقاضا',
    aging: 'تعداد درخواست‌های باز در هر بازهٔ سنی',
    matrix: 'تعداد درخواست‌ها در هر ترکیب دارای داده از طرح و اداره',
    priority: 'تعداد درخواست‌ها به تفکیک اولویت',
    credit: 'بهره‌برداری از تخصیص؛ جزئیات عددی در راهنما و جدول پایین صفحه',
    approval: 'تعداد تصمیم‌ها به تفکیک وضعیت',
    status: 'تعداد درخواست‌ها بر اساس وضعیت فعلی',
    resolution: 'تعداد درخواست‌های تکمیل‌شده در هر بازهٔ زمان حل',
  };

  function numberAxis() {
    return {
      type: 'value', min: 0,
      axisLabel: { color: palette.muted, formatter: (value) => formatNumber(value) },
      axisLine: { show: false }, axisTick: { show: false }, splitLine: { lineStyle: { color: palette.border, type: 'dashed' } },
    };
  }

  function categoryAxis(labels) {
    return {
      type: 'category', data: labels, position: 'right', inverse: true,
      axisLabel: { color: palette.text, width: 126, overflow: 'truncate', formatter: (value) => value.length > 20 ? `${value.slice(0, 19)}…` : value },
      axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false },
    };
  }

  function countTooltip(params) {
    const list = Array.isArray(params) ? params : [params];
    return list.map((item) => {
      const name = escapeHtml(item.name || item.axisValueLabel || '');
      const value = Array.isArray(item.value) ? item.value[item.value.length - 1] : item.value;
      return `${name}<br>${escapeHtml(item.seriesName || 'تعداد')}: <b>${formatNumber(value)}</b>`;
    }).join('<br><br>');
  }

  function baseOption() {
    return {
      animation: !reduceMotion,
      animationDuration: reduceMotion ? 0 : 240,
      animationDurationUpdate: reduceMotion ? 0 : 180,
      textStyle: { fontFamily: palette.font, color: palette.text },
      aria: { enabled: true, decal: { show: true } },
      grid: { left: 18, right: 142, top: 22, bottom: 30, containLabel: false },
      tooltip: { trigger: 'axis', confine: true, appendToBody: true, textStyle: { fontFamily: palette.font }, extraCssText: 'direction:rtl;border-radius:12px;box-shadow:0 8px 28px #102b461f', formatter: countTooltip },
      xAxis: { ...numberAxis(), inverse: true },
      yAxis: categoryAxis([]),
    };
  }

  function horizontalOptions(rows, options = {}) {
    const sorted = [...rows];
    if (options.sort !== false) sorted.sort((a, b) => Number(a.total || 0) - Number(b.total || 0));
    const config = baseOption();
    config.grid.right = options.right || 150;
    config.grid.bottom = 22;
    config.xAxis = { ...numberAxis(), inverse: true, max: options.max || null, axisLabel: { color: palette.muted, formatter: options.percent ? (v) => `${formatNumber(v)}٪` : (v) => formatNumber(v) } };
    config.yAxis = categoryAxis(sorted.map((row) => row.name));
    config.tooltip.formatter = (params) => {
      const item = Array.isArray(params) ? params[0] : params;
      const row = sorted[item.dataIndex];
      if (!row) return '';
      const val = options.percent ? `${formatNumber(row.total)}٪` : formatNumber(row.total);
      const extras = options.extra ? options.extra(row) : '';
      return `${escapeHtml(row.name)}<br>${escapeHtml(options.valueLabel || 'تعداد')}: <b>${val}</b>${extras}`;
    };
    if (options.series) config.series = options.series(sorted);
    else config.series = [{ name: options.valueLabel || 'تعداد', type: 'bar', data: sorted.map((row) => ({ value: row.total || 0, url: row.url })), barMaxWidth: 24, itemStyle: { color: options.color || palette.primary, borderRadius: [4, 0, 0, 4] }, emphasis: { itemStyle: { color: palette.accent } } }];
    return { config, rows: sorted };
  }

  function trendOptions(rows) {
    const config = baseOption();
    const labels = rows.map((row) => dateLabel(row.date));
    config.grid = { left: 48, right: 28, top: 38, bottom: 38, containLabel: true };
    config.legend = { top: 0, right: 0, textStyle: { color: palette.text, fontFamily: palette.font }, itemWidth: 12, itemHeight: 8 };
    config.tooltip = { trigger: 'axis', confine: true, textStyle: { fontFamily: palette.font }, extraCssText: 'direction:rtl;border-radius:12px;box-shadow:0 8px 28px #102b461f', formatter: (items) => {
      const first = items[0];
      const title = escapeHtml(labels[first?.dataIndex] || '');
      return `${title}<br>${items.map((item) => `${escapeHtml(item.seriesName)}: <b>${formatNumber(item.value)}</b>`).join('<br>')}`;
    } };
    config.xAxis = { type: 'category', data: labels, inverse: true, boundaryGap: false, axisLabel: { color: palette.muted, hideOverlap: true }, axisLine: { lineStyle: { color: palette.border } }, axisTick: { show: false } };
    config.yAxis = { ...numberAxis(), inverse: false, splitNumber: 4 };
    config.series = [
      { name: 'درخواست جدید', type: 'line', data: rows.map((row) => row.new), smooth: false, showSymbol: rows.length < 20, symbolSize: 5, lineStyle: { width: 3, color: palette.primary }, itemStyle: { color: palette.primary }, areaStyle: { color: palette.primary, opacity: .08 } },
      { name: 'تکمیل‌شده', type: 'line', data: rows.map((row) => row.completed), smooth: false, showSymbol: rows.length < 20, symbolSize: 5, lineStyle: { width: 3, color: palette.accent }, itemStyle: { color: palette.accent }, areaStyle: { color: palette.accent, opacity: .06 } },
    ];
    return config;
  }

  function workloadOptions(rows) {
    const sorted = [...rows].sort((a, b) => Number(a.open || 0) - Number(b.open || 0));
    const config = baseOption();
    config.grid.right = 142;
    config.yAxis = categoryAxis(sorted.map((row) => row.name));
    config.series = [
      { name: 'باز به‌موقع', type: 'bar', stack: 'workload', data: sorted.map((r) => ({ value: Math.max(0, (r.open || 0) - (r.overdue || 0)), url: r.url })), barMaxWidth: 23, itemStyle: { color: palette.primary }, emphasis: { focus: 'series' } },
      { name: 'متأخر', type: 'bar', stack: 'workload', data: sorted.map((r) => ({ value: r.overdue || 0, url: r.url })), barMaxWidth: 23, itemStyle: { color: palette.danger }, emphasis: { focus: 'series' } },
      { name: 'تکمیل‌شده', type: 'bar', stack: 'workload', data: sorted.map((r) => ({ value: r.completed || 0, url: r.url })), barMaxWidth: 23, itemStyle: { color: palette.success }, emphasis: { focus: 'series' } },
    ];
    config.legend = { top: 0, right: 0, textStyle: { color: palette.text, fontFamily: palette.font }, itemWidth: 12, itemHeight: 8 };
    config.grid.top = 42;
    config.tooltip.formatter = (params) => {
      const items = Array.isArray(params) ? params : [params];
      const name = escapeHtml(items[0]?.name || '');
      return `${name}<br>${items.map((item) => `${escapeHtml(item.seriesName)}: <b>${formatNumber(item.value)}</b>`).join('<br>')}<br><small>درخواست‌های متأخر بخشی از صف باز هستند.</small>`;
    };
    return { config, rows: sorted };
  }

  function matrixOptions(rows) {
    const departments = [...new Set(rows.map((row) => row.department))];
    const programs = [...new Set(rows.map((row) => row.program))];
    const config = baseOption();
    config.grid = { left: 110, right: 12, top: 48, bottom: 34, containLabel: true };
    config.tooltip = { position: 'top', confine: true, textStyle: { fontFamily: palette.font }, extraCssText: 'direction:rtl;border-radius:12px', formatter: (item) => {
      const cell = item.data?.row;
      return cell ? `${escapeHtml(cell.program)}<br>${escapeHtml(cell.department)}<br>درخواست: <b>${formatNumber(cell.total)}</b><br>باز: ${formatNumber(cell.open)} · متأخر: ${formatNumber(cell.overdue)}` : '';
    } };
    config.xAxis = { type: 'category', data: departments, position: 'top', inverse: true, axisLabel: { color: palette.text, interval: 0, width: 112, overflow: 'truncate' }, axisLine: { show: false }, axisTick: { show: false }, splitArea: { show: true, areaStyle: { color: ['#f5f8fb', '#fff'] } } };
    config.yAxis = { type: 'category', data: programs, inverse: true, axisLabel: { color: palette.text, width: 90, overflow: 'truncate' }, axisLine: { show: false }, axisTick: { show: false }, splitArea: { show: true, areaStyle: { color: ['#f5f8fb', '#fff'] } } };
    config.visualMap = { min: 0, max: Math.max(1, ...rows.map((row) => Number(row.total || 0))), calculable: false, orient: 'horizontal', left: 'center', bottom: 0, itemWidth: 14, itemHeight: 110, textStyle: { color: palette.text, fontFamily: palette.font }, inRange: { color: ['#e9f2f8', '#8cb8d3', palette.primary, '#153b5d'] } };
    config.series = [{ name: 'درخواست', type: 'heatmap', data: rows.map((row) => ({ value: [departments.indexOf(row.department), programs.indexOf(row.program), row.total], row, url: row.url })), label: { show: rows.length <= 36, color: '#16334d', formatter: (item) => formatNumber(item.value[2]), fontFamily: palette.font, fontSize: 12 }, itemStyle: { borderColor: palette.surface, borderWidth: 3, borderRadius: 5 }, emphasis: { itemStyle: { shadowBlur: 8, shadowColor: '#12324c44' } } }];
    return { config, rows };
  }

  function getConfiguration(key) {
    const rows = chartValues(key);
    if (key === 'trend') return rows.length ? { config: trendOptions(rows), rows, kind: key } : null;
    if (key === 'department' || key === 'owner') return rows.length ? { ...workloadOptions(rows), kind: key } : null;
    if (key === 'matrix') return rows.length ? { ...matrixOptions(rows), kind: key } : null;
    if (key === 'sla') return rows.length ? { ...horizontalOptions(rows, { percent: true, max: 100, valueLabel: 'پایبندی', extra: (row) => `<br>نمونهٔ قابل سنجش: ${formatNumber(row.samples)}` }), kind: key } : null;
    if (key === 'credit') return rows.length ? { ...horizontalOptions(rows, { percent: true, max: 100, valueLabel: 'بهره‌برداری', extra: (row) => `<br>تخصیص: ${formatNumber(row.quantity)} · رزرو: ${formatNumber(row.reserved)} · مصرف: ${formatNumber(row.consumed)} · مانده: ${formatNumber(row.remaining)} · آزادشده: ${formatNumber(row.released)}` }), kind: key } : null;
    if (key === 'aging') {
      const colors = [palette.success, '#6e9b72', palette.warning, '#bd7139', palette.danger];
      return rows.length ? { ...horizontalOptions(rows, { sort: false, color: palette.primary, series: (items) => [{ name: 'درخواست باز', type: 'bar', data: items.map((row, i) => ({ value: row.total, itemStyle: { color: colors[i] } })), barMaxWidth: 24, itemStyle: { borderRadius: [4, 0, 0, 4] } }] }), kind: key } : null;
    }
    if (key === 'flow' || key === 'service' || key === 'program' || key === 'project' || key === 'family' || key === 'resolution' || key === 'priority' || key === 'status' || key === 'approval') {
      return rows.length ? { ...horizontalOptions(rows, { sort: key !== 'resolution' && key !== 'flow' }), kind: key } : null;
    }
    return null;
  }

  function renderValueTable(key, config) {
    const host = document.querySelector(`[data-chart-table="${key}"]`);
    if (!host || !config) return;
    const table = document.createElement('table');
    table.className = 'chart-values-table';
    const head = document.createElement('thead');
    const body = document.createElement('tbody');
    const header = document.createElement('tr');
    const columns = key === 'trend' ? ['تاریخ', 'درخواست جدید', 'تکمیل‌شده'] : key === 'matrix' ? ['طرح', 'اداره', 'کل', 'باز', 'متأخر'] : key === 'credit' ? ['طرح / اداره / اولویت', 'بهره‌برداری', 'تخصیص', 'رزرو', 'مصرف', 'آزادشده', 'مانده'] : ['عنوان', key === 'sla' ? 'درصد' : key === 'credit' ? 'درصد' : 'تعداد'];
    columns.forEach((label) => { const cell = document.createElement('th'); cell.scope = 'col'; cell.textContent = label; header.append(cell); });
    head.append(header);
    const rows = config.rows || [];
    rows.forEach((row) => {
      const tr = document.createElement('tr');
      let values;
      if (key === 'trend') values = [dateLabel(row.date), formatNumber(row.new), formatNumber(row.completed)];
      else if (key === 'matrix') values = [row.program, row.department, formatNumber(row.total), formatNumber(row.open), formatNumber(row.overdue)];
      else if (key === 'credit') values = [row.name, `${formatNumber(row.total)}٪`, formatNumber(row.quantity), formatNumber(row.reserved), formatNumber(row.consumed), formatNumber(row.released), formatNumber(row.remaining)];
      else values = [row.name, key === 'sla' ? `${formatNumber(row.total)}٪ · ${formatNumber(row.samples)} نمونه` : formatNumber(row.total)];
      values.forEach((value) => { const cell = document.createElement('td'); cell.textContent = value ?? ''; tr.append(cell); });
      body.append(tr);
    });
    table.append(head, body);
    host.replaceChildren(table);
  }

  document.querySelectorAll('[data-report-chart]').forEach((element) => {
    const key = element.dataset.reportChart;
    const descriptor = getConfiguration(key);
    const subtitle = document.querySelector(`[data-chart-subtitle="${key}"]`);
    if (subtitle) subtitle.textContent = chartText[key] || '';
    if (!descriptor) {
      element.innerHTML = '<p class="chart-empty">برای فیلترهای انتخاب‌شده داده‌ای وجود ندارد.</p>';
      return;
    }
    const chart = echarts.init(element, null, { renderer: 'canvas' });
    chart.setOption(descriptor.config);
    const hint = document.querySelector(`[data-chart-hint="${key}"]`);
    const linked = descriptor.rows.some((row) => row.url);
    if (linked && hint) { hint.textContent = 'برای دیدن درخواست‌ها انتخاب کنید'; hint.classList.add('is-interactive'); }
    chart.on('click', (event) => {
      const target = event.data?.url || event.data?.row?.url;
      if (target) window.location.assign(target);
    });
    renderValueTable(key, descriptor);
    charts.push(chart);
  });

  let resizeFrame;
  window.addEventListener('resize', () => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => charts.forEach((chart) => chart.resize()));
  }, { passive: true });
}

const filterForm = document.querySelector('[data-analytics-filters]');
if (filterForm) {
  const advanced = filterForm.querySelector('[data-advanced-filters]');
  const count = filterForm.querySelector('[data-advanced-count]');
  const chips = filterForm.querySelector('[data-active-filters]');
  const controls = [...filterForm.querySelectorAll('select[name],input[name]')];
  const ignore = new Set(['csrfmiddlewaretoken']);
  const labelFor = (control) => {
    if (control.name === 'start') return 'از تاریخ';
    if (control.name === 'end') return 'تا تاریخ';
    return control.closest('label')?.childNodes?.[0]?.textContent?.trim() || control.name;
  };
  const displayValue = (control) => {
    if (control.classList.contains('jalali-iso')) return control.previousElementSibling?.textContent?.trim() || control.value;
    if (control.tagName === 'SELECT') return control.selectedOptions[0]?.textContent?.trim() || control.value;
    return control.value;
  };
  const isActive = (control) => control.value && !(control.name === 'period' && control.value === '30d');
  const renderFilters = () => {
    const active = controls.filter((control) => !ignore.has(control.name) && isActive(control));
    const advancedNames = new Set(['start', 'end', 'program', 'project', 'family', 'service', 'priority', 'requester', 'unit', 'owner']);
    const advancedCount = active.filter((control) => advancedNames.has(control.name)).length;
    if (count) count.textContent = formatNumber(advancedCount);
    if (!chips) return;
    chips.replaceChildren();
    if (!active.length) return;
    const caption = document.createElement('span'); caption.className = 'active-filter-caption'; caption.textContent = 'فیلترهای فعال'; chips.append(caption);
    active.forEach((control) => {
      const chip = document.createElement('button'); chip.type = 'button'; chip.className = 'filter-chip';
      chip.dataset.removeFilter = control.name;
      chip.textContent = `${labelFor(control)}: ${displayValue(control)} ×`;
      chips.append(chip);
    });
  };
  filterForm.addEventListener('change', (event) => {
    if (event.target.name === 'period' && event.target.value === 'custom' && advanced) advanced.open = true;
    renderFilters();
  });
  chips?.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-remove-filter]');
    if (!chip) return;
    const control = controls.find((item) => item.name === chip.dataset.removeFilter);
    if (!control) return;
    if (control.name === 'start' || control.name === 'end') {
      control.value = '';
      if (control.previousElementSibling) control.previousElementSibling.textContent = 'انتخاب تاریخ';
      if (filterForm.elements.period?.value === 'custom') filterForm.elements.period.value = '30d';
    } else control.value = '';
    filterForm.requestSubmit();
  });
  renderFilters();

  const select = (name) => filterForm.querySelector(`select[name="${name}"]`);
  function dependent(parent, child, attribute) {
    if (!parent || !child) return;
    const refresh = () => {
      for (const option of child.options) {
        if (!option.value) continue;
        option.hidden = Boolean(parent.value && option.dataset[attribute] !== parent.value);
        option.disabled = option.hidden;
      }
      if (child.selectedOptions[0]?.disabled) child.value = '';
    };
    parent.addEventListener('change', refresh); refresh();
  }
  dependent(select('program'), select('project'), 'program');
  dependent(select('department'), select('family'), 'department');
  const family = select('family'), department = select('department'), service = select('service');
  if (service) {
    const refresh = () => {
      for (const option of service.options) {
        if (!option.value) continue;
        option.hidden = Boolean((family?.value && option.dataset.family !== family.value) || (department?.value && option.dataset.department !== department.value));
        option.disabled = option.hidden;
      }
      if (service.selectedOptions[0]?.disabled) service.value = '';
    };
    family?.addEventListener('change', refresh); department?.addEventListener('change', refresh); refresh();
  }
}
