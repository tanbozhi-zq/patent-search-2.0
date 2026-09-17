'use strict';

// 管理看板前端只做展示和有界查询：后端决定 PromQL、权限、筛选白名单和游标。
// 这里把响应拆成几种视图，不把 Prometheus 原始 JSON 或日志 message 拼进 HTML。
const API = '/admin-api/v1';
// CORE_CONFIG_KEYS 是看板始终突出显示的核心运行参数，完整配置仍由后端决定。
const CORE_CONFIG_KEYS = Object.freeze([
    'opensearch.pool_maxsize',
    'opensearch.timeout_seconds',
    'opensearch.max_retries',
    'bulkhead.global_capacity',
    'bulkhead.heavy_capacity',
    'bulkhead.acquire_timeout_seconds',
    'request.deadline_seconds',
    'readiness.timeout_seconds',
]);

const state = {
    configSchema: null,
    draftsEnabled: false,
    runtimeConfigEnabled: false,
    runtimeConfig: null,
    metrics: null,
    logCursor: null,
    logQuery: null,
    // logRevision 用来丢弃过期请求的返回值；用户修改筛选条件后，旧请求不能覆盖新状态。
    logRevision: 0,
    logScope: 'unavailable',
    routes: new Set(),
};

const byId = (id) => document.getElementById(id);

async function fetchJson(path, options = {}) {
    // same-origin 让浏览器沿用 viewer Basic 会话，no-store 避免管理响应被缓存。
    const { headers = {}, ...requestOptions } = options;
    const response = await fetch(path, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json', ...headers },
        cache: 'no-store',
        ...requestOptions,
    });
    if (!response.ok) {
        const body = await response.json().catch(() => null);
        const message = body && body.message ? body.message : `请求失败（HTTP ${response.status}）`;
        throw new Error(message);
    }
    return response.json();
}

function setText(node, value) {
    // 所有文本通过 textContent 写入，避免运行态、route 或日志字段成为 HTML。
    node.textContent = value == null || value === '' ? '—' : String(value);
}

function element(tag, className, text) {
    // 小型 DOM 工厂统一使用 textContent；看板不需要 innerHTML 模板拼接。
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
}

function tableCell(value, { number = false } = {}) {
    // 表格单元格也走统一的文本写入路径，并把数值列的 CSS 标记集中处理。
    const text = value == null || value === '' ? '—' : String(value);
    const classes = [];
    if (number) classes.push('number');
    if (text === '0' || text === '0%' || text === '0.000/s') classes.push('zero');
    return element('td', classes.join(' '), text);
}

function emptyTableRow(message, columnCount) {
    // 空态和错误态使用普通文本渲染，避免把后端 message 当作 HTML 解释。
    const row = document.createElement('tr');
    const cell = element('td', 'empty-cell', message);
    cell.colSpan = columnCount;
    row.append(cell);
    return row;
}

function formatTime(value) {
    // Prometheus 时间戳和 API ISO 时间都可能缺失或非法，展示层降级为破折号。
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false });
}

function formatNumber(value, digits = 2) {
    // 只有有限数字才格式化，Prometheus partial/NaN 不在页面上伪装成 0。
    return Number.isFinite(value)
        ? value.toLocaleString('zh-CN', { maximumFractionDigits: digits })
        : '—';
}

function formatRate(value) {
    return Number.isFinite(value) ? `${formatNumber(value, 3)}/s` : '—';
}

function formatRatio(value) {
    return Number.isFinite(value) ? `${formatNumber(value * 100, 2)}%` : '—';
}

function metric(key) {
    // 后端固定返回 results key；找不到或整批未加载时统一返回 null。
    if (!state.metrics) return null;
    return state.metrics.results.find((item) => item.key === key) || null;
}

function firstValue(key) {
    // 信号 rail 只需要聚合结果的第一个样本，逐实例数据由专门视图处理。
    const item = metric(key);
    return item && item.available && item.samples.length ? item.samples[0].value : null;
}

function metricEmptyLabel(keys) {
    // 所有指标都可用时，空表表示确实没有 series；否则要明确显示 available=false。
    const items = keys.map(metric).filter(Boolean);
    return items.length && items.every((item) => item.available) ? 'series=0' : 'available=false';
}

function renderStatus(status) {
    // status 负责发布身份、日志能力和 notices；指标身份与逐实例数据由相邻视图负责。
    const release = status.release;
    setText(byId('releaseVersion'), release.service_version);
    setText(byId('releaseCommit'), release.commit);
    setText(byId('releaseTag'), release.tag);
    setText(byId('releaseInstance'), release.instance_id);
    setText(byId('releaseStarted'), formatTime(release.started_at));
    setText(byId('metricsIdentity'), `${status.metrics_source} / —`);
    setText(byId('logScopeIdentity'), status.log_scope);
    state.logScope = status.log_scope;
    setText(byId('logScope'), `scope=${status.log_scope} · limit=50`);
    state.draftsEnabled = Boolean(status.config_drafts_enabled);
    state.runtimeConfigEnabled = Boolean(status.runtime_config_enabled);
    byId('draftWorkspace').hidden = !state.draftsEnabled;
    setText(
        byId('adminModeBadge'),
        `${state.runtimeConfigEnabled ? '运行时' : state.draftsEnabled ? '草案' : '只读'} · ${String(status.role || 'admin').toUpperCase()}`,
    );
    state.routes = new Set(status.log_routes || []);
    renderRoutes();
}

