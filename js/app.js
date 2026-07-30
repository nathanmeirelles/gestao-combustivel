(() => {
  'use strict';

  const STORAGE_KEY = 'gas-entries-v1';
  const THEME_KEY = 'gas-theme';

  const MONTHS_FULL = ['janeiro','fevereiro','março','abril','maio','junho','julho','agosto','setembro','outubro','novembro','dezembro'];
  const MONTHS_SHORT = ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'];

  const currencyFmt = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
  const priceFmt = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 3 });

  // Referência Inmetro (ciclo cidade) para o Onix LT 1.0 2023
  const FUEL_LABEL = { gasolina: 'Gasolina', etanol: 'Etanol' };
  const FUEL_REF_CITY = { gasolina: 13.3, etanol: 9.9 };

  // ---------- Elements ----------
  const entryForm = document.getElementById('entryForm');
  const entryIdInput = document.getElementById('entryId');
  const dateInput = document.getElementById('date');
  const fuelInput = document.getElementById('fuel');
  const priceInput = document.getElementById('price');
  const litersInput = document.getElementById('liters');
  const kmInput = document.getElementById('km');
  const submitBtn = document.getElementById('submitBtn');
  const cancelEditBtn = document.getElementById('cancelEditBtn');
  const clearAllBtn = document.getElementById('clearAllBtn');

  const monthSelect = document.getElementById('monthSelect');
  const statSpend = document.getElementById('statSpend');
  const statLiters = document.getElementById('statLiters');
  const statKm = document.getElementById('statKm');
  const statConsumption = document.getElementById('statConsumption');
  const statAvgPrice = document.getElementById('statAvgPrice');
  const statVsReference = document.getElementById('statVsReference');
  const statVsReferenceBadge = document.getElementById('statVsReferenceBadge');

  const entriesTableBody = document.getElementById('entriesTableBody');
  const emptyState = document.getElementById('emptyState');

  const monthlyReportCard = document.getElementById('monthlyReportCard');
  const monthlyTableBody = document.getElementById('monthlyTableBody');

  const chartsCard = document.getElementById('chartsCard');
  const chartSpendEl = document.getElementById('chartSpend');
  const chartConsumptionEl = document.getElementById('chartConsumption');

  const themeToggle = document.getElementById('themeToggle');
  const tooltip = document.getElementById('tooltip');

  // ---------- State ----------
  let entries = load();
  let selectedMonth = null;

  // ---------- Storage ----------
  function load() {
    try {
      const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      return Array.isArray(raw) ? raw : [];
    } catch (e) {
      return [];
    }
  }

  function persist() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  }

  // ---------- Helpers ----------
  function monthKeyOf(dateStr) {
    return dateStr.slice(0, 7);
  }

  function monthLabelShort(key) {
    const [y, m] = key.split('-');
    return `${MONTHS_SHORT[Number(m) - 1]}/${y.slice(2)}`;
  }

  function monthLabelFull(key) {
    const [y, m] = key.split('-');
    const name = MONTHS_FULL[Number(m) - 1];
    return `${name.charAt(0).toUpperCase()}${name.slice(1)} de ${y}`;
  }

  function formatDate(dateStr) {
    const [y, m, d] = dateStr.split('-');
    return `${d}/${m}/${y}`;
  }

  function num(n, decimals) {
    return n.toLocaleString('pt-BR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }

  function entryTotal(e) {
    return e.price * e.liters;
  }

  function entryConsumption(e) {
    return e.km > 0 ? e.km / e.liters : null;
  }

  function entryFuel(e) {
    return e.fuel === 'etanol' ? 'etanol' : 'gasolina';
  }

  function groupByMonth() {
    const map = new Map();
    for (const e of entries) {
      const key = monthKeyOf(e.date);
      if (!map.has(key)) map.set(key, { liters: 0, spend: 0, km: 0, count: 0, litersByFuel: { gasolina: 0, etanol: 0 } });
      const agg = map.get(key);
      agg.liters += e.liters;
      agg.spend += entryTotal(e);
      agg.km += e.km || 0;
      agg.count += 1;
      agg.litersByFuel[entryFuel(e)] += e.liters;
    }
    return map;
  }

  function referenceConsumption(litersByFuel) {
    const total = litersByFuel.gasolina + litersByFuel.etanol;
    if (total <= 0) return null;
    return (litersByFuel.gasolina * FUEL_REF_CITY.gasolina + litersByFuel.etanol * FUEL_REF_CITY.etanol) / total;
  }

  function uid() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }

  // ---------- Rendering: entries table ----------
  function renderEntriesTable() {
    const sorted = [...entries].sort((a, b) => b.date.localeCompare(a.date) || b.id.localeCompare(a.id));
    entriesTableBody.innerHTML = '';
    emptyState.hidden = sorted.length > 0;

    for (const e of sorted) {
      const tr = document.createElement('tr');

      const cons = entryConsumption(e);
      const cells = [
        formatDate(e.date),
        FUEL_LABEL[entryFuel(e)],
        `${priceFmt.format(e.price)}`,
        `${num(e.liters, 2)} L`,
        e.km ? `${num(e.km, 1)} km` : '—',
        cons !== null ? `${num(cons, 2)} km/L` : '—',
        currencyFmt.format(entryTotal(e)),
      ];
      for (const c of cells) {
        const td = document.createElement('td');
        td.textContent = c;
        tr.appendChild(td);
      }

      const actionsTd = document.createElement('td');
      actionsTd.className = 'row-actions';

      const editBtn = document.createElement('button');
      editBtn.type = 'button';
      editBtn.className = 'icon-btn';
      editBtn.textContent = 'Editar';
      editBtn.addEventListener('click', () => startEdit(e.id));

      const delBtn = document.createElement('button');
      delBtn.type = 'button';
      delBtn.className = 'icon-btn';
      delBtn.textContent = 'Excluir';
      delBtn.addEventListener('click', () => deleteEntry(e.id));

      actionsTd.appendChild(editBtn);
      actionsTd.appendChild(delBtn);
      tr.appendChild(actionsTd);

      entriesTableBody.appendChild(tr);
    }
  }

  // ---------- Rendering: month select + stat tiles ----------
  function renderMonthSelect() {
    const keys = new Set(groupByMonth().keys());
    const todayKey = new Date().toISOString().slice(0, 7);
    keys.add(todayKey);
    const sortedKeys = [...keys].sort().reverse();

    if (!selectedMonth || !sortedKeys.includes(selectedMonth)) {
      selectedMonth = sortedKeys[0];
    }

    monthSelect.innerHTML = '';
    for (const key of sortedKeys) {
      const opt = document.createElement('option');
      opt.value = key;
      opt.textContent = monthLabelFull(key);
      if (key === selectedMonth) opt.selected = true;
      monthSelect.appendChild(opt);
    }
  }

  function renderStats() {
    const agg = groupByMonth().get(selectedMonth) || { liters: 0, spend: 0, km: 0, count: 0, litersByFuel: { gasolina: 0, etanol: 0 } };
    statSpend.textContent = currencyFmt.format(agg.spend);
    statLiters.textContent = `${num(agg.liters, 2)} L`;
    statKm.textContent = `${num(agg.km, 1)} km`;
    const actualConsumption = agg.liters > 0 && agg.km > 0 ? agg.km / agg.liters : null;
    statConsumption.textContent = actualConsumption !== null ? `${num(actualConsumption, 2)} km/L` : '—';
    statAvgPrice.textContent = agg.liters > 0 ? `${currencyFmt.format(agg.spend / agg.liters)}/L` : '—';

    const ref = referenceConsumption(agg.litersByFuel);
    if (actualConsumption !== null && ref) {
      const ratio = actualConsumption / ref;
      const diffPct = (ratio - 1) * 100;
      const sign = diffPct >= 0 ? '+' : '';
      statVsReference.textContent = `${sign}${num(diffPct, 0)}%`;
      statVsReferenceBadge.hidden = false;
      let status, label;
      if (ratio >= 0.95) { status = 'good'; label = 'Dentro do esperado'; }
      else if (ratio >= 0.8) { status = 'warning'; label = 'Abaixo do esperado'; }
      else { status = 'critical'; label = 'Muito abaixo do esperado'; }
      statVsReferenceBadge.className = `status-badge status-${status}`;
      statVsReferenceBadge.textContent = label;
    } else {
      statVsReference.textContent = '—';
      statVsReferenceBadge.hidden = true;
    }
  }

  // ---------- Rendering: monthly report table ----------
  function renderMonthlyTable() {
    const map = groupByMonth();
    const keys = [...map.keys()].sort().reverse();
    monthlyReportCard.hidden = keys.length === 0;
    monthlyTableBody.innerHTML = '';

    for (const key of keys) {
      const agg = map.get(key);
      const tr = document.createElement('tr');
      const avgCons = agg.liters > 0 && agg.km > 0 ? `${num(agg.km / agg.liters, 2)} km/L` : '—';
      const avgPrice = agg.liters > 0 ? `${currencyFmt.format(agg.spend / agg.liters)}/L` : '—';
      const cells = [
        monthLabelFull(key),
        String(agg.count),
        `${num(agg.liters, 2)} L`,
        `${num(agg.km, 1)} km`,
        avgCons,
        avgPrice,
        currencyFmt.format(agg.spend),
      ];
      for (const c of cells) {
        const td = document.createElement('td');
        td.textContent = c;
        tr.appendChild(td);
      }
      monthlyTableBody.appendChild(tr);
    }
  }

  // ---------- Rendering: charts ----------
  function renderCharts() {
    const map = groupByMonth();
    const keys = [...map.keys()].sort();
    chartsCard.hidden = keys.length === 0;
    if (keys.length === 0) return;

    const spendData = keys.map((key) => ({
      key,
      label: monthLabelShort(key),
      fullLabel: monthLabelFull(key),
      value: map.get(key).spend,
    }));

    const consumptionData = keys.map((key) => {
      const agg = map.get(key);
      return {
        key,
        label: monthLabelShort(key),
        fullLabel: monthLabelFull(key),
        value: agg.liters > 0 && agg.km > 0 ? agg.km / agg.liters : 0,
      };
    });

    renderBarChart(chartSpendEl, spendData, {
      formatValue: (v) => currencyFmt.format(v),
      ariaLabel: 'Gasto total por mês',
    });

    renderBarChart(chartConsumptionEl, consumptionData, {
      formatValue: (v) => `${num(v, 2)} km/L`,
      ariaLabel: 'Consumo médio por mês',
    });
  }

  function roundedTopPath(x, y, w, h, r) {
    const radius = Math.min(r, w / 2, Math.max(h, 0.01));
    if (h <= 0.5) {
      return `M${x},${y} h${w} v0.5 h${-w} Z`;
    }
    return `M${x},${y + h}
            L${x},${y + radius}
            Q${x},${y} ${x + radius},${y}
            L${x + w - radius},${y}
            Q${x + w},${y} ${x + w},${y + radius}
            L${x + w},${y + h}
            Z`;
  }

  function renderBarChart(container, data, opts) {
    container.innerHTML = '';
    const svgNS = 'http://www.w3.org/2000/svg';

    if (!data.length || data.every((d) => !d.value)) {
      const p = document.createElement('p');
      p.className = 'empty-state';
      p.textContent = 'Dados insuficientes para este gráfico.';
      container.appendChild(p);
      return;
    }

    const slotW = 68;
    const W = Math.max(data.length * slotW, 260);
    const H = 220;
    const padTop = 26;
    const padBottom = 28;
    const chartH = H - padTop - padBottom;
    const maxVal = Math.max(...data.map((d) => d.value), 0.0001);
    const barWidth = Math.min(24, slotW * 0.45);
    const baseY = padTop + chartH;
    const showValueLabels = data.length <= 8;

    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', opts.ariaLabel);

    const baseline = document.createElementNS(svgNS, 'line');
    baseline.setAttribute('x1', 0);
    baseline.setAttribute('x2', W);
    baseline.setAttribute('y1', baseY);
    baseline.setAttribute('y2', baseY);
    baseline.setAttribute('class', 'chart-baseline');
    svg.appendChild(baseline);

    data.forEach((d, i) => {
      const slotX = slotW * i;
      const cx = slotX + slotW / 2;
      const h = maxVal > 0 ? (d.value / maxVal) * chartH : 0;
      const y = baseY - h;

      const bar = document.createElementNS(svgNS, 'path');
      bar.setAttribute('d', roundedTopPath(cx - barWidth / 2, y, barWidth, h, 4));
      bar.setAttribute('class', 'bar-mark');
      svg.appendChild(bar);

      if (showValueLabels && h > 18) {
        const label = document.createElementNS(svgNS, 'text');
        label.setAttribute('x', cx);
        label.setAttribute('y', y - 6);
        label.setAttribute('text-anchor', 'middle');
        label.setAttribute('class', 'bar-value-label');
        label.textContent = opts.formatValue(d.value);
        svg.appendChild(label);
      }

      const axisLabel = document.createElementNS(svgNS, 'text');
      axisLabel.setAttribute('x', cx);
      axisLabel.setAttribute('y', baseY + 18);
      axisLabel.setAttribute('text-anchor', 'middle');
      axisLabel.setAttribute('class', 'axis-label');
      axisLabel.textContent = d.label;
      svg.appendChild(axisLabel);

      const hit = document.createElementNS(svgNS, 'rect');
      hit.setAttribute('x', slotX);
      hit.setAttribute('y', padTop);
      hit.setAttribute('width', slotW);
      hit.setAttribute('height', chartH);
      hit.setAttribute('fill', 'transparent');
      hit.setAttribute('tabindex', '0');
      hit.style.cursor = 'pointer';

      const show = (evt) => {
        bar.classList.add('hovered');
        showTooltip(evt, d.fullLabel, opts.formatValue(d.value));
      };
      const hide = () => {
        bar.classList.remove('hovered');
        hideTooltip();
      };

      hit.addEventListener('pointermove', show);
      hit.addEventListener('pointerenter', show);
      hit.addEventListener('pointerleave', hide);
      hit.addEventListener('focus', (evt) => show(evt));
      hit.addEventListener('blur', hide);

      svg.appendChild(hit);
    });

    container.appendChild(svg);
  }

  function showTooltip(evt, label, value) {
    tooltip.innerHTML = '';
    const line1 = document.createElement('div');
    line1.textContent = label;
    const line2 = document.createElement('strong');
    line2.textContent = value;
    tooltip.appendChild(line1);
    tooltip.appendChild(line2);
    tooltip.hidden = false;

    const x = evt.clientX ?? (evt.target.getBoundingClientRect().left + evt.target.getBoundingClientRect().width / 2);
    const y = evt.clientY ?? evt.target.getBoundingClientRect().top;
    tooltip.style.left = `${x + 14}px`;
    tooltip.style.top = `${y + 14}px`;
  }

  function hideTooltip() {
    tooltip.hidden = true;
  }

  // ---------- Render all ----------
  function renderAll() {
    renderEntriesTable();
    renderMonthSelect();
    renderStats();
    renderMonthlyTable();
    renderCharts();
  }

  // ---------- Form logic ----------
  function resetForm() {
    entryForm.reset();
    entryIdInput.value = '';
    submitBtn.textContent = 'Adicionar';
    cancelEditBtn.hidden = true;
    dateInput.value = new Date().toISOString().slice(0, 10);
  }

  function startEdit(id) {
    const e = entries.find((x) => x.id === id);
    if (!e) return;
    entryIdInput.value = e.id;
    dateInput.value = e.date;
    fuelInput.value = entryFuel(e);
    priceInput.value = e.price;
    litersInput.value = e.liters;
    kmInput.value = e.km || '';
    submitBtn.textContent = 'Salvar alterações';
    cancelEditBtn.hidden = false;
    entryForm.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function deleteEntry(id) {
    if (!confirm('Excluir este abastecimento?')) return;
    entries = entries.filter((x) => x.id !== id);
    persist();
    renderAll();
  }

  entryForm.addEventListener('submit', (evt) => {
    evt.preventDefault();

    const date = dateInput.value;
    const fuel = fuelInput.value === 'etanol' ? 'etanol' : 'gasolina';
    const price = parseFloat(priceInput.value);
    const liters = parseFloat(litersInput.value);
    const km = kmInput.value ? parseFloat(kmInput.value) : 0;

    if (!date || !Number.isFinite(price) || price <= 0 || !Number.isFinite(liters) || liters <= 0) {
      return;
    }

    const id = entryIdInput.value;
    if (id) {
      const existing = entries.find((x) => x.id === id);
      if (existing) {
        existing.date = date;
        existing.fuel = fuel;
        existing.price = price;
        existing.liters = liters;
        existing.km = km;
      }
    } else {
      entries.push({ id: uid(), date, fuel, price, liters, km });
    }

    persist();
    resetForm();
    renderAll();
  });

  cancelEditBtn.addEventListener('click', resetForm);

  clearAllBtn.addEventListener('click', () => {
    if (!entries.length) return;
    if (!confirm('Tem certeza que deseja apagar todos os abastecimentos registrados? Esta ação não pode ser desfeita.')) return;
    entries = [];
    persist();
    renderAll();
  });

  monthSelect.addEventListener('change', () => {
    selectedMonth = monthSelect.value;
    renderStats();
  });

  // ---------- Theme ----------
  function updateThemeButton() {
    const stored = document.documentElement.dataset.theme;
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const isDark = stored ? stored === 'dark' : prefersDark;
    themeToggle.textContent = isDark ? '☀️' : '🌙';
  }

  const storedTheme = localStorage.getItem(THEME_KEY);
  if (storedTheme) document.documentElement.dataset.theme = storedTheme;
  updateThemeButton();

  themeToggle.addEventListener('click', () => {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const current = document.documentElement.dataset.theme || (prefersDark ? 'dark' : 'light');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem(THEME_KEY, next);
    updateThemeButton();
  });

  // ---------- Init ----------
  dateInput.value = new Date().toISOString().slice(0, 10);
  renderAll();
})();
