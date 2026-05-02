(function () {
  var cfg = window.__resultsConfig;
  if (!cfg) return;

  var tbody = document.getElementById('results-tbody');
  var sentinel = document.getElementById('scroll-sentinel');
  var label = document.getElementById('row-count-label');
  var emptyState = document.getElementById('empty-state');
  var table = document.getElementById('results-table');

  var CHUNK = 100;
  var offset = 0;
  var loading = false;
  var done = false;

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function buildUrl(off) {
    var p = Object.assign({}, cfg.params, { offset: off, limit: CHUNK });
    var qs = Object.keys(p)
      .filter(function (k) { return p[k] !== '' && p[k] !== null; })
      .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(p[k]); })
      .join('&');
    return cfg.rowsUrl + '?' + qs;
  }

  function appendRows(rows) {
    var fragment = document.createDocumentFragment();
    rows.forEach(function (row) {
      var tr = document.createElement('tr');
      var numTd = document.createElement('td');
      numTd.className = 'row-num';
      numTd.textContent = row.idx;
      tr.appendChild(numTd);

      row.cells.forEach(function (cell) {
        var td = document.createElement('td');
        if (cell.i) {
          td.className = 'cell-invalid';
          td.setAttribute('data-code', cell.c || '');
          td.setAttribute('title', (cell.c || '') + ': ' + (cell.m || ''));
        }
        td.textContent = cell.v;
        tr.appendChild(td);
      });

      fragment.appendChild(tr);
    });
    tbody.appendChild(fragment);
  }

  function updateLabel() {
    var loaded = tbody.rows.length;
    var total = cfg.total;
    if (total === 0) {
      label.textContent = '0 rows';
    } else if (done) {
      label.textContent = total + ' row' + (total !== 1 ? 's' : '');
    } else {
      label.textContent = loaded + ' of ' + total + ' rows loaded…';
    }
  }

  function loadChunk() {
    if (loading || done) return;
    loading = true;

    fetch(buildUrl(offset))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        appendRows(data.rows);
        offset += data.rows.length;

        if (offset >= data.total || data.rows.length === 0) {
          done = true;
          sentinel.style.display = 'none';
          if (data.total === 0) {
            table.style.display = 'none';
            emptyState.style.display = '';
          }
        }

        updateLabel();
        loading = false;
        // immediately try the next chunk without waiting for another scroll
        loadChunk();
      })
      .catch(function () {
        loading = false;
      });
  }

  // ── Fixed bottom scrollbar sync ──────────────────────────────────────────
  var tableScroll = document.getElementById('table-scroll');

  // Give the table-scroll a fixed height so:
  //   (a) overflow: auto creates a real scroll container
  //   (b) position: sticky on thead th works at top: 0
  function fitTableHeight() {
    var rect = tableScroll.getBoundingClientRect();
    var h = window.innerHeight - rect.top - 20;
    tableScroll.style.height = Math.max(h, 300) + 'px';
  }
  fitTableHeight();
  window.addEventListener('resize', fitTableHeight);

  var observer = new IntersectionObserver(function (entries) {
    if (entries[0].isIntersecting) loadChunk();
  }, { root: tableScroll, rootMargin: '1500px' });

  observer.observe(sentinel);
  var fixedBar = document.getElementById('fixed-scrollbar');
  var fixedInner = document.getElementById('fixed-scrollbar-inner');

  function syncScrollbarWidth() {
    fixedInner.style.width = tableScroll.scrollWidth + 'px';
  }

  // Keep phantom width in sync as rows load
  var origAppendRows = appendRows;
  appendRows = function(rows) {
    origAppendRows(rows);
    syncScrollbarWidth();
  };

  var syncingFromFixed = false;
  var syncingFromTable = false;

  fixedBar.addEventListener('scroll', function () {
    if (syncingFromTable) return;
    syncingFromFixed = true;
    tableScroll.scrollLeft = fixedBar.scrollLeft;
    syncingFromFixed = false;
  });

  tableScroll.addEventListener('scroll', function () {
    if (syncingFromFixed) return;
    syncingFromTable = true;
    fixedBar.scrollLeft = tableScroll.scrollLeft;
    syncingFromTable = false;
  });

  // Show/hide bar only when table is actually wider than viewport
  function updateBarVisibility() {
    var needsScroll = tableScroll.scrollWidth > tableScroll.clientWidth;
    fixedBar.style.display = needsScroll ? '' : 'none';
  }

  syncScrollbarWidth();
  updateBarVisibility();
  window.addEventListener('resize', function () {
    syncScrollbarWidth();
    updateBarVisibility();
  });

  // ── Highlight column header on invalid cell hover
  table.addEventListener('mouseover', function (e) {
    var cell = e.target.closest('td.cell-invalid');
    if (!cell) return;
    var colIdx = Array.from(cell.parentElement.children).indexOf(cell);
    Array.from(table.querySelectorAll('thead th')).forEach(function (h, i) {
      h.classList.toggle('col-hover', i === colIdx);
    });
  });
  table.addEventListener('mouseleave', function () {
    Array.from(table.querySelectorAll('thead th')).forEach(function (h) {
      h.classList.remove('col-hover');
    });
  });
})();