function renderMetricsIdentity(metrics) {
    // metrics identity 单独展示 Prometheus 部分可用状态，不把 partial 结果当作完整快照。
    setText(byId('metricsGeneratedAt'), formatTime(metrics.generated_at));
    setText(byId('metricsIdentity'), `${metrics.source} / ${String(metrics.partial)}`);
}

function summarizeOutcomeRates(samples) {
    // 将固定 status 标签聚合为客户端错误、服务端错误和总量，供信号 rail 使用。
    const summary = { total: 0, clientError: 0, serverError: 0 };
    samples.forEach((sample) => {
        const value = Number.isFinite(sample.value) ? sample.value : 0;
        const status = String(sample.labels.status || '');
        summary.total += value;
        if (status.startsWith('4')) summary.clientError += value;
        if (status.startsWith('5')) summary.serverError += value;
    });
    return summary;
}

function renderSignals() {
    // 秒、比例等单位转换只发生在展示层，后端和 Prometheus 保持原始契约。
    const outcome = metric('http_outcome_rate');
    const outcomeSummary = outcome && outcome.available
        ? summarizeOutcomeRates(outcome.samples)
        : null;
    const globalInFlight = sampleMap('bulkhead_in_flight_total', 'bulkhead').get('global');
    const values = {
        request_rate: firstValue('request_rate'),
        success_rate: firstValue('success_rate'),
        latency_p50_seconds: firstValue('latency_p50_seconds'),
        latency_p95_seconds: firstValue('latency_p95_seconds'),
        latency_p99_seconds: firstValue('latency_p99_seconds'),
        http_4xx_rate: outcomeSummary ? outcomeSummary.clientError : null,
        http_5xx_rate: outcomeSummary ? outcomeSummary.serverError : null,
        global_in_flight: globalInFlight == null ? null : globalInFlight,
    };
    const formats = {
        request_rate: (value) => formatNumber(value, 3),
        success_rate: formatRatio,
        latency_p50_seconds: (value) => formatNumber(value * 1000, 0),
        latency_p95_seconds: (value) => formatNumber(value * 1000, 0),
        latency_p99_seconds: (value) => formatNumber(value * 1000, 0),
        http_4xx_rate: (value) => formatNumber(value, 3),
        http_5xx_rate: (value) => formatNumber(value, 3),
        global_in_flight: (value) => formatNumber(value, 0),
    };
    document.querySelectorAll('[data-signal]').forEach((node) => {
        const key = node.dataset.signal;
        const value = values[key];
        setText(node.querySelector('strong'), value == null ? '—' : formats[key](value));
    });
}

