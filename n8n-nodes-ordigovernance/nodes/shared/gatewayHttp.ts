/**
 * Shared HTTP and polling helpers for Ordigovernance gateway nodes.
 * Error messages follow the "[status] body-snippet" convention so that
 * gateway failures surface readably in the n8n UI instead of raw stacks.
 */

export interface GatewayCredentials {
	baseUrl: string;
	token: string;
}

export interface GatewayRequestOptions {
	method: 'GET' | 'POST';
	path: string;
	body?: Record<string, unknown>;
	timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 30000;
const SNIPPET_LIMIT = 500;

export async function gatewayRequest<T extends Record<string, unknown>>(
	credentials: GatewayCredentials,
	options: GatewayRequestOptions,
): Promise<T> {
	const baseUrl = credentials.baseUrl.replace(/\/+$/, '');
	const url = `${baseUrl}${options.path}`;
	const controller = new AbortController();
	const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? DEFAULT_TIMEOUT_MS);

	let response: Response;
	try {
		response = await fetch(url, {
			method: options.method,
			headers: {
				Authorization: `Bearer ${credentials.token}`,
				'Content-Type': 'application/json',
			},
			body: options.body === undefined ? undefined : JSON.stringify(options.body),
			signal: controller.signal,
		});
	} catch (error) {
		const reason = error instanceof Error ? error.message : String(error);
		throw new Error(`gateway request ${options.method} ${options.path} failed: ${reason}`);
	} finally {
		clearTimeout(timeout);
	}

	const text = await response.text();
	if (!response.ok) {
		throw new Error(
			`gateway request ${options.method} ${options.path} failed [${response.status}]: ${text.slice(0, SNIPPET_LIMIT)}`,
		);
	}
	if (!text) return {} as T;
	try {
		return JSON.parse(text) as T;
	} catch {
		throw new Error(
			`gateway request ${options.method} ${options.path} returned a non-JSON response`,
		);
	}
}

export interface PollOptions {
	intervalMs: number;
	timeoutMs: number;
}

export async function pollUntil<T>(
	fetchOnce: () => Promise<T>,
	isTerminal: (value: T) => boolean,
	options: PollOptions,
	describe: string,
): Promise<T> {
	const deadline = Date.now() + options.timeoutMs;
	for (;;) {
		const value = await fetchOnce();
		if (isTerminal(value)) return value;
		if (Date.now() >= deadline) {
			throw new Error(`${describe} did not reach a terminal state within ${options.timeoutMs}ms`);
		}
		await new Promise((resolve) => setTimeout(resolve, options.intervalMs));
	}
}