import type {
	IDataObject,
	IExecuteFunctions,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
} from 'n8n-workflow';
import { NodeOperationError } from 'n8n-workflow';
import { gatewayRequest, pollUntil, type GatewayCredentials } from '../shared/gatewayHttp';
import { asTrimmedString, parseJsonObject } from '../shared/nodeParams';

const CREDENTIAL_TYPE = 'ordigovernanceApi';
const pathSubmitTask = (runId: string): string => `/runs/${encodeURIComponent(runId)}/tasks`;
const pathTask = (runId: string, taskId: string): string =>
	`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}`;
const TERMINAL_TASK_STATUSES = new Set(['succeeded', 'failed', 'cancelled', 'rejected', 'expired']);

function extractTaskId(response: Record<string, unknown>): string {
	const taskId = response.task_id ?? response.id;
	if (typeof taskId !== 'string' || !taskId) {
		throw new Error('gateway response did not contain a task_id');
	}
	return taskId;
}

export class OrdigovernanceTask implements INodeType {
		description: INodeTypeDescription = {
		displayName: 'Ordigovernance Task',
		name: 'ordigovernanceTask',
		icon: 'fa:tasks',
		group: ['transform'],
		version: 1,
		subtitle: '={{ $parameter["impl"] }}',
		description: 'Submit a task to a governed run and optionally wait for its terminal state',
		defaults: { name: 'Ordigovernance Task' },
		inputs: ['main'],
		outputs: ['main'],
		credentials: [{ name: CREDENTIAL_TYPE, required: true }],
		properties: [
			{
				displayName: 'Run ID',
				name: 'runId',
				type: 'string',
				default: '',
				required: true,
				description: 'Identifier of the run the task belongs to',
			},
			{
				displayName: 'Task ID',
				name: 'taskId',
				type: 'string',
				default: '',
				required: true,
				description:
					'Client-generated task identifier (e.g. an expression with a UUID). Required by the gateway for idempotent submission.',
			},
			{
				displayName: 'Impl',
				name: 'impl',
				type: 'string',
				default: '',
				required: true,
				description: 'Name of the registered task implementation to execute',
			},
			{
				displayName: 'Input',
				name: 'taskInput',
				type: 'json',
				default: '{}',
				description: 'JSON input payload for the task',
			},
			{
				displayName: 'Upstream Task IDs',
				name: 'upstream',
				type: 'string',
				default: '',
				description:
					'Comma-separated task ids this task depends on. Recorded as dependency evidence edges (D1); n8n remains the scheduler.',
			},
			{
				displayName: 'Wait for Completion',
				name: 'waitForCompletion',
				type: 'boolean',
				default: true,
				description: 'Whether to poll the gateway until the task reaches a terminal state',
			},
			{
				displayName: 'Poll Interval (Ms)',
				name: 'pollIntervalMs',
				type: 'number',
				default: 2000,
				displayOptions: { show: { waitForCompletion: [true] } },
				description: 'Delay between task status polls',
			},
			{
				displayName: 'Poll Timeout (Ms)',
				name: 'pollTimeoutMs',
				type: 'number',
				default: 120000,
				displayOptions: { show: { waitForCompletion: [true] } },
				description: 'Maximum time to wait for a terminal task state before failing',
			},
		],
	};
	async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
		const items = this.getInputData();
		const credentials = (await this.getCredentials(
			CREDENTIAL_TYPE,
		)) as unknown as GatewayCredentials;
		const returnData: INodeExecutionData[] = [];
		const seenTaskIds = new Set<string>();

		for (let itemIndex = 0; itemIndex < items.length; itemIndex++) {
			try {
				// Trim every id: expression-field whitespace is literal text.
				const runId = asTrimmedString(this.getNodeParameter('runId', itemIndex));
				const taskIdParam = asTrimmedString(this.getNodeParameter('taskId', itemIndex));
				const impl = asTrimmedString(this.getNodeParameter('impl', itemIndex));
				const taskInput = parseJsonObject(
					this.getNodeParameter('taskInput', itemIndex),
					'taskInput',
				);
				const upstream = asTrimmedString(this.getNodeParameter('upstream', itemIndex, ''))
					.split(',')
					.map((entry) => entry.trim())
					.filter(Boolean);
				const waitForCompletion = this.getNodeParameter(
					'waitForCompletion',
					itemIndex,
				) as boolean;

				// D11: one execution per item; auto-suffix colliding evaluated ids.
				let taskId = taskIdParam;
				if (seenTaskIds.has(taskId)) taskId = `${taskIdParam}-${itemIndex}`;
				seenTaskIds.add(taskId);

				// D1: upstream entries become dependency evidence edges.
				// Gateway contract: the impl payload field is "params".
				const body: Record<string, unknown> = { task_id: taskId, impl, params: taskInput };
				if (upstream.length) body.upstream = upstream;

				const submitted = await gatewayRequest(credentials, {
					method: 'POST',
					path: pathSubmitTask(runId),
					body,
				});
				const resolvedTaskId = extractTaskId(submitted);

				let output: Record<string, unknown> = submitted;
				if (waitForCompletion) {
					const pollIntervalMs = this.getNodeParameter('pollIntervalMs', itemIndex) as number;
					const pollTimeoutMs = this.getNodeParameter('pollTimeoutMs', itemIndex) as number;
					const terminal = await pollUntil(
						() =>
							gatewayRequest(credentials, {
								method: 'GET',
								path: pathTask(runId, resolvedTaskId),
							}),
						(task) => TERMINAL_TASK_STATUSES.has(String(task.status)),
						{ intervalMs: pollIntervalMs, timeoutMs: pollTimeoutMs },
						`task ${resolvedTaskId}`,
					);
					output = { ...terminal, task_id: resolvedTaskId };
					// A terminal state is not a success: surface non-succeeded
					// outcomes as node errors so the canvas stops lying.
					if (String(terminal.status) !== 'succeeded') {
						throw new Error(
							`task ${resolvedTaskId} reached terminal status "${String(terminal.status)}"`,
						);
					}
				}

				returnData.push({ json: output as IDataObject, pairedItem: { item: itemIndex } });
			} catch (error) {
				if (this.continueOnFail()) {
					returnData.push({
						json: { error: error instanceof Error ? error.message : String(error) },
						pairedItem: { item: itemIndex },
					});
					continue;
				}
				throw new NodeOperationError(
					this.getNode(),
					error instanceof Error ? error.message : String(error),
					{ itemIndex },
				);
			}
		}

		return [returnData];
	}
}