function instanceModels(buildSamples, startSamples, probeSamples) {
    // build/start/probe 是三组独立查询，按 scrape instance label 在浏览器端外连接成一行；
    // 缺某一组时仍保留实例，避免“没有 probe”被误读为“实例不存在”。
    const instances = new Map();
    const model = (sample) => {
        const name = sample.labels.instance || 'unknown';
        if (!instances.has(name)) {
            instances.set(name, { name, build: null, start: null, probes: new Map() });
        }
        return instances.get(name);
    };
    buildSamples.forEach((sample) => { model(sample).build = sample; });
    startSamples.forEach((sample) => { model(sample).start = sample; });
    probeSamples.forEach((sample) => {
        const probe = sample.labels.probe;
        if (probe) model(sample).probes.set(probe, sample.value);
    });
    return [...instances.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function renderInstances() {
    // 逐实例表保留 instance、版本、启动时间和三类探针，不能用 job 聚合值替代。
    const builds = metric('build_info');
    const starts = metric('service_start_time_seconds');
    const probes = metric('probe_by_instance');
    const models = instanceModels(
        builds && builds.available ? builds.samples : [],
        starts && starts.available ? starts.samples : [],
        probes && probes.available ? probes.samples : [],
    );
    const rows = models.map((instance) => {
        const row = document.createElement('tr');
        const labels = instance.build ? instance.build.labels : {};
        row.append(
            tableCell(instance.name),
            tableCell(labels.version || '—'),
            tableCell(labels.commit || '—'),
            tableCell(instance.start ? formatTime(instance.start.value * 1000) : '—'),
            tableCell(instance.probes.get('live'), { number: true }),
            tableCell(instance.probes.get('startup'), { number: true }),
            tableCell(instance.probes.get('ready'), { number: true }),
        );
        return row;
    });
    byId('instanceRows').replaceChildren(...(rows.length ? rows : [
        emptyTableRow(metricEmptyLabel(['build_info', 'service_start_time_seconds', 'probe_by_instance']), 7),
    ]));
}

function sampleMap(key, label, combine = (_current, next) => next) {
    // 把一组 Prometheus samples 聚合为 label→value；调用方显式选择覆盖或 max 语义。
    const item = metric(key);
    const map = new Map();
    if (item && item.available) item.samples.forEach((sample) => {
        const name = sample.labels[label] || 'unknown';
        map.set(name, map.has(name) ? combine(map.get(name), sample.value) : sample.value);
    });
    return map;
}

function renderBulkheads() {
    // 容量、在途和拒绝数按 bulkhead 汇总；利用率使用后端按实例计算的最差值，避免
    // 把多实例容量相加后再除而隐藏某个实例的过载。
    const capacity = sampleMap('bulkhead_capacity_total', 'bulkhead');
    const inFlight = sampleMap('bulkhead_in_flight_total', 'bulkhead');
    const utilization = sampleMap('bulkhead_worst_utilization', 'bulkhead', Math.max);
    const rejected = sampleMap('bulkhead_rejections', 'bulkhead');
    const names = [...new Set([
        ...capacity.keys(),
        ...inFlight.keys(),
        ...utilization.keys(),
        ...rejected.keys(),
    ])].sort();
    const rows = names.map((name) => {
        const row = document.createElement('tr');
        row.append(
            tableCell(name),
            tableCell(formatNumber(inFlight.get(name), 0), { number: true }),
            tableCell(formatNumber(capacity.get(name), 0), { number: true }),
            tableCell(utilization.has(name) ? formatRatio(utilization.get(name)) : '—', { number: true }),
            tableCell(formatNumber(rejected.get(name), 0), { number: true }),
        );
        return row;
    });
    byId('bulkheadRows').replaceChildren(...(rows.length ? rows : [
        emptyTableRow(metricEmptyLabel([
            'bulkhead_capacity_total',
            'bulkhead_in_flight_total',
            'bulkhead_worst_utilization',
            'bulkhead_rejections',
        ]), 5),
    ]));
}

function groupDependencyCallRates(result) {
    // dependency samples 按 operation 合并 success/failed，供表格同时展示调用量和失败率。
    const grouped = new Map();
    if (result && result.available) result.samples.forEach((sample) => {
        const operation = sample.labels.operation || 'unknown';
        const current = grouped.get(operation) || { total: 0, failed: 0 };
        current.total += sample.value;
        if (sample.labels.outcome !== 'success') current.failed += sample.value;
        grouped.set(operation, current);
    });
    return grouped;
}

function renderDependencies() {
    // OpenSearch 调用按 operation 汇总成功/失败速率，同时显示 P95 和显式重试速率。
    const calls = metric('opensearch_call_rate');
    const latency = sampleMap('opensearch_latency_p95_seconds', 'operation');
    const retries = sampleMap('opensearch_retry_rate', 'operation');
    const grouped = groupDependencyCallRates(calls);
    const names = [...new Set([...grouped.keys(), ...latency.keys(), ...retries.keys()])].sort();
    const rows = names.map((operation) => {
        const values = grouped.get(operation);
        const row = document.createElement('tr');
        row.append(
            tableCell(operation),
            tableCell(values ? formatRate(values.total) : '—', { number: true }),
            tableCell(values ? formatRate(values.failed) : '—', { number: true }),
            tableCell(values && values.total > 0 ? formatRatio(values.failed / values.total) : '—', { number: true }),
            tableCell(latency.has(operation) ? `${formatNumber(latency.get(operation) * 1000, 0)} ms` : '—', { number: true }),
            tableCell(formatRate(retries.get(operation)), { number: true }),
        );
        return row;
    });
    byId('dependencyRows').replaceChildren(...(rows.length ? rows : [
        emptyTableRow(metricEmptyLabel([
            'opensearch_call_rate',
            'opensearch_latency_p95_seconds',
            'opensearch_retry_rate',
        ]), 6),
    ]));
}

function renderRouteRates() {
    // route 统计保持后端低基数标签口径，展示层只计算总量占比，不重新解释原始样本。
    const result = metric('route_request_rate');
    const samples = result && result.available ? result.samples.slice() : [];
    const total = samples.reduce((sum, sample) => sum + sample.value, 0);
    const rows = samples
        .sort((a, b) => b.value - a.value || String(a.labels.route).localeCompare(String(b.labels.route)))
        .map((sample) => {
            const row = document.createElement('tr');
            row.append(
                tableCell(sample.labels.route || 'unknown'),
                tableCell(formatRate(sample.value), { number: true }),
                tableCell(total > 0 ? formatRatio(sample.value / total) : '—', { number: true }),
            );
            return row;
        });
    byId('routeRows').replaceChildren(...(rows.length ? rows : [
        emptyTableRow(result && result.available ? 'series=0' : 'available=false', 3),
    ]));
}

function renderOutcomes() {
    // HTTP status/code 是后端固定低基数标签；比例只用于展示，不改变采集数据口径。
    const result = metric('http_outcome_rate');
    const samples = result && result.available ? result.samples.slice() : [];
    const total = summarizeOutcomeRates(samples).total;
    const rows = samples
        .sort((a, b) => `${a.labels.status}:${a.labels.code}`.localeCompare(`${b.labels.status}:${b.labels.code}`))
        .map((sample) => {
            const row = document.createElement('tr');
            row.append(
                tableCell(sample.labels.status || 'other'),
                tableCell(sample.labels.code || 'other'),
                tableCell(formatRate(sample.value), { number: true }),
                tableCell(total > 0 ? formatRatio(sample.value / total) : '—', { number: true }),
            );
            return row;
        });
    byId('outcomeRows').replaceChildren(...(rows.length ? rows : [
        emptyTableRow(result && result.available ? 'series=0' : 'available=false', 4),
    ]));
}

function coreConfigItems(items) {
    // 核心配置按固定顺序显示；缺失项保留占位，避免看板布局随服务端返回顺序漂移。
    const byKey = new Map(items.map((item) => [item.key, item]));
    return CORE_CONFIG_KEYS.map((key) => byKey.get(key) || {
        key,
        label: key,
        category: 'runtime',
        value: null,
    });
}

function configValue(item) {
    // secret 类配置只显示 configured 状态，普通 runtime/limit 配置显示已生效值。
    if (item.category === 'secret') return `configured=${String(Boolean(item.configured))}`;
    return item.value == null || item.value === '' ? '—' : String(item.value);
}

function renderConfig(config) {
    // 核心配置和完整配置表共用同一份后端数据，前者突出关键容量参数，后者保留审计视图。
    const coreRows = coreConfigItems(config.items).map((item) => {
        const row = element('div', 'core-config-item');
        row.append(
            element('span', null, item.label),
            element('strong', null, configValue(item)),
            element('code', null, item.key),
        );
        return row;
    });
    const allRows = config.items.map((item) => {
        const row = document.createElement('tr');
        row.append(
            tableCell(item.key),
            tableCell(item.label),
            tableCell(item.category),
            tableCell(configValue(item)),
        );
        return row;
    });
    byId('coreConfigRows').replaceChildren(...coreRows);
    byId('configRows').replaceChildren(...(allRows.length ? allRows : [emptyTableRow('count=0', 4)]));
    setText(byId('configCount'), `count=${config.items.length}`);
}

function draftCandidateValues(entries) {
    const candidates = {};
    entries.filter((entry) => entry.selected).forEach((entry) => {
        const raw = String(entry.value).trim();
        const value = Number(raw);
        if (!raw || !Number.isFinite(value)) {
            throw new Error(`${entry.key} 需要有限数值`);
        }
        if (entry.valueType === 'integer' && !Number.isInteger(value)) {
            throw new Error(`${entry.key} 需要整数`);
        }
        candidates[entry.key] = value;
    });
    return candidates;
}

function draftDiffSummary(diff) {
    const entries = Object.entries(diff || {});
    if (!entries.length) return 'diff=0';
    return entries
        .map(([key, value]) => `${key}: ${value.old} → ${value.new}`)
        .join(' · ');
}

function draftStatusText(status) {
    return {
        validated: 'validated · 已通过预检，尚未生效',
        invalid: 'invalid · 预检未通过',
        expired: 'expired · 基线已变化或 24 小时 TTL 已到期',
    }[status] || String(status || 'unknown');
}

function runtimeDraftCanApply(draft, runtimeConfig = state.runtimeConfig) {
    const entries = Object.values(draft.diff || {});
    return Boolean(
        runtimeConfig
        && runtimeConfig.writes_enabled
        && draft.status === 'validated'
        && draft.baseline_fingerprint === runtimeConfig.version
        && entries.length
        && entries.every((item) => item.apply_mode === 'runtime_reload'),
    );
}

function runtimeIdempotencyKey(prefix) {
    if (!window.crypto || typeof window.crypto.randomUUID !== 'function') {
        throw new Error('浏览器不支持安全的幂等键生成，无法执行运行时变更');
    }
    return `${prefix}-${window.crypto.randomUUID()}`;
}

function runtimeOperationSummary(operation) {
    const status = String(operation.status || 'unknown');
    const failure = operation.failure_code ? ` · ${operation.failure_code}` : '';
    const shortVersion = (value) => value ? String(value).slice(0, 12) : '—';
    return {
        result: `${status}${failure}`,
        operation: `operation=${operation.operation_id || 'unknown'}`,
        versions: `old=${shortVersion(operation.previous_version)} · new=${shortVersion(operation.current_version)}`,
    };
}

function renderRuntimeOperations(runtimeConfig) {
    const section = byId('runtimeAudit');
    if (!runtimeConfig) {
        section.hidden = true;
        byId('runtimeOperationRows').replaceChildren(emptyTableRow('runtime_audit=unavailable', 4));
        return;
    }
    section.hidden = false;
    const rows = (runtimeConfig.recent_operations || []).map((operation) => {
        const summary = runtimeOperationSummary(operation);
        const row = document.createElement('tr');
        const resultCell = document.createElement('td');
        resultCell.append(
            element('strong', '', summary.result),
            element('code', '', formatTime(operation.created_at)),
        );
        const identityCell = document.createElement('td');
        identityCell.append(
            document.createTextNode(String(operation.actor || 'unknown')),
            element('code', '', summary.operation),
        );
        row.append(
            resultCell,
            identityCell,
            tableCell(String(operation.reason || '—')),
            tableCell(summary.versions),
        );
        return row;
    });
    byId('runtimeOperationRows').replaceChildren(...(
        rows.length ? rows : [emptyTableRow('runtime_operations=0', 4)]
    ));
}

function renderRuntimeConfig(runtimeConfig) {
    state.runtimeConfig = runtimeConfig;
    renderRuntimeOperations(runtimeConfig);
    const enabled = Boolean(runtimeConfig && runtimeConfig.writes_enabled);
    byId('runtimeControl').hidden = !enabled;
    if (enabled) {
        setText(
            byId('draftBoundaryTitle'),
            '运行时热应用已启用：仅 runtime_reload 草案可在当前单实例内应用。',
        );
        setText(
            byId('draftBoundaryDetail'),
            '完整快照会原子替换并回读；restart_required 参数仍只允许正常发布。',
        );
        setText(
            byId('runtimeVersion'),
            `version=${runtimeConfig.version.slice(0, 12)} · source=${runtimeConfig.source}`,
        );
        setText(
            byId('runtimeRollbackTarget'),
            runtimeConfig.rollback_version
                ? `rollback=${runtimeConfig.rollback_version.slice(0, 12)}`
                : 'rollback=unavailable',
        );
        byId('runtimeRollbackButton').disabled = !runtimeConfig.rollback_version;
        return;
    }
    if (state.runtimeConfigEnabled) {
        setText(byId('draftBoundaryTitle'), '运行时状态暂不可用；禁止应用和回滚。');
        setText(
            byId('draftBoundaryDetail'),
            '请先刷新运行时快照；无法确认实际版本时不会显示任何写操作。',
        );
        return;
    }
    setText(byId('draftBoundaryTitle'), '这里只预检并保存草案，不会修改当前运行参数。');
        setText(
            byId('draftBoundaryDetail'),
            '运行时应用开关默认关闭；restart_required 参数始终需要按发布流程生效。',
        );
}

function renderDraftSchema(schema) {
    state.configSchema = schema;
    setText(
        byId('draftBaseline'),
        `registry=${schema.registry_version} · baseline=${schema.baseline_fingerprint.slice(0, 12)} · ttl=${schema.draft_ttl_seconds}s`,
    );
    const rows = schema.items.map((item, index) => {
        const row = element('div', 'draft-parameter-row');
        const toggle = document.createElement('input');
        toggle.type = 'checkbox';
        toggle.className = 'draft-parameter-toggle';
        toggle.id = `draftParameterToggle${index}`;
        toggle.dataset.key = item.key;
        toggle.setAttribute('aria-label', `修改 ${item.label}`);

        const identity = element('label', 'draft-parameter-identity');
        identity.htmlFor = toggle.id;
        identity.append(
            element('strong', '', item.label),
            element('code', '', item.key),
            element('small', '', `${item.apply_mode} · ${item.risk}`),
        );

        const current = element('div', 'draft-current');
        current.append(
            element('span', '', '当前 / 回滚'),
            element('strong', '', `${item.current_value} ${item.unit}`),
        );

        const field = element('label', 'draft-value-field');
        const input = document.createElement('input');
        input.type = 'number';
        input.step = item.value_type === 'integer' ? '1' : 'any';
        input.min = String(item.minimum);
        input.max = String(item.maximum);
        input.value = String(item.current_value);
        input.disabled = true;
        input.dataset.key = item.key;
        input.dataset.valueType = item.value_type;
        field.append(
            element('span', '', `候选 · ${item.minimum}–${item.maximum} ${item.unit}`),
            input,
        );
        toggle.addEventListener('change', () => {
            input.disabled = !toggle.checked;
            if (toggle.checked) input.focus();
            updateDraftSelectionCount();
        });
        row.append(toggle, identity, current, field);
        return row;
    });
    byId('draftParameterRows').replaceChildren(...(
        rows.length ? rows : [element('p', 'empty-state', 'registry_items=0')]
    ));
    updateDraftSelectionCount();
}

function selectedDraftEntries() {
    return [...byId('draftParameterRows').querySelectorAll('.draft-parameter-row')]
        .map((row) => {
            const toggle = row.querySelector('.draft-parameter-toggle');
            const input = row.querySelector('.draft-value-field input');
            return {
                selected: Boolean(toggle && toggle.checked),
                key: input ? input.dataset.key : '',
                value: input ? input.value : '',
                valueType: input ? input.dataset.valueType : '',
            };
        });
}

function updateDraftSelectionCount() {
    const selected = selectedDraftEntries().filter((entry) => entry.selected).length;
    setText(byId('draftSelectionCount'), `selected=${selected}`);
}

function renderDraftHistory(response) {
    const rows = response.items.map((draft) => {
        const row = document.createElement('tr');
        const statusCell = document.createElement('td');
        statusCell.append(element('span', `draft-status ${draft.status}`, draft.status));

        const identityCell = document.createElement('td');
        identityCell.append(
            document.createTextNode(formatTime(draft.created_at)),
            element('code', '', `expires=${formatTime(draft.expires_at)}`),
            element('code', '', draft.id),
        );
        const exportCell = document.createElement('td');
        const exportLink = element('a', 'draft-export', '导出 JSON');
        exportLink.href = `${API}/config-drafts/export?id=${encodeURIComponent(draft.id)}`;
        exportLink.download = `config-draft-${draft.id}.json`;
        exportCell.append(exportLink);
        const actionCell = document.createElement('td');
        if (runtimeDraftCanApply(draft)) {
            const applyButton = element('button', 'runtime-action', '应用运行时草案');
            applyButton.type = 'button';
            applyButton.dataset.runtimeApply = draft.id;
            applyButton.setAttribute('aria-label', `应用运行时草案 ${draft.id}`);
            actionCell.append(applyButton);
        } else if (draft.status === 'validated' && Object.values(draft.diff || {}).some(
            (item) => item.apply_mode !== 'runtime_reload',
        )) {
            actionCell.append(element('span', 'restart-required', '需要发布'));
        } else {
            actionCell.append(element('span', 'muted-action', '—'));
        }
        row.append(
            statusCell,
            identityCell,
            tableCell(draftDiffSummary(draft.diff)),
            tableCell(draft.reason),
            exportCell,
            actionCell,
        );
        return row;
    });
    byId('draftHistoryRows').replaceChildren(...(
        rows.length ? rows : [emptyTableRow('drafts=0', 6)]
    ));
}

function renderDraftResult(draft) {
    const node = byId('draftResult');
    node.className = `draft-result ${draft.status}`;
    const details = draft.status === 'validated'
        ? draftDiffSummary(draft.diff)
        : draft.validation_errors.map((error) => (
            [error.key, error.message].filter(Boolean).join(' · ')
        )).join('；') || '预检未通过，请检查候选值。';
    node.replaceChildren(
        element('strong', '', draftStatusText(draft.status)),
        element('span', '', `draft=${draft.id}`),
        element('span', '', details),
    );
}

function renderRuntimeActionResult(action, version) {
    const node = byId('draftResult');
    node.className = 'draft-result validated';
    node.replaceChildren(
        element('strong', '', action === 'rollback' ? '运行时回滚已回读' : '运行时草案已应用并回读'),
        element('span', '', `version=${version}`),
        element('span', '', '当前实例已切换完整运行时快照；刷新后可继续观察指标和日志。'),
    );
}

function renderDraftError(message) {
    const node = byId('draftResult');
    node.className = 'draft-result error';
    node.replaceChildren(
        element('strong', '', '草案操作失败'),
        element('span', '', `${message}。请刷新草案基线后重试。`),
    );
}

function resetDraftForm() {
    byId('draftReason').value = '';
    byId('draftParameterRows').querySelectorAll('.draft-parameter-row').forEach((row) => {
        const toggle = row.querySelector('.draft-parameter-toggle');
        const input = row.querySelector('.draft-value-field input');
        if (toggle) toggle.checked = false;
        if (input) {
            input.disabled = true;
            const item = state.configSchema && state.configSchema.items.find(
                (candidate) => candidate.key === input.dataset.key,
            );
            if (item) input.value = String(item.current_value);
        }
    });
    updateDraftSelectionCount();
}

async function loadDraftHistory() {
    const response = await fetchJson(`${API}/config-drafts?limit=20`);
    renderDraftHistory(response);
    return response;
}

async function draftHistoryRefreshState(draftId, loader = loadDraftHistory) {
    try {
        await loader();
        return `saved=${draftId}`;
    } catch (_error) {
        return `saved=${draftId} · history_refresh_failed=true`;
    }
}

async function loadDraftWorkspace() {
    if (!state.draftsEnabled) return;
    setText(byId('draftSubmitState'), 'loading=true');
    const requests = [
        fetchJson(`${API}/config-schema`),
        fetchJson(`${API}/config-drafts?limit=20`),
    ];
    requests.push(fetchJson(`${API}/runtime-config`));
    const [schemaResult, historyResult, runtimeResult] = await Promise.allSettled(requests);
    if (schemaResult.status === 'fulfilled') {
        renderDraftSchema(schemaResult.value);
    } else {
        setText(byId('draftBaseline'), `error=${schemaResult.reason.message}`);
    }
    if (historyResult.status === 'fulfilled') {
        renderDraftHistory(historyResult.value);
    } else {
        byId('draftHistoryRows').replaceChildren(emptyTableRow('history_unavailable=true', 6));
    }
    if (runtimeResult && runtimeResult.status === 'fulfilled') {
        renderRuntimeConfig(runtimeResult.value);
        if (historyResult.status === 'fulfilled') renderDraftHistory(historyResult.value);
    } else {
        renderRuntimeConfig(null);
    }
    const failures = [schemaResult, historyResult, runtimeResult].filter(
        (result) => result && result.status === 'rejected',
    );
    setText(byId('draftSubmitState'), failures.length ? `partial=true · failures=${failures.length}` : 'ready=true');
}

async function submitConfigDraft(event) {
    event.preventDefault();
    if (!state.configSchema) {
        renderDraftError('参数注册表尚未加载');
        return;
    }
    const form = byId('configDraftForm');
    if (!form.reportValidity()) return;
    let candidateValues;
    try {
        candidateValues = draftCandidateValues(selectedDraftEntries());
    } catch (error) {
        renderDraftError(error.message);
        return;
    }
    if (!Object.keys(candidateValues).length) {
        renderDraftError('请至少勾选一个候选参数');
        return;
    }

    const button = byId('createDraftButton');
    button.disabled = true;
    setText(byId('draftSubmitState'), 'submitting=true');
    try {
        const draft = await fetchJson(`${API}/config-drafts`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Admin-Intent': 'create-config-draft',
            },
            body: JSON.stringify({
                baseline_fingerprint: state.configSchema.baseline_fingerprint,
                reason: byId('draftReason').value.trim(),
                candidate_values: candidateValues,
            }),
        });
        renderDraftResult(draft);
        resetDraftForm();
        setText(byId('draftSubmitState'), `saved=${draft.id}`);
        setText(
            byId('draftSubmitState'),
            await draftHistoryRefreshState(draft.id),
        );
    } catch (error) {
        renderDraftError(error.message);
        setText(byId('draftSubmitState'), `error=${error.message}`);
    } finally {
        button.disabled = false;
    }
}

