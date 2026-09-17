import type { IDataObject, IExecuteFunctions, INode, INodeExecutionData } from 'n8n-workflow';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { OrdigovernanceApproval } from '../nodes/OrdigovernanceApproval/OrdigovernanceApproval.node';
import { OrdigovernanceRun } from '../nodes/OrdigovernanceRun/OrdigovernanceRun.node';
import { OrdigovernanceTask } from '../nodes/OrdigovernanceTask/OrdigovernanceTask.node';
import { OrdigovernanceTool } from '../nodes/OrdigovernanceTool/OrdigovernanceTool.node';
import { OrdigovernanceComposite } from '../nodes/OrdigovernanceComposite/OrdigovernanceComposite.node';

const CREDENTIALS = { baseUrl: 'http://gateway.test/', token: 'test-token' };

const NODE: INode = {
	id: 'node-1',
	name: 'Test Node',
	type: 'ordigovernanceTest',
	typeVersion: 1,
	position: [0, 0],
	parameters: {},
};

const fetchMock = vi.fn();

beforeEach(() => {
	fetchMock.mockReset();
	vi.stubGlobal('fetch', fetchMock);
});

function jsonResponse(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' },
	});
}

function lastRequest(): { url: string; init: RequestInit; body: Record<string, unknown> } {
	const [url, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
	return {
		url,
		init,
		body: typeof init.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {},
	};
}

function createContext(
	parameters: Record<string, unknown>,
	items: INodeExecutionData[] = [{ json: {} }],
): IExecuteFunctions {
	const partial = {
		getInputData: () => items,
		getNode: () => NODE,
		getNodeParameter: (name: string, _itemIndex: number, fallback?: unknown) =>
			(parameters[name] ?? fallback) as never,
		getCredentials: async (type: string) => {
			if (type !== 'ordigovernanceApi') {
				throw new Error(`unexpected credential type: ${type}`);
			}
			return CREDENTIALS as unknown as IDataObject;
		},
		continueOnFail: () => false,
	};
	return partial as unknown as IExecuteFunctions;
}

describe('OrdigovernanceRun', () => {
	it('starts a run with auth header and body fields', async () => {
		fetchMock.mockResolvedValue(jsonResponse({ run_id: 'run-1', status: 'running' }));
		const context = createContext({
			operation: 'start',
			client: 'n8n',
			purpose: 'demo',
			metadata: { origin: 'test' },
		});

		const [output] = await new OrdigovernanceRun().execute.call(context);

		const request = lastRequest();
		expect(request.url).toBe('http://gateway.test/runs');
		expect(request.init.method).toBe('POST');
		expect((request.init.headers as Record<string, string>).Authorization).toBe(
			'Bearer test-token',
		);
		expect(request.body).toEqual({ client: 'n8n', purpose: 'demo', metadata: { origin: 'test' } });
		expect(output[0].json).toMatchObject({ run_id: 'run-1' });
	});

	it('finishes a run with the terminal status', async () => {
		fetchMock.mockResolvedValue(jsonResponse({ run_id: 'run-1', status: 'succeeded' }));
		const context = createContext({
			operation: 'finish',
			runId: 'run-1',
			finalStatus: 'succeeded',
			summary: 'done',
		});

		await new OrdigovernanceRun().execute.call(context);

		const request = lastRequest();
		expect(request.url).toBe('http://gateway.test/runs/run-1/finish');
		expect(request.body).toEqual({ status: 'succeeded', summary: 'done' });
	});

		it('cancels the conflicting run and retries when finishExistingOnConflict is set', async () => {
		fetchMock
			.mockResolvedValueOnce(
				jsonResponse({ detail: "a run is already in progress: 'run-stale'" }, 409),
			)
			.mockResolvedValueOnce(jsonResponse({ run_id: 'run-stale', final_status: 'cancelled' }))
			.mockResolvedValueOnce(jsonResponse({ run_id: 'run-new', status: 'running' }));
		const context = createContext({
			operation: 'start',
			client: 'n8n',
			purpose: 'demo',
			metadata: {},
			finishExistingOnConflict: true,
		});

		const [output] = await new OrdigovernanceRun().execute.call(context);

		expect(fetchMock).toHaveBeenCalledTimes(3);
		expect((fetchMock.mock.calls[1] as [string])[0]).toBe(
			'http://gateway.test/runs/run-stale/finish',
		);
		expect(output[0].json).toMatchObject({ run_id: 'run-new' });
	});

	it('propagates 409 without retry when finishExistingOnConflict is off', async () => {
		fetchMock.mockResolvedValue(
			jsonResponse({ detail: "a run is already in progress: 'run-stale'" }, 409),
		);
		const context = createContext({
			operation: 'start',
			client: 'n8n',
			purpose: 'demo',
			metadata: {},
			finishExistingOnConflict: false,
		});

		await expect(new OrdigovernanceRun().execute.call(context)).rejects.toThrow(/\[409\]/);
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});
});

describe('OrdigovernanceTool', () => {
	it('posts a governed tool call with run and task linkage', async () => {
		fetchMock.mockResolvedValue(jsonResponse({ status: 'ok', result: { echoed: true } }));
		const context = createContext({
			client: 'n8n',
			purpose: 'demo',
			toolName: 'search',
			toolInputs: { query: 'hello' },
			runId: 'run-1',
			taskId: 'task-1',
		});

		await new OrdigovernanceTool().execute.call(context);

		const request = lastRequest();
		expect(request.url).toBe('http://gateway.test/governed/tool-call');
		expect(request.body).toEqual({
			client: 'n8n',
			purpose: 'demo',
			tool: 'search',
			inputs: { query: 'hello' },
			run_id: 'run-1',
			task_id: 'task-1',
		});
	});

	it('surfaces a readable error on gateway rejection', async () => {
		fetchMock.mockResolvedValue(jsonResponse({ detail: 'denied by policy' }, 403));
		const context = createContext({
			client: 'n8n',
			purpose: 'demo',
			toolName: 'search',
			toolInputs: {},
			runId: '',
			taskId: '',
		});

		await expect(new OrdigovernanceTool().execute.call(context)).rejects.toThrow(
			/\[403\].*denied by policy/,
		);
	});
});

describe('OrdigovernanceTask', () => {
	it('returns the submit response when not waiting', async () => {
		fetchMock.mockResolvedValue(jsonResponse({ task_id: 'task-1', status: 'pending' }));
		const context = createContext({
			runId: 'run-1',
			taskId: 'task-1',
			impl: 'classify',
			taskInput: { text: 'hi' },
			waitForCompletion: false,
		});

		const [output] = await new OrdigovernanceTask().execute.call(context);

		expect(fetchMock).toHaveBeenCalledTimes(1);
		const request = lastRequest();
		expect(request.url).toBe('http://gateway.test/runs/run-1/tasks');
		expect(request.body).toEqual({ task_id: 'task-1', impl: 'classify', params: { text: 'hi' } });
		expect(output[0].json).toMatchObject({ task_id: 'task-1', status: 'pending' });
	});

	it('polls until the task reaches a terminal state', async () => {
		fetchMock
			.mockResolvedValueOnce(jsonResponse({ task_id: 'task-1', status: 'pending' }))
			.mockResolvedValueOnce(jsonResponse({ task_id: 'task-1', status: 'running' }))
			.mockResolvedValueOnce(jsonResponse({ task_id: 'task-1', status: 'succeeded' }));
		const context = createContext({
			runId: 'run-1',
			taskId: 'task-1',
			impl: 'classify',
			taskInput: {},
			waitForCompletion: true,
			pollIntervalMs: 1,
			pollTimeoutMs: 2000,
		});

		const [output] = await new OrdigovernanceTask().execute.call(context);

		expect(fetchMock).toHaveBeenCalledTimes(3);
		expect((fetchMock.mock.calls[1] as [string])[0]).toBe(
			'http://gateway.test/runs/run-1/tasks/task-1',
		);
		expect(output[0].json).toMatchObject({ task_id: 'task-1', status: 'succeeded' });
	});
		it('sends upstream evidence edges when provided (D1)', async () => {
		fetchMock.mockResolvedValue(jsonResponse({ task_id: 'task-1', status: 'pending' }));
		const context = createContext({
			runId: 'run-1',
			taskId: 'task-1',
			impl: 'classify',
			taskInput: {},
			upstream: 'task-a, task-b',
			waitForCompletion: false,
		});

		await new OrdigovernanceTask().execute.call(context);

		expect(lastRequest().body).toMatchObject({ upstream: ['task-a', 'task-b'] });
	});

	it('auto-suffixes colliding task ids across items (D11)', async () => {
		// Fresh Response per call: a Response body is a one-shot stream.
		fetchMock.mockImplementation(() =>
			Promise.resolve(jsonResponse({ task_id: 'task-1', status: 'pending' })),
		);
		const context = createContext(
			{
				runId: 'run-1',
				taskId: 'task-1',
				impl: 'classify',
				taskInput: {},
				upstream: '',
				waitForCompletion: false,
			},
			[{ json: {} }, { json: {} }],
		);

		await new OrdigovernanceTask().execute.call(context);

		const bodies = fetchMock.mock.calls.map(
			([, init]) => JSON.parse(String((init as RequestInit).body)) as Record<string, unknown>,
		);
		expect(bodies[0].task_id).toBe('task-1');
		expect(bodies[1].task_id).toBe('task-1-1');
	});

	it('trims whitespace from expression-field ids (n8n literal-text pitfall)', async () => {
		fetchMock.mockResolvedValue(jsonResponse({ task_id: 'task-1', status: 'pending' }));
		const context = createContext({
			runId: '   run-1',
			taskId: 'task-1',
			impl: 'classify',
			taskInput: {},
			upstream: '',
			waitForCompletion: false,
		});

		await new OrdigovernanceTask().execute.call(context);

		expect(lastRequest().url).toBe('http://gateway.test/runs/run-1/tasks');
	});
	it('names the impl payload field "params" (silent-drop regression lock)', async () => {
		// The gateway schema accepts params and IGNORES unknown fields;
		// sending "input" instead fails silently as impl defaults.
		fetchMock.mockResolvedValue(jsonResponse({ task_id: 'task-1', status: 'pending' }));
		const context = createContext({
			runId: 'run-1',
			taskId: 'task-1',
			impl: 'classify',
			taskInput: { topic: 'EV batteries' },
			upstream: '',
			waitForCompletion: false,
		});

		await new OrdigovernanceTask().execute.call(context);

		expect(lastRequest().body).toMatchObject({ params: { topic: 'EV batteries' } });
		expect(lastRequest().body).not.toHaveProperty('input');
	});
	it('fails the node when the task settles on a non-succeeded terminal state', async () => {
		fetchMock
			.mockResolvedValueOnce(jsonResponse({ task_id: 'task-1', status: 'pending' }))
			.mockResolvedValueOnce(jsonResponse({ task_id: 'task-1', status: 'failed', result: null }));
		const context = createContext({
			runId: 'run-1',
			taskId: 'task-1',
			impl: 'classify',
			taskInput: {},
			upstream: '',
			waitForCompletion: true,
			pollIntervalMs: 1,
			pollTimeoutMs: 2000,
		});

		await expect(new OrdigovernanceTask().execute.call(context)).rejects.toThrow(
			/terminal status "failed"/,
		);
	});

});

describe('OrdigovernanceApproval', () => {
	it.each([
		['pause', 'http://gateway.test/runs/run-1/hitl/pause'],
		['resume', 'http://gateway.test/runs/run-1/hitl/resume'],
		['retry', 'http://gateway.test/runs/run-1/hitl/retry'],
	])('posts %s to the hitl route', async (operation, expectedUrl) => {
		fetchMock.mockResolvedValue(jsonResponse({ action_id: 'act-1', status: 'accepted' }));
		const context = createContext({
			operation,
			runId: 'run-1',
			taskId: 'task-1',
			waitForReceipt: false,
			pollIntervalMs: 1,
			pollTimeoutMs: 100,
		});

		await new OrdigovernanceApproval().execute.call(context);

		expect(lastRequest().url).toBe(expectedUrl);
	});

	it('sends task_id for pause/retry and root_id defaulting to the run id for resume', async () => {
		// Fresh Response per call: a Response body is a one-shot stream.
		fetchMock.mockImplementation(() =>
			Promise.resolve(jsonResponse({ action_id: 'act-1', status: 'accepted' })),
		);
		const pauseCtx = createContext({
			operation: 'pause',
			runId: 'run-1',
			taskId: 'task-1',
			waitForReceipt: false,
			pollIntervalMs: 1,
			pollTimeoutMs: 100,
		});
		await new OrdigovernanceApproval().execute.call(pauseCtx);
		expect(lastRequest().body).toEqual({ task_id: 'task-1' });

		const resumeCtx = createContext({
			operation: 'resume',
			runId: 'run-1',
			rootId: '',
			waitForReceipt: false,
			pollIntervalMs: 1,
			pollTimeoutMs: 100,
		});
		await new OrdigovernanceApproval().execute.call(resumeCtx);
		expect(lastRequest().body).toEqual({ root_id: 'run-1' });
	});

	it('polls the execution receipt while it 404s (dual-receipt discipline)', async () => {
		fetchMock
			.mockResolvedValueOnce(jsonResponse({ action_id: 'act-1', status: 'accepted' }))
			.mockResolvedValueOnce(jsonResponse({ detail: 'pending' }, 404))
			.mockResolvedValueOnce(jsonResponse({ action_id: 'act-1', status: 'executed', rerun: 1 }));
		const context = createContext({
			operation: 'pause',
			runId: 'run-1',
			taskId: 'task-1',
			waitForReceipt: true,
			pollIntervalMs: 1,
			pollTimeoutMs: 2000,
		});

		const [output] = await new OrdigovernanceApproval().execute.call(context);

		expect(fetchMock).toHaveBeenCalledTimes(3);
		expect((fetchMock.mock.calls[1] as [string])[0]).toBe(
			'http://gateway.test/runs/run-1/hitl/receipt/act-1',
		);
		expect(output[0].json).toMatchObject({
			acceptance: { action_id: 'act-1' },
			receipt: { status: 'executed', rerun: 1 },
		});
	});

	it('awaitDecision resolves approved when a new generation appears', async () => {
		fetchMock
			.mockResolvedValueOnce(
				jsonResponse({
					task_id: 'task-1',
					status: 'cancelled',
					execution_id: 'e-1',
					previous_execution_ids: [],
				}),
			)
			.mockResolvedValueOnce(
				jsonResponse({
					task_id: 'task-1',
					status: 'running',
					execution_id: 'e-2',
					previous_execution_ids: ['e-1'],
				}),
			);
		const context = createContext({
			operation: 'awaitDecision',
			runId: 'run-1',
			taskId: 'task-1',
			pollIntervalMs: 1,
			pollTimeoutMs: 2000,
		});

		const [output] = await new OrdigovernanceApproval().execute.call(context);

		expect(output[0].json).toMatchObject({ status: 'approved', execution_id: 'e-2' });
	});

	it('awaitDecision resolves rejected when the run finishes (hot read 404, D14)', async () => {
		fetchMock
			.mockResolvedValueOnce(
				jsonResponse({
					task_id: 'task-1',
					status: 'cancelled',
					execution_id: 'e-1',
					previous_execution_ids: [],
				}),
			)
			.mockResolvedValueOnce(
				jsonResponse({ detail: 'historical evidence belongs to the viewer' }, 404),
			);
		const context = createContext({
			operation: 'awaitDecision',
			runId: 'run-1',
			taskId: 'task-1',
			pollIntervalMs: 1,
			pollTimeoutMs: 2000,
		});

		const [output] = await new OrdigovernanceApproval().execute.call(context);

		expect(output[0].json).toMatchObject({ status: 'rejected' });
	});
});

describe('OrdigovernanceComposite', () => {
	it('starts a composite with name and params', async () => {
		fetchMock.mockResolvedValue(jsonResponse({ composite_id: 'cmp-1', status: 'running' }));
		const context = createContext({
			runId: 'run-1',
			compositeName: 'quality_gate_pair',
			compositeParams: { producer_id: 'writer-9', threshold: 80 },
			waitForCompletion: false,
		});

		const [output] = await new OrdigovernanceComposite().execute.call(context);

		const request = lastRequest();
		expect(request.url).toBe('http://gateway.test/runs/run-1/composites');
		expect(request.body).toEqual({
			name: 'quality_gate_pair',
			params: { producer_id: 'writer-9', threshold: 80 },
		});
		expect(output[0].json).toMatchObject({ composite_id: 'cmp-1' });
	});

	it('polls until the composite reaches a terminal state and returns the outcome', async () => {
		fetchMock
			.mockResolvedValueOnce(jsonResponse({ composite_id: 'cmp-1', status: 'running' }))
			.mockResolvedValueOnce(jsonResponse({ composite_id: 'cmp-1', status: 'running' }))
			.mockResolvedValueOnce(
				jsonResponse({
					composite_id: 'cmp-1',
					status: 'succeeded',
					children: ['writer-9', 'reviewer-9'],
					outcome: { passed: true, iterations: 2, scores: [62, 88] },
				}),
			);
		const context = createContext({
			runId: 'run-1',
			compositeName: 'quality_gate_pair',
			compositeParams: {},
			waitForCompletion: true,
			pollIntervalMs: 1,
			pollTimeoutMs: 2000,
		});

		const [output] = await new OrdigovernanceComposite().execute.call(context);

		expect(fetchMock).toHaveBeenCalledTimes(3);
		expect((fetchMock.mock.calls[1] as [string])[0]).toBe(
			'http://gateway.test/runs/run-1/composites/cmp-1',
		);
		expect(output[0].json).toMatchObject({
			status: 'succeeded',
			outcome: { passed: true, iterations: 2 },
		});
	});

	it('fails the node when the composite settles on a non-succeeded terminal state', async () => {
		fetchMock
			.mockResolvedValueOnce(jsonResponse({ composite_id: 'cmp-1', status: 'running' }))
			.mockResolvedValueOnce(jsonResponse({ composite_id: 'cmp-1', status: 'failed' }));
		const context = createContext({
			runId: 'run-1',
			compositeName: 'quality_gate_pair',
			compositeParams: {},
			waitForCompletion: true,
			pollIntervalMs: 1,
			pollTimeoutMs: 2000,
		});

		await expect(new OrdigovernanceComposite().execute.call(context)).rejects.toThrow(
			/terminal status "failed"/,
		);
	});
});