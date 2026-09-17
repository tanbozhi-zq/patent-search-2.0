'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const {
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
} = require('../app/static/admin/admin.js');

test('draft candidates include only selected finite values and preserve numeric types', () => {
    assert.deepEqual(
        draftCandidateValues([
            { selected: true, key: 'opensearch.max_retries', value: '0', valueType: 'integer' },
            { selected: true, key: 'request.deadline_seconds', value: '300.5', valueType: 'number' },
            { selected: false, key: 'bulkhead.global_capacity', value: '8', valueType: 'integer' },
        ]),
        {
            'opensearch.max_retries': 0,
            'request.deadline_seconds': 300.5,
        },
    );
    assert.throws(
        () => draftCandidateValues([
            { selected: true, key: 'opensearch.max_retries', value: '0.5', valueType: 'integer' },
        ]),
        /需要整数/,
    );
});

test('draft diff summary is deterministic and does not imply that values applied', () => {
    assert.equal(draftDiffSummary({}), 'diff=0');
    assert.equal(
        draftDiffSummary({
            'opensearch.max_retries': { old: 1, new: 0 },
            'request.deadline_seconds': { old: 240, new: 300 },
        }),
        'opensearch.max_retries: 1 → 0 · request.deadline_seconds: 240 → 300',
    );
});

test('only a current validated runtime-reload draft receives an apply action', () => {
    const runtime = { writes_enabled: true, version: 'runtime-version-current' };
    const baseDraft = {
        status: 'validated',
        baseline_fingerprint: 'runtime-version-current',
        diff: {
            'opensearch.max_retries': { apply_mode: 'runtime_reload' },
        },
    };

    assert.equal(runtimeDraftCanApply(baseDraft, runtime), true);
    assert.equal(runtimeDraftCanApply({
        ...baseDraft,
        diff: {
            ...baseDraft.diff,
            'opensearch.pool_maxsize': { apply_mode: 'restart_required' },
        },
    }, runtime), false);
    assert.equal(runtimeDraftCanApply({
        ...baseDraft,
        baseline_fingerprint: 'stale-version',
    }, runtime), false);
    assert.equal(runtimeDraftCanApply(baseDraft, { ...runtime, writes_enabled: false }), false);
});

test('runtime audit summary exposes only the operation result and version linkage', () => {
    assert.deepEqual(
        runtimeOperationSummary({
            status: 'failed',
            failure_code: 'verification_failed',
            operation_id: '11111111-1111-4111-8111-111111111111',
            previous_version: 'a'.repeat(64),
            current_version: 'b'.repeat(64),
            idempotency_key: 'SENTINEL_IDEMPOTENCY_KEY',
            values: { 'opensearch.timeout_seconds': 3 },
        }),
        {
            result: 'failed · verification_failed',
            operation: 'operation=11111111-1111-4111-8111-111111111111',
            versions: 'old=aaaaaaaaaaaa · new=bbbbbbbbbbbb',
        },
    );
});

test('a saved draft stays successful when the follow-up history refresh fails', async () => {
    assert.equal(
        await draftHistoryRefreshState('draft-ok', async () => undefined),
        'saved=draft-ok',
    );
    assert.equal(
        await draftHistoryRefreshState('draft-saved', async () => {
            throw new Error('database temporarily busy');
        }),
        'saved=draft-saved · history_refresh_failed=true',
    );
});

test('mixed-version instances are the union of build, start, and probe samples', () => {
    const models = instanceModels(
        [
            {
                labels: {
                    instance: 'instance-new',
                    version: '0.10.0',
                    commit: 'abcdef0',
                },
                value: 1,
            },
        ],
        [
            { labels: { instance: 'instance-new' }, value: 100 },
            { labels: { instance: 'instance-old' }, value: 50 },
        ],
        [
            { labels: { instance: 'instance-new', probe: 'ready' }, value: 1 },
            { labels: { instance: 'instance-old', probe: 'ready' }, value: 0 },
        ],
    );

    assert.deepEqual(models.map((item) => item.name), ['instance-new', 'instance-old']);
    const oldInstance = models.find((item) => item.name === 'instance-old');
    assert.equal(oldInstance.build, null);
    assert.equal(oldInstance.start.value, 50);
    assert.equal(oldInstance.probes.get('ready'), 0);
});

test('log pagination reuses the query snapshot that produced its cursor', () => {
    const querySnapshot = {
        window_seconds: '3600',
        request_id: 'request-original',
        route: '/api/patent/search',
        code: '50301',
    };
    const currentForm = { ...querySnapshot, request_id: 'request-edited' };

    const params = logParams(querySnapshot, 'opaque-cursor');

    assert.equal(params.get('request_id'), 'request-original');
    assert.notEqual(params.get('request_id'), currentForm.request_id);
    assert.equal(params.get('cursor'), 'opaque-cursor');
    assert.equal(params.get('include_system'), 'true');
});

test('log query explicitly preserves the business-only system activity choice', () => {
    const params = logParams(
        {
            window_seconds: '900',
            request_id: '',
            route: '',
            code: '',
            include_system: false,
        },
        null,
    );

    assert.equal(params.get('include_system'), 'false');
    assert.equal(params.get('window_seconds'), '900');
    assert.equal(params.get('limit'), '50');
});

test('outcome summary derives 4xx and 5xx rates without changing metric queries', () => {
    const summary = summarizeOutcomeRates([
        { labels: { status: '200', code: '0' }, value: 1.2 },
        { labels: { status: '400', code: '40002' }, value: 0.03 },
        { labels: { status: '503', code: '50301' }, value: 0.01 },
    ]);

    assert.deepEqual(summary, {
        total: 1.24,
        clientError: 0.03,
        serverError: 0.01,
    });
});

test('dependency call rates stay unknown when that query is unavailable', () => {
    const unavailable = groupDependencyCallRates({
        available: false,
        samples: [
            { labels: { operation: 'search', outcome: 'success' }, value: 2 },
        ],
    });
    const available = groupDependencyCallRates({
        available: true,
        samples: [
            { labels: { operation: 'search', outcome: 'success' }, value: 2 },
            { labels: { operation: 'search', outcome: 'timeout' }, value: 0.25 },
        ],
    });

    assert.equal(unavailable.has('search'), false);
    assert.deepEqual(available.get('search'), { total: 2.25, failed: 0.25 });
});

test('core config is selected by stable keys in the agreed operator order', () => {
    const items = CORE_CONFIG_KEYS.slice().reverse().map((key) => ({
        key,
        label: `label:${key}`,
        category: 'runtime',
        value: `value:${key}`,
    }));

    assert.deepEqual(
        coreConfigItems(items).map((item) => item.key),
        CORE_CONFIG_KEYS,
    );
    assert.equal(coreConfigItems(items)[0].value, `value:${CORE_CONFIG_KEYS[0]}`);
});

test('interface filter accepts only an empty value or an exact allowlisted path', () => {
    const routes = new Set(['/api/patent/search', '/ready']);

    assert.equal(isExactRoute('', routes), true);
    assert.equal(isExactRoute('/api/patent/search', routes), true);
    assert.equal(isExactRoute('/api/patent/search/', routes), false);
    assert.equal(isExactRoute('/api/patent', routes), false);
});

test('clickable log row writes its exact Request ID back to the filter', () => {
    const input = { value: '' };
    const target = {
        closest(selector) {
            assert.equal(selector, 'tr[data-request-id]');
            return { dataset: { requestId: 'request-from-row' } };
        },
    };

    assert.equal(fillLogRequestIdFromTarget(target, input), true);
    assert.equal(input.value, 'request-from-row');
});