async function applyRuntimeDraft(draftId) {
    if (!state.runtimeConfig || !state.configSchema) {
        renderDraftError('运行时快照尚未加载');
        return;
    }
    const button = byId('draftHistoryRows').querySelector(
        `button[data-runtime-apply="${draftId}"]`,
    );
    if (button) button.disabled = true;
    setText(byId('draftSubmitState'), 'applying=true');
    try {
        const response = await fetchJson(`${API}/runtime-config/apply`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Admin-Intent': 'apply-runtime-config',
                'Idempotency-Key': runtimeIdempotencyKey('runtime-apply'),
            },
            body: JSON.stringify({
                draft_id: draftId,
                expected_version: state.runtimeConfig.version,
            }),
        });
        renderRuntimeConfig(response);
        renderRuntimeActionResult('apply', response.version.slice(0, 12));
        setText(byId('draftSubmitState'), `applied=${response.version.slice(0, 12)}`);
        await loadRuntime({ refreshDrafts: true });
    } catch (error) {
        renderDraftError(error.message);
        setText(byId('draftSubmitState'), `error=${error.message}`);
    } finally {
        if (button) button.disabled = false;
    }
}

async function rollbackRuntimeConfig() {
    const runtimeConfig = state.runtimeConfig;
    if (!runtimeConfig || !runtimeConfig.rollback_version) {
        renderDraftError('当前没有可回滚的上一运行时快照');
        return;
    }
    const button = byId('runtimeRollbackButton');
    button.disabled = true;
    setText(byId('draftSubmitState'), 'rolling_back=true');
    try {
        const response = await fetchJson(`${API}/runtime-config/rollback`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Admin-Intent': 'rollback-runtime-config',
                'Idempotency-Key': runtimeIdempotencyKey('runtime-rollback'),
            },
            body: JSON.stringify({
                expected_version: runtimeConfig.version,
                target_version: runtimeConfig.rollback_version,
                reason: '管理员界面请求恢复上一运行时版本',
            }),
        });
        renderRuntimeConfig(response);
        renderRuntimeActionResult('rollback', response.version.slice(0, 12));
        setText(byId('draftSubmitState'), `rolled_back=${response.version.slice(0, 12)}`);
        await loadRuntime({ refreshDrafts: true });
    } catch (error) {
        renderDraftError(error.message);
        setText(byId('draftSubmitState'), `error=${error.message}`);
    } finally {
        button.disabled = !state.runtimeConfig || !state.runtimeConfig.rollback_version;
    }
}

