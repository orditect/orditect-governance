import { BaseChatModel } from '@langchain/core/language_models/chat_models';
import { AIMessage, HumanMessage, SystemMessage, ToolMessage } from '@langchain/core/messages';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
	TrackedChatModel,
	toAiMessage,
	toOpenAiMessages,
} from '../nodes/OrdigovernanceChatModel/TrackedChatModel';

const CONFIG = {
	baseUrl: 'http://gw:8180',
	token: 'test-token',
	client: 'research',
	purpose: 'n8n-chat',
};

const OPENAI_TOOL_SPEC = {
	type: 'function',
	function: {
		name: 'search',
		description: 'web search',
		parameters: { type: 'object', properties: { query: { type: 'string' } } },
	},
};

function okResponse(body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'Content-Type': 'application/json' },
	});
}

describe('message translation', () => {
	it('maps langchain message types to openai roles', () => {
		const dicts = toOpenAiMessages([
			new SystemMessage('sys'),
			new HumanMessage('hi'),
			new AIMessage('hello'),
		]);
		expect(dicts).toEqual([
			{ role: 'system', content: 'sys' },
			{ role: 'user', content: 'hi' },
			{ role: 'assistant', content: 'hello' },
		]);
	});

	it('translates normalized tool calls back to the wire shape', () => {
		const dicts = toOpenAiMessages([
			new AIMessage({
				content: '',
				tool_calls: [{ name: 'search', args: { query: 'ev' }, id: 'call_1', type: 'tool_call' }],
			}),
			new ToolMessage({ content: '{"hits": 3}', tool_call_id: 'call_1' }),
		]);
		expect(dicts[0].tool_calls).toEqual([
			{
				id: 'call_1',
				type: 'function',
				function: { name: 'search', arguments: '{"query":"ev"}' },
			},
		]);
		expect(dicts[1]).toEqual({ role: 'tool', content: '{"hits": 3}', tool_call_id: 'call_1' });
	});

	it('passes raw additional_kwargs tool_calls through untouched', () => {
		const raw = [
			{ id: 'call_9', type: 'function', function: { name: 'search', arguments: '{}' } },
		];
		const dicts = toOpenAiMessages([
			new AIMessage({ content: '', additional_kwargs: { tool_calls: raw } }),
		]);
		expect(dicts[0].tool_calls).toBe(raw);
	});
});

describe('response translation', () => {
	it('maps openai tool_calls to langchain toolcall dicts and usage', () => {
		const message = toAiMessage({
			choices: [
				{
					message: {
						content: '',
						tool_calls: [
							{
								id: 'call_1',
								type: 'function',
								function: { name: 'search', arguments: '{"query": "ev"}' },
							},
						],
					},
				},
			],
			usage: { prompt_tokens: 3, completion_tokens: 2, total_tokens: 5 },
		});
		expect(message.tool_calls).toEqual([
			{ name: 'search', args: { query: 'ev' }, id: 'call_1', type: 'tool_call' },
		]);
		expect(message.usage_metadata).toEqual({
			input_tokens: 3,
			output_tokens: 2,
			total_tokens: 5,
		});
	});
});

describe('governed call contract', () => {
	const fetchMock = vi.fn();

	beforeEach(() => {
		vi.stubGlobal('fetch', fetchMock);
		fetchMock.mockReset();
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('is a BaseChatModel of its own langchain copy', () => {
		// The real acceptance is instanceof against n8n's built-in copy
		// (peer dependency discipline, see README); this pins the class shape.
		expect(new TrackedChatModel(CONFIG)).toBeInstanceOf(BaseChatModel);
	});

	it('forwards bind options, identity and translated messages', async () => {
		fetchMock.mockResolvedValue(
			okResponse({
				status: 'ok',
				call_id: 'n8n-chat-n8n-call-abc-e-abc-1001',
				response: { choices: [{ message: { content: 'ok' } }] },
				usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
			}),
		);
		const model = new TrackedChatModel({ ...CONFIG, runId: 'run-1', taskId: 'task-a' });
		const result = await model._generate([new HumanMessage('hi')], {
			tools: [OPENAI_TOOL_SPEC],
			tool_choice: 'auto',
			stop: ['###'],
			parallel_tool_calls: false,
		} as never);

		expect(fetchMock).toHaveBeenCalledTimes(1);
		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('http://gw:8180/governed/llm-chat');
		expect(init.method).toBe('POST');
		expect((init.headers as Record<string, string>).Authorization).toBe('Bearer test-token');
		const payload = JSON.parse(String(init.body));
		expect(payload.client).toBe('research');
		expect(payload.purpose).toBe('n8n-chat');
		expect(payload.run_id).toBe('run-1');
		expect(payload.task_id).toBe('task-a');
		expect(payload.messages).toEqual([{ role: 'user', content: 'hi' }]);
		expect(payload.kwargs.tools).toEqual([OPENAI_TOOL_SPEC]);
		expect(payload.kwargs.tool_choice).toBe('auto');
		expect(payload.kwargs.stop).toEqual(['###']);
		expect(payload.kwargs.parallel_tool_calls).toBe(false);

		expect(result.generations[0].message.content).toBe('ok');
		expect(result.llmOutput).toEqual({
			usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
			call_id: 'n8n-chat-n8n-call-abc-e-abc-1001',
		});
	});

	it('omits run/task identity and tools when unbound', async () => {
		fetchMock.mockResolvedValue(
			okResponse({
				status: 'ok',
				call_id: 'cid',
				response: { choices: [{ message: { content: 'ok' } }] },
			}),
		);
		const model = new TrackedChatModel(CONFIG);
		await model._generate([new HumanMessage('hi')], {} as never);
		const payload = JSON.parse(String((fetchMock.mock.calls[0] as [string, RequestInit])[1].body));
		expect('run_id' in payload).toBe(false);
		expect('task_id' in payload).toBe(false);
		expect('tools' in payload.kwargs).toBe(false);
	});

	it('surfaces gateway errors readably', async () => {
		fetchMock.mockResolvedValue(
			new Response(
				JSON.stringify({ detail: "unknown llm client 'nope'; registered: ['research']" }),
				{ status: 422 },
			),
		);
		const model = new TrackedChatModel(CONFIG);
		await expect(model._generate([new HumanMessage('hi')], {} as never)).rejects.toThrow(
			/\[422\].*unknown llm client/,
		);
	});

	it('surfaces an unreachable gateway readably', async () => {
		fetchMock.mockRejectedValue(new Error('fetch failed'));
		const model = new TrackedChatModel(CONFIG);
		await expect(model._generate([new HumanMessage('hi')], {} as never)).rejects.toThrow(
			/gateway unreachable at http:\/\/gw:8180/,
		);
	});
});