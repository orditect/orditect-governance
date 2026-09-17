/**
 * TrackedChatModel: BaseChatModel shell over the ordigovernance gateway.
 *
 * Every invocation lands as one governed call through
 * POST /governed/llm-chat: semaphore, budget, audit and call_id
 * idempotency all apply on the gateway side. The shell translates
 * LangChain message objects into OpenAI-shaped dicts -- assistant
 * tool_calls and tool messages included, in whichever shape the
 * middleware left them (the additional_kwargs fallback mirrors the
 * Python bridge's _raw_tool_calls discipline) -- and translates the
 * response's OpenAI tool_calls back into LangChain ToolCall dicts,
 * the shape ToolNode dispatches on. Bound tools / tool_choice / stop /
 * extra kwargs are forwarded verbatim on every call; the unbound
 * model never carries tool specs (binding goes through
 * RunnableBinding, so those options exist only on the bound clone).
 */

import type { CallbackManagerForLLMRun } from '@langchain/core/callbacks/manager';
import {
	BaseChatModel,
	type BaseChatModelCallOptions,
	type BaseChatModelParams,
	type BindToolsInput,
} from '@langchain/core/language_models/chat_models';
import {
	AIMessage,
	isAIMessage,
	isToolMessage,
	type BaseMessage,
} from '@langchain/core/messages';
import type { ToolCall } from '@langchain/core/messages/tool';
import type { ChatResult } from '@langchain/core/outputs';
import type { Runnable, RunnableConfig } from '@langchain/core/runnables';
import { convertToOpenAITool } from '@langchain/core/utils/function_calling';

const ROLE_MAP: Record<string, string> = {
	human: 'user',
	ai: 'assistant',
	system: 'system',
	tool: 'tool',
	function: 'tool',
};

/** Call options that configure the transport, never the request body. */
const TRANSPORT_OPTION_KEYS = new Set([
	'callbacks',
	'signal',
	'tags',
	'metadata',
	'runName',
	'runManager',
	'timeout',
	'configurable',
	'recursionLimit',
]);

export interface GatewayModelConfig {
	baseUrl: string;
	token: string;
	client: string;
	purpose: string;
	runId?: string;
	taskId?: string;
	/** Per-request transport timeout in seconds (default: 300). */
	timeout?: number;
}

export function messageContentToText(content: BaseMessage['content']): string {
	if (typeof content === 'string') return content;
	return content
		.filter(
			(part): part is { type: 'text'; text: string } =>
				typeof part === 'object' && part !== null && (part as { type?: string }).type === 'text',
		)
		.map((part) => part.text)
		.join('');
}

function rawToolCallsOf(message: AIMessage): unknown[] | undefined {
	// Mirror of the Python bridge's _raw_tool_calls: middleware chains may
	// leave raw OpenAI entries only under additional_kwargs['tool_calls'];
	// those pass through untouched. Normalized LangChain ToolCall dicts are
	// translated back to the wire shape. Dropping either shape makes the
	// next round's history reference tool_call_ids the assistant message
	// never declared, and strict endpoints reject the request with a 400.
	const raw = message.additional_kwargs?.tool_calls as unknown[] | undefined;
	if (Array.isArray(raw) && raw.length > 0) return raw;
	const calls = message.tool_calls;
	if (!calls || calls.length === 0) return undefined;
	return calls.map((call, index) => ({
		id: call.id ?? `call_${index}`,
		type: 'function',
		function: {
			name: call.name,
			arguments: JSON.stringify(call.args ?? {}),
		},
	}));
}

function toOpenAiToolSpec(tool: unknown): unknown {
	// bindTools already converted these to the endpoint-native shape;
	// pass wire-shaped entries through untouched, convert everything else.
	if (
		tool &&
		typeof tool === 'object' &&
		(tool as { type?: unknown }).type === 'function' &&
		'function' in tool
	) {
		return tool;
	}
	return convertToOpenAITool(tool as Parameters<typeof convertToOpenAITool>[0]);
}


export function toOpenAiMessages(messages: BaseMessage[]): Array<Record<string, unknown>> {
	const out: Array<Record<string, unknown>> = [];
	for (const message of messages) {
		const content = messageContentToText(message.content);
		if (isAIMessage(message)) {
			const entry: Record<string, unknown> = { role: 'assistant', content };
			const rawCalls = rawToolCallsOf(message);
			if (rawCalls) entry.tool_calls = rawCalls;
			out.push(entry);
		} else if (isToolMessage(message)) {
			out.push({ role: 'tool', content, tool_call_id: message.tool_call_id });
		} else {
			const type = message._getType();
			out.push({ role: ROLE_MAP[type] ?? type, content });
		}
	}
	return out;
}