function isExactRoute(value, routes) {
    return value === '' || new Set(routes).has(value);
}

function renderRoutes() {
    // route 选项来自后端返回的精确白名单；用户不能通过输入框构造任意 URL 路径。
    const input = byId('logRoute');
    const current = input.value;
    const options = [...state.routes].sort().map((route) => {
        const option = document.createElement('option');
        option.value = route;
        return option;
    });
    byId('logRouteOptions').replaceChildren(...options);
    input.value = current;
    validateLogRoute();
}

function validateLogRoute({ report = false } = {}) {
    // 前端只做即时体验校验，最终 route 合法性仍由服务端再次确认。
    const input = byId('logRoute');
    const value = input.value.trim();
    const valid = isExactRoute(value, state.routes);
    input.setCustomValidity(valid ? '' : '请选择列表中的精确接口路径');
    if (report && !valid) input.reportValidity();
    return valid;
}

async function loadRuntime({ refreshDrafts = false } = {}) {
    // 一次刷新并行读取 status/metrics/config；失败时保留上一批指标，便于区分数据
    // 故障与瞬时网络错误。需要时再刷新草稿工作区，避免每次轮询触发额外控制面读取。
    const button = byId('refreshButton');
    button.disabled = true;
    setText(byId('refreshState'), 'loading=true');
    try {
        const windowSeconds = byId('metricWindow').value;
        const [status, metrics, config] = await Promise.all([
            fetchJson(`${API}/status`),
            fetchJson(`${API}/metrics?window_seconds=${encodeURIComponent(windowSeconds)}`),
            fetchJson(`${API}/config`),
        ]);
        state.metrics = metrics;
        renderStatus(status);
        renderMetricsIdentity(metrics);
        renderSignals();
        renderInstances();
        renderBulkheads();
        renderDependencies();
        renderRouteRates();
        renderOutcomes();
        renderConfig(config);
        if (refreshDrafts && state.draftsEnabled) await loadDraftWorkspace();
        document.querySelectorAll('[data-window-label]').forEach((node) => {
            setText(node, `window=${windowSeconds}s`);
        });
        setText(byId('refreshState'), `last_refresh=${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`);
        byId('refreshState').classList.remove('updated');
        requestAnimationFrame(() => byId('refreshState').classList.add('updated'));
    } catch (error) {
        setText(byId('refreshState'), `error=${error.message}`);
    } finally {
        button.disabled = false;
    }
}

function currentLogQuery() {
    // 读取控件的原始值，真正的格式、权限和 include_system 边界校验仍在服务端完成。
    return {
        window_seconds: byId('logWindow').value,
        request_id: byId('logRequestId').value.trim(),
        route: byId('logRoute').value.trim(),
        code: byId('logCode').value.trim(),
        include_system: byId('includeSystem').checked,
    };
}

function logParams(query, cursor) {
    // 只发送非空筛选和 opaque cursor；分页大小固定为 50，服务端仍有更高上限保护。
    const includeSystem = query.include_system == null ? true : query.include_system;
    const params = new URLSearchParams({
        window_seconds: query.window_seconds,
        limit: '50',
        include_system: String(includeSystem),
    });
    ['request_id', 'route', 'code'].forEach((key) => {
        if (query[key]) params.set(key, query[key]);
    });
    if (cursor) params.set('cursor', cursor);
    return params;
}

function resetLogPaging() {
    // 筛选变化会使旧 cursor 失效；revision 同时让尚未返回的旧 fetch 安静丢弃。
    state.logRevision += 1;
    state.logCursor = null;
    state.logQuery = null;
    byId('nextLogsButton').hidden = true;
    setText(byId('logSummary'), 'query_dirty=true');
}

async function loadLogs({ append = false } = {}) {
    // 首页替换结果，后续页追加结果；后端 cursor 已绑定窗口和筛选指纹，前端不解析它。
    if (append && (!state.logCursor || !state.logQuery)) return;
    if (!append && !validateLogRoute({ report: true })) return;
    const query = append ? state.logQuery : currentLogQuery();
    const revision = state.logRevision;
    setText(byId('logSummary'), 'loading=true');
    try {
        const response = await fetchJson(`${API}/logs?${logParams(query, append ? state.logCursor : null)}`);
        if (revision !== state.logRevision) return;
        state.logScope = response.scope;
        const rows = response.items.map(logRow);
        if (append) byId('logRows').append(...rows);
        else byId('logRows').replaceChildren(...(rows.length ? rows : [emptyTableRow('matched=0', 6)]));
        state.logQuery = query;
        state.logCursor = response.next_cursor;
        byId('nextLogsButton').hidden = !state.logCursor;
        setText(
            byId('logSummary'),
            `returned=${response.items.length} · scanned=${response.scanned_count} · scope=${response.scope} · include_system=${query.include_system} · truncated=${response.truncated} · available=${response.available}`,
        );
    } catch (error) {
        if (revision !== state.logRevision) return;
        if (!append) byId('logRows').replaceChildren(emptyTableRow('query_failed=true', 6));
        setText(byId('logSummary'), `error=${error.message}`);
    }
}

function logRow(item) {
    // 同一行兼容 HTTP、dependency、admin action 等事件的可选字段，但不展示原始 message。
    const row = document.createElement('tr');
    const operation = item.route
        ? [item.method, item.route].filter(Boolean).join(' ')
        : [item.dependency, item.operation].filter(Boolean).join(' / ') || item.action || '—';
    const status = item.status != null
        ? `${item.status} / ${item.code || 0}`
        : item.result || item.outcome || '—';
    row.append(
        tableCell(formatTime(item.timestamp)),
        tableCell(item.event),
        requestIdCell(item.request_id),
        tableCell(operation),
        tableCell(status),
        tableCell(item.elapsed_ms != null ? formatNumber(item.elapsed_ms, 1) : '—', { number: true }),
    );
    if (item.request_id) {
        row.className = 'log-row-action';
        row.dataset.requestId = item.request_id;
    }
    return row;
}

function requestIdCell(requestId) {
    // Request ID 作为可点击筛选入口展示，但仍通过 textContent 写入，避免日志字段进入 HTML。
    if (!requestId) return tableCell('—');
    const cell = document.createElement('td');
    const button = element('button', 'request-id-action', requestId);
    button.type = 'button';
    button.setAttribute('aria-label', `使用 Request ID ${requestId} 筛选日志`);
    cell.append(button);
    return cell;
}

function fillLogRequestIdFromTarget(target, input = byId('logRequestId')) {
    const row = target.closest('tr[data-request-id]');
    if (!row) return false;
    input.value = row.dataset.requestId;
    return true;
}

function useLogRowRequestId(target) {
    if (!fillLogRequestIdFromTarget(target)) return false;
    resetLogPaging();
    loadLogs();
    return true;
}

function refreshAll() {
    resetLogPaging();
    loadRuntime({ refreshDrafts: true });
    loadLogs();
}

function initializeDashboard() {
    // 事件绑定集中在这里；30 秒轮询只在页面可见时刷新，手动筛选会重置分页状态。
    byId('refreshButton').addEventListener('click', refreshAll);
    byId('metricWindow').addEventListener('change', loadRuntime);
    byId('logForm').addEventListener('submit', (event) => {
        event.preventDefault();
        resetLogPaging();
        loadLogs();
    });
    byId('nextLogsButton').addEventListener('click', () => loadLogs({ append: true }));
    byId('configDraftForm').addEventListener('submit', submitConfigDraft);
    byId('refreshDraftsButton').addEventListener('click', loadDraftWorkspace);
    byId('draftHistoryRows').addEventListener('click', (event) => {
        if (!(event.target instanceof Element)) return;
        const button = event.target.closest('button[data-runtime-apply]');
        if (button) applyRuntimeDraft(button.dataset.runtimeApply);
    });
    byId('runtimeRollbackButton').addEventListener('click', rollbackRuntimeConfig);
    ['logWindow', 'logRequestId', 'logCode', 'includeSystem'].forEach((id) => {
        const eventName = id === 'logWindow' || id === 'includeSystem' ? 'change' : 'input';
        byId(id).addEventListener(eventName, resetLogPaging);
    });
    byId('logRoute').addEventListener('input', () => {
        validateLogRoute();
        resetLogPaging();
    });
    byId('logRows').addEventListener('click', (event) => useLogRowRequestId(event.target));
    loadRuntime({ refreshDrafts: true });
    loadLogs();
    window.setInterval(() => { if (!document.hidden) loadRuntime(); }, 30000);
}

if (typeof document !== 'undefined') initializeDashboard();
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        CORE_CONFIG_KEYS,
        coreConfigItems,
        draftCandidateValues,
        draftDiffSummary,
        draftHistoryRefreshState,
        fillLogRequestIdFromTarget,
        groupDependencyCallRates,
        instanceModels,
        isExactRoute,
        logParams,
        runtimeDraftCanApply,
        runtimeOperationSummary,
        summarizeOutcomeRates,
    };
}
