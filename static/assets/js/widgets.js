/**
 * ITL Dashboard Widgets - JavaScript
 * Interactive functionality for dashboard widgets
 */

// ============================================
// Resource Distribution Chart Widget
// ============================================
function initResourceDistribution(data) {
  const ctx = document.getElementById('resource-distribution-chart');
  if (!ctx) return;

  const defaultData = {
    labels: ['Tenants', 'Subscriptions', 'Resource Groups', 'Virtual Machines', 'Networks'],
    values: [3, 12, 45, 28, 15],
    colors: ['#ffc451', '#2ecc71', '#3498db', '#9b59b6', '#e74c3c']
  };

  const chartData = data && data.labels ? data : defaultData;

  // Create chart using Chart.js (if available)
  if (typeof Chart !== 'undefined') {
    new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: chartData.labels,
        datasets: [{
          data: chartData.values,
          backgroundColor: chartData.colors,
          borderWidth: 2,
          borderColor: '#fff'
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        cutout: '60%',
        plugins: {
          legend: {
            display: false
          },
          tooltip: {
            callbacks: {
              label: function(context) {
                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                const percentage = ((context.raw / total) * 100).toFixed(1);
                return `${context.label}: ${context.raw} (${percentage}%)`;
              }
            }
          }
        }
      }
    });
  }

  // Populate legend
  const legend = document.getElementById('distribution-legend');
  if (legend) {
    const total = chartData.values.reduce((a, b) => a + b, 0);
    legend.innerHTML = chartData.labels.map((label, i) => `
      <div class="legend-item">
        <span class="legend-color" style="background: ${chartData.colors[i]}"></span>
        <span class="legend-label">${label}</span>
        <span class="legend-value">${chartData.values[i]}</span>
      </div>
    `).join('');
  }
}

// ============================================
// Alerts Widget
// ============================================
function initAlertsWidget() {
  const widget = document.getElementById('alerts-widget');
  if (!widget) return;

  // Filter buttons
  const filterBtns = widget.querySelectorAll('.filter-btn');
  const alertItems = widget.querySelectorAll('.alert-item');
  const alertsEmpty = widget.querySelector('#alerts-empty');
  const alertsCount = widget.querySelector('#alerts-count');

  filterBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const filter = btn.dataset.filter;
      
      // Update active state
      filterBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      // Filter items
      let visibleCount = 0;
      alertItems.forEach(item => {
        if (filter === 'all' || item.dataset.severity === filter) {
          item.style.display = 'flex';
          visibleCount++;
        } else {
          item.style.display = 'none';
        }
      });

      // Show/hide empty state
      if (alertsEmpty) {
        alertsEmpty.style.display = visibleCount === 0 ? 'flex' : 'none';
      }
    });
  });

  // Dismiss buttons
  widget.querySelectorAll('.alert-dismiss').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      const alertItem = btn.closest('.alert-item');
      alertItem.style.transition = 'opacity 0.3s, transform 0.3s';
      alertItem.style.opacity = '0';
      alertItem.style.transform = 'translateX(20px)';
      
      setTimeout(() => {
        alertItem.remove();
        updateAlertCount();
      }, 300);
    });
  });

  function updateAlertCount() {
    const remaining = widget.querySelectorAll('.alert-item');
    if (alertsCount) {
      alertsCount.textContent = remaining.length;
    }
    if (remaining.length === 0 && alertsEmpty) {
      alertsEmpty.style.display = 'flex';
    }
  }
}

// ============================================
// Deployment Status Widget
// ============================================
function initDeploymentWidget() {
  const widget = document.getElementById('deployment-status-widget');
  if (!widget) return;

  // Animate progress rings on load
  widget.querySelectorAll('.deploy-progress-ring').forEach(ring => {
    const progress = ring.dataset.progress;
    const circle = ring.querySelector('.circle-progress');
    if (circle) {
      // Initial state
      circle.style.strokeDasharray = '0, 100';
      
      // Animate after a short delay
      setTimeout(() => {
        circle.style.strokeDasharray = `${progress}, 100`;
      }, 100);
    }
  });

  // Simulate progress updates for running deployments (demo)
  const runningDeployments = widget.querySelectorAll('.deploy-running');
  runningDeployments.forEach(deploy => {
    const ring = deploy.querySelector('.deploy-progress-ring');
    const progressText = ring.querySelector('.progress-text');
    const circle = ring.querySelector('.circle-progress');
    let progress = parseInt(ring.dataset.progress) || 0;

    // Only simulate for demo
    if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
      const interval = setInterval(() => {
        if (progress >= 100) {
          clearInterval(interval);
          deploy.classList.remove('deploy-running');
          deploy.classList.add('deploy-succeeded');
          progressText.innerHTML = '<i class="bi bi-check"></i>';
          return;
        }
        progress += Math.floor(Math.random() * 3) + 1;
        progress = Math.min(progress, 100);
        ring.dataset.progress = progress;
        progressText.textContent = `${progress}%`;
        circle.style.strokeDasharray = `${progress}, 100`;
      }, 2000);
    }
  });
}

// ============================================
// Resource Health Widget
// ============================================
function initHealthWidget() {
  const widget = document.getElementById('resource-health-widget');
  if (!widget) return;

  // Animate health ring
  const healthRing = widget.querySelector('.health-progress');
  if (healthRing) {
    const dashArray = healthRing.getAttribute('stroke-dasharray');
    healthRing.setAttribute('stroke-dasharray', '0 283');
    
    setTimeout(() => {
      healthRing.style.transition = 'stroke-dasharray 1s ease-out';
      healthRing.setAttribute('stroke-dasharray', dashArray);
    }, 100);
  }
}

// ============================================
// Usage Metrics Widget
// ============================================
function initMetricsWidget() {
  const widget = document.getElementById('usage-metrics-widget');
  if (!widget) return;

  // Animate bar fills
  widget.querySelectorAll('.bar-fill').forEach(bar => {
    const width = bar.style.width;
    bar.style.width = '0';
    
    setTimeout(() => {
      bar.style.width = width;
    }, 100);
  });

  // Animate cost counter (if anime.js or similar is available)
  const costAmount = widget.querySelector('#cost-amount');
  if (costAmount) {
    const finalValue = costAmount.textContent.replace(/[^0-9]/g, '');
    if (finalValue && typeof anime !== 'undefined') {
      const obj = { value: 0 };
      anime({
        targets: obj,
        value: parseInt(finalValue),
        round: 1,
        easing: 'easeOutExpo',
        duration: 2000,
        update: function() {
          costAmount.textContent = obj.value.toLocaleString('nl-NL');
        }
      });
    }
  }
}

// ============================================
// Provider Registry Widget
// ============================================
function initProviderWidget() {
  const widget = document.getElementById('provider-registry-widget');
  if (!widget) return;

  // Add click handlers for provider items
  widget.querySelectorAll('.provider-item').forEach(item => {
    item.style.cursor = 'pointer';
    item.addEventListener('click', () => {
      const namespace = item.querySelector('.provider-namespace')?.textContent;
      if (namespace) {
        window.location.href = `/providers/${namespace.replace('ITL.', '').toLowerCase()}`;
      }
    });
  });
}

// ============================================
// Initialize all widgets on DOM ready
// ============================================
document.addEventListener('DOMContentLoaded', function() {
  initAlertsWidget();
  initDeploymentWidget();
  initHealthWidget();
  initMetricsWidget();
  initProviderWidget();
  
  // Resource distribution is initialized inline in the template
  // to receive server-side data
});

// ============================================
// Auto-refresh functionality (optional)
// ============================================
function enableWidgetAutoRefresh(widgetId, intervalMs, refreshFn) {
  if (!intervalMs || intervalMs < 5000) return;
  
  setInterval(() => {
    const widget = document.getElementById(widgetId);
    if (widget && document.visibilityState === 'visible') {
      refreshFn(widget);
    }
  }, intervalMs);
}

// Export for external use
window.ITLWidgets = {
  initResourceDistribution,
  initAlertsWidget,
  initDeploymentWidget,
  initHealthWidget,
  initMetricsWidget,
  initProviderWidget,
  enableWidgetAutoRefresh
};