function toLangChainToolCalls(raw: unknown): ToolCall[] {
	if (!Array.isArray(raw)) return [];
	const out: ToolCall[] = [];
	raw.forEach((call, index) => {
		if (call && typeof call === 'object' && 'function' in call) {
			const record = call as {
				id?: string;
				function?: { name?: string; arguments?: unknown };
			};
			const fn = record.function ?? {};
			let args = fn.arguments ?? {};
			if (typeof args === 'string') {
				try {
					args = JSON.parse(args || '{}');
				} catch {
					args = { __arg1: args };
				}
			}
			if (typeof args !== 'object' || args === null || Array.isArray(args)) {
				args = { __arg1: args };
			}
			out.push({
				name: fn.name ?? '',
				args: args as Record<string, unknown>,
				id: record.id ?? `call_${index}`,
				type: 'tool_call',
			});
		} else if (call && typeof call === 'object' && 'name' in call) {
			const record = call as { id?: string; name: string; args?: Record<string, unknown> };
			out.push({
				name: record.name,
				args: record.args ?? {},
				id: record.id ?? `call_${index}`,
				type: 'tool_call',
			});
		}
	});
	return out;
}

export function toAiMessage(response: unknown): AIMessage {
	const record = (response ?? {}) as {
		choices?: Array<{ message?: Record<string, unknown> }>;
		usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
	};
	const choice = (record.choices ?? [])[0] ?? {};
	const message = choice.message ?? {};
	const usage = record.usage;
	return new AIMessage({
		content: (message.content as string) ?? '',
		tool_calls: toLangChainToolCalls(message.tool_calls),
		usage_metadata: usage
			? {
					input_tokens: usage.prompt_tokens ?? 0,
					output_tokens: usage.completion_tokens ?? 0,
					total_tokens: usage.total_tokens ?? 0,
				}
			: undefined,
	});
}

interface GatewayChatResponse {
	status: string;
	call_id: string;
	response: unknown;
	usage?: Record<string, unknown>;
}

async function postGovernedChat(
	config: GatewayModelConfig,
	payload: Record<string, unknown>,
): Promise<GatewayChatResponse> {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), (config.timeout ?? 300) * 1000);
	let response: Response;
	try {
		response = await fetch(`${config.baseUrl.replace(/\/+$/, '')}/governed/llm-chat`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				Authorization: `Bearer ${config.token}`,
			},
			body: JSON.stringify(payload),
			signal: controller.signal,
		});
	} catch (error) {
		throw new Error(
			`ordigovernance gateway unreachable at ${config.baseUrl}: ${(error as Error).message}`,
		);
	} finally {
		clearTimeout(timer);
	}
	const text = await response.text();
	if (!response.ok) {
		// Surface the gateway's structured error readably in the n8n UI:
		// 422 vocabulary lists, 409 budget/quota denials, 504 timeouts.
		throw new Error(`governed chat failed [${response.status}]: ${text}`);
	}
	return JSON.parse(text) as GatewayChatResponse;
}

export class TrackedChatModel extends BaseChatModel {
	private readonly gateway: GatewayModelConfig;

		constructor(fields: GatewayModelConfig & BaseChatModelParams) {
		// _llmType is invoked during super() before this.gateway can be
		// assigned, so the config is stashed on the instance first.
		super(fields);
		this.gateway = {
			baseUrl: fields.baseUrl,
			token: fields.token,
			client: fields.client,
			purpose: fields.purpose,
			runId: fields.runId,
			taskId: fields.taskId,
			timeout: fields.timeout,
		};
	}

	_llmType(): string {
		return `tracked-${this.gateway?.client ?? 'unknown'}`;
	}


	bindTools(
		tools: BindToolsInput[],
		kwargs?: Partial<BaseChatModelCallOptions>,
	): Runnable {
		// n8n's Tools Agent probes `typeof model.bindTools === 'function'`
		// before accepting a chat model; without this method the agent
		// rejects the node with "requires Chat Model which supports Tools
		// calling". withConfig produces a RunnableBinding that merges these
		// options into every downstream invocation; _generate forwards them
		// to the gateway verbatim on every governed call.
		return this.withConfig({
			tools: tools.map((tool) => toOpenAiToolSpec(tool)),
			...kwargs,
		} as unknown as RunnableConfig);
	}
	async _generate(
		messages: BaseMessage[],
		options: BaseChatModelCallOptions,
		_runManager?: CallbackManagerForLLMRun,
	): Promise<ChatResult> {
		// Forward every call option the framework binds onto the model:
		// tool specs (converted to the endpoint-native shape), tool_choice,
		// stop, and any extra bind kwargs ride along on every governed call.
		// Dropping them changes model behavior silently (pitfalls 14.2).
		const requestKwargs: Record<string, unknown> = {};
		for (const [key, value] of Object.entries(options)) {
			if (value === undefined || TRANSPORT_OPTION_KEYS.has(key)) continue;
			if (key === 'tools') {
				requestKwargs.tools = (value as unknown[]).map((tool) => toOpenAiToolSpec(tool));
			} else {
				requestKwargs[key] = value;
			}
		}

		const payload: Record<string, unknown> = {
			client: this.gateway.client,
			purpose: this.gateway.purpose,
			messages: toOpenAiMessages(messages),
			kwargs: requestKwargs,
		};
		if (this.gateway.runId) payload.run_id = this.gateway.runId;
		if (this.gateway.taskId) payload.task_id = this.gateway.taskId;

		const body = await postGovernedChat(this.gateway, payload);
		const message = toAiMessage(body.response);
		return {
			generations: [
				{
					text: messageContentToText(message.content),
					message,
				},
			],
			llmOutput: { usage: body.usage, call_id: body.call_id },
		};
	}
}