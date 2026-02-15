
// Section collapse
function toggleSec(idx) {
    var chev = document.getElementById('chev' + idx);
    var body = document.getElementById('secBody' + idx);
    var header = body.previousElementSibling;
    var isExpanded = body.classList.contains('expanded');
    if (isExpanded) {
        body.classList.remove('expanded');
        chev.classList.remove('expanded');
        header.classList.remove('expanded');
    } else {
        body.classList.add('expanded');
        chev.classList.add('expanded');
        header.classList.add('expanded');
    }
}

// Drilldown toggling
function toggleDrilldown(id) {
    var allDDs = document.querySelectorAll('.drilldown-content');
    var allBtns = document.querySelectorAll('.drilldown-toggle');
    allDDs.forEach(function (el) { el.classList.remove('visible'); });
    allBtns.forEach(function (el) { el.classList.remove('active'); });
    document.getElementById('dd-' + id).classList.add('visible');
    event.target.classList.add('active');
}

// ── Heatmap Rendering ────────────────────────────────

function heatColor(value, max) {
    if (value === 0) return 'var(--bg-hover)';
    var ratio = value / max;
    if (ratio < 0.25) return 'rgba(194,65,12,0.15)';
    if (ratio < 0.5) return 'rgba(194,65,12,0.35)';
    if (ratio < 0.75) return 'rgba(194,65,12,0.6)';
    return 'rgba(194,65,12,0.9)';
}

function textColor(value, max) {
    var ratio = value / max;
    return ratio > 0.5 ? 'rgba(255,255,255,0.95)' : 'var(--text-muted)';
}

function renderHeatmap(containerId, tools, data) {
    var hours = [];
    for (var h = 0; h < 24; h++) hours.push(h);

    var maxVal = 0;
    data.forEach(function (row) {
        row.forEach(function (v) { if (v > maxVal) maxVal = v; });
    });

    var cols = 25; // label + 24 hours
    var html = '<div class="heatmap-grid" style="grid-template-columns: 100px repeat(24, 1fr);">';

    // Header row
    html += '<div class="heatmap-row-label"></div>';
    hours.forEach(function (h) {
        html += '<div class="heatmap-label">' + (h < 10 ? '0' : '') + h + '</div>';
    });

    // Data rows
    tools.forEach(function (tool, i) {
        html += '<div class="heatmap-row-label">' + tool + '</div>';
        hours.forEach(function (h) {
            var val = data[i][h];
            var bg = heatColor(val, maxVal);
            var fg = textColor(val, maxVal);
            html += '<div class="heatmap-cell" style="background:' + bg + ';color:' + fg + ';" title="' + tool + ' @ ' + (h < 10 ? '0' : '') + h + ':00 — ' + val + ' calls">' + (val > 0 ? val : '') + '</div>';
        });
    });

    html += '</div>';

    // Legend
    html += '<div style="display:flex;align-items:center;gap:8px;margin-top:10px;justify-content:flex-end;">';
    html += '<span class="text-xs muted">Less</span>';
    ['rgba(194,65,12,0.15)', 'rgba(194,65,12,0.35)', 'rgba(194,65,12,0.6)', 'rgba(194,65,12,0.9)'].forEach(function (c) {
        html += '<div style="width:14px;height:14px;border-radius:2px;background:' + c + ';"></div>';
    });
    html += '<span class="text-xs muted">More</span>';
    html += '</div>';

    document.getElementById(containerId).innerHTML = html;
}

// Tool heatmap data (dummy - 5 tools × 24 hours)
var toolHeatmapData = [
    [2, 1, 0, 0, 0, 1, 4, 12, 22, 28, 31, 29, 26, 24, 30, 27, 22, 18, 14, 10, 8, 5, 3, 2],   // brave_search
    [1, 0, 0, 0, 0, 0, 3, 10, 18, 22, 25, 24, 20, 19, 23, 21, 18, 15, 11, 8, 6, 4, 2, 1],   // pdf_reader
    [0, 0, 0, 0, 0, 0, 2, 8, 14, 16, 18, 17, 15, 14, 16, 15, 12, 10, 8, 5, 3, 2, 1, 0],     // text_splitter
    [1, 0, 0, 0, 0, 0, 1, 6, 11, 14, 15, 14, 13, 12, 14, 13, 10, 9, 7, 4, 3, 1, 1, 0],      // json_parser
    [0, 0, 0, 0, 0, 0, 1, 5, 9, 12, 14, 13, 11, 10, 13, 12, 9, 8, 6, 3, 2, 1, 0, 0],        // ast_parser
];

renderHeatmap('toolHeatmap', ['brave_search', 'pdf_reader', 'text_splitter', 'json_parser', 'ast_parser'], toolHeatmapData);

// Skill heatmap data (dummy - 6 skills × 24 hours)
var skillHeatmapData = [
    [1, 0, 0, 0, 0, 1, 3, 11, 20, 26, 30, 28, 25, 23, 28, 25, 20, 16, 12, 9, 7, 4, 2, 1],   // web_research
    [0, 0, 0, 0, 0, 0, 2, 8, 16, 22, 26, 25, 22, 20, 24, 22, 18, 14, 10, 7, 5, 3, 1, 0],    // code_review
    [0, 0, 0, 0, 0, 0, 1, 6, 12, 16, 20, 22, 18, 17, 19, 18, 15, 12, 9, 6, 4, 2, 1, 0],     // content_writing
    [1, 0, 0, 0, 0, 0, 2, 8, 14, 16, 15, 14, 12, 11, 13, 12, 9, 7, 5, 3, 2, 1, 0, 0],       // planning
    [0, 0, 0, 0, 0, 0, 1, 4, 8, 11, 14, 13, 12, 10, 13, 12, 10, 8, 6, 4, 2, 1, 0, 0],       // summarization
    [2, 1, 1, 2, 1, 2, 1, 2, 1, 1, 2, 1, 2, 1, 1, 2, 1, 1, 2, 1, 2, 1, 1, 2],               // health_monitoring
];

renderHeatmap('skillHeatmap', ['web_research', 'code_review', 'content_writing', 'planning', 'summarization', 'health_monitor'], skillHeatmapData);
