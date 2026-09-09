let trendChart;
let categoryChart;

function destroy(chart) {
  if (chart) chart.destroy();
  return null;
}

function toggleEmpty(id, visible) {
  const node = document.querySelector(id);
  if (node) node.hidden = !visible;
}

export function categorySummary(transactions, presentation = {}, mode = 'detail') {
  const grouped = {};
  transactions.forEach((item) => {
    const category = presentation[item.category_id] || presentation._fallback || { label: '未分类' };
    const label = mode === 'primary' ? category.primaryLabel : category.label;
    const color = mode === 'primary' ? category.rootColor : category.color;
    if (!grouped[label]) grouped[label] = { value: 0, color };
    grouped[label].value += -item.amount;
  });
  const labels = Object.keys(grouped);
  return {
    labels,
    values: labels.map((label) => grouped[label].value),
    colors: labels.map((label) => grouped[label].color),
  };
}

export function accountChartSeries(detail, presentation = {}) {
  const data = detail.chart_data || { daily_expenses: [], category_expenses: [] };
  return {
    trend: { labels: data.daily_expenses.map(item => item.date.slice(5, 10)), values: data.daily_expenses.map(item => item.amount) },
    categories: {
      labels: data.category_expenses.map(item => (presentation[item.category_id] || presentation._fallback || { label: '未分类' }).label),
      values: data.category_expenses.map(item => item.amount),
      colors: data.category_expenses.map(item => (presentation[item.category_id] || presentation._fallback || {}).color),
    },
  };
}

export function renderCharts(detail, presentation = {}) {
  trendChart = destroy(trendChart);
  categoryChart = destroy(categoryChart);
  const series = accountChartSeries(detail, presentation);
  const canChart = Boolean(window.Chart && series.trend.values.length);
  toggleEmpty('#trendEmpty', !canChart);
  toggleEmpty('#categoryEmpty', !canChart);
  if (!canChart) return;

  const trend = document.querySelector('#trendChart');
  const category = document.querySelector('#categoryChart');
  if (!trend || !category) return;
  const { labels, values } = series.trend;
  trendChart = new window.Chart(trend, {
    type: 'line', data: { labels, datasets: [{ label: '支出', data: values, borderColor: '#0071e3', backgroundColor: 'rgba(0,113,227,.12)', fill: true, tension: .35 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: true } } },
  });
  const summary = series.categories;
  categoryChart = new window.Chart(category, {
    type: 'doughnut', data: { labels: summary.labels, datasets: [{ data: summary.values, backgroundColor: summary.colors }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: true } } },
  });
}
