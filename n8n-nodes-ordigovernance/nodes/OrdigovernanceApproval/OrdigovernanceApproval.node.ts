import type {
	IDataObject,
	IExecuteFunctions,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
} from 'n8n-workflow';
import { NodeOperationError } from 'n8n-workflow';
import { gatewayRequest, pollUntil, type GatewayCredentials } from '../shared/gatewayHttp';
import { asTrimmedString } from '../shared/nodeParams';

const CREDENTIAL_TYPE = 'ordigovernanceApi';

// Gateway HITL contract (docs/n8n-bridge-design.md, decisions D5/D8/D14):
//   POST /runs/{id}/hitl/pause   {task_id}
//   POST /runs/{id}/hitl/resume  {root_id}
//   POST /runs/{id}/hitl/retry   {task_id}
//   GET  /runs/{id}/hitl/receipt/{action_id}   (404 = pending, dual-receipt discipline)
const pathHitlAction = (runId: string, action: 'pause' | 'resume' | 'retry'): string =>
	`/runs/${encodeURIComponent(runId)}/hitl/${action}`;
const pathHitlReceipt = (runId: string, actionId: string): string =>
	`/runs/${encodeURIComponent(runId)}/hitl/receipt/${encodeURIComponent(actionId)}`;
const pathTask = (runId: string, taskId: string): string =>
	`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}`;

function is404(error: unknown): boolean {
	return error instanceof Error && error.message.includes('[404]');
}

async function getTaskRecordOrNull(
	credentials: GatewayCredentials,
	runId: string,
	taskId: string,
): Promise<Record<string, unknown> | null> {
	try {
		return await gatewayRequest(credentials, { method: 'GET', path: pathTask(runId, taskId) });
	} catch (error) {
		// D14: hot reads close at finish; a 404 here means the run ended.
		if (is404(error)) return null;
		throw error;
	}
}

/** Poll the execution receipt of an HITL action; 404 means pending (pitfalls 13.7). */
async function pollHitlReceipt(
	credentials: GatewayCredentials,
	runId: string,
	actionId: string,
	options: { intervalMs: number; timeoutMs: number },
): Promise<Record<string, unknown>> {
	const receipt = await pollUntil(
		async (): Promise<Record<string, unknown> | null> => {
			try {
				return await gatewayRequest<Record<string, unknown>>(credentials, {
					method: 'GET',
					path: pathHitlReceipt(runId, actionId),
				});
			} catch (error) {
				if (is404(error)) return null;
				throw error;
			}
		},
		(value) => value !== null,
		options,
		`receipt ${actionId}`,
	);
	if (receipt === null) {
		throw new Error(`receipt ${actionId} polling returned without a result`);
	}
	return receipt;
}

type Decision =
	| { status: 'approved'; record: Record<string, unknown> }
	| { status: 'rejected'; reason: string };

export class OrdigovernanceApproval implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'Ordigovernance Approval',
		name: 'ordigovernanceApproval',
		icon: 'fa:check-circle',
		group: ['transform'],
		version: 1,
		subtitle: '={{ $parameter["operation"] }}',
		description: 'Drive human-in-the-loop operations on the Ordigovernance gateway',
		defaults: { name: 'Ordigovernance Approval' },
		inputs: ['main'],
		outputs: ['main'],
		credentials: [{ name: CREDENTIAL_TYPE, required: true }],
		properties: [
			{
				displayName: 'Operation',
				name: 'operation',
				type: 'options',
				noDataExpression: true,
				options: [
					{ name: 'Pause Task', value: 'pause', action: 'Pause a task in a run' },
					{ name: 'Resume Tree', value: 'resume', action: 'Resume from a scope root' },
					{ name: 'Retry Task', value: 'retry', action: 'Reopen a terminal task' },
					{
						name: 'Await Decision',
						value: 'awaitDecision',
						action: 'Wait until a paused task is resumed or the run ends',
					},
				],
				default: 'awaitDecision',
			},
			{
				displayName: 'Run ID',
				name: 'runId',
				type: 'string',
				default: '',
				required: true,
				description: 'Identifier of the run the HITL operation is scoped to',
			},
			{
				displayName: 'Task ID',
				name: 'taskId',
				type: 'string',
				default: '',
				required: true,
				displayOptions: { show: { operation: ['pause', 'retry', 'awaitDecision'] } },
				description: 'Task to pause / retry, or the paused task to await a decision for',
			},
			{
				displayName: 'Root ID',
				name: 'rootId',
				type: 'string',
				default: '',
				displayOptions: { show: { operation: ['resume'] } },
				description:
					'Scope root for resume_tree; empty defaults to the run id (the run root)',
			},

			{
				displayName: 'Wait for Execution Receipt',
				name: 'waitForReceipt',
				type: 'boolean',
				default: true,
				displayOptions: { show: { operation: ['pause', 'resume', 'retry'] } },
				description:
					'Whether to poll the execution receipt (404 = pending) after the acceptance receipt',
			},
			{
				displayName: 'Poll Interval (Ms)',
				name: 'pollIntervalMs',
				type: 'number',
				default: 1000,
				description: 'Delay between receipt / decision polls',
			},
			{
				displayName: 'Poll Timeout (Ms)',
				name: 'pollTimeoutMs',
				type: 'number',
				default: 600000,
				description: 'Maximum time to wait before failing',
			},
		],
	};

		async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
		const items = this.getInputData();
		const credentials = (await this.getCredentials(
			CREDENTIAL_TYPE,
		)) as unknown as GatewayCredentials;
		const returnData: INodeExecutionData[] = [];

		for (let itemIndex = 0; itemIndex < items.length; itemIndex++) {
			try {
				const operation = this.getNodeParameter('operation', itemIndex) as string;
				const runId = asTrimmedString(this.getNodeParameter('runId', itemIndex));
				const pollIntervalMs = this.getNodeParameter('pollIntervalMs', itemIndex) as number;
				const pollTimeoutMs = this.getNodeParameter('pollTimeoutMs', itemIndex) as number;
				let response: Record<string, unknown>;

				if (operation === 'awaitDecision') {
					// Mechanism-layer approval semantics: the gateway ships no
					// approval resource (G2/G6), so a human decision is observed
					// through the evidence chain -- a new generation means
					// approved; the run finishing without one means rejected.
					const taskId = asTrimmedString(this.getNodeParameter('taskId', itemIndex));
					const baseline = await getTaskRecordOrNull(credentials, runId, taskId);
					if (baseline === null) {
						response = {
							status: 'rejected',
							task_id: taskId,
							reason: 'run already finished; hot record closed (D14)',
						};
					} else {
						const baselinePrevs = Array.isArray(baseline.previous_execution_ids)
							? baseline.previous_execution_ids.length
							: 0;
						const decision = await pollUntil(
							async (): Promise<Decision | null> => {
								const record = await getTaskRecordOrNull(credentials, runId, taskId);
								if (record === null) {
									return {
										status: 'rejected',
										reason: 'run finished without a new generation',
									};
								}
								const prevs = Array.isArray(record.previous_execution_ids)
									? record.previous_execution_ids.length
									: 0;
								if (prevs > baselinePrevs) return { status: 'approved', record };
								return null;
							},
							(value) => value !== null,
							{ intervalMs: pollIntervalMs, timeoutMs: pollTimeoutMs },
							`approval decision for task ${taskId}`,
						);
						if (decision === null) {
							throw new Error(`approval polling for task ${taskId} returned without a decision`);
						}
						response =
							decision.status === 'approved'
								? {
										status: 'approved',
										task_id: taskId,
										execution_id: decision.record.execution_id,
										previous_execution_ids: decision.record.previous_execution_ids,
									}
								: { status: 'rejected', task_id: taskId, reason: decision.reason };
					}
				} else {
					// The gateway HITL body schema accepts task_id and root_id
					// (both optional, no reason field); send the design-mapped key.
					const body: Record<string, unknown> = {};
					if (operation === 'resume') {
						const rootId = asTrimmedString(this.getNodeParameter('rootId', itemIndex, ''));
						body.root_id = rootId || runId;
					} else {
						body.task_id = asTrimmedString(this.getNodeParameter('taskId', itemIndex));
					}

					const acceptance = await gatewayRequest(credentials, {
						method: 'POST',
						path: pathHitlAction(runId, operation as 'pause' | 'resume' | 'retry'),
						body,
					});

					const waitForReceipt = this.getNodeParameter('waitForReceipt', itemIndex) as boolean;
					if (waitForReceipt) {
						const actionId = String(acceptance.action_id ?? acceptance.id ?? '');
						if (!actionId) {
							throw new Error('gateway acceptance receipt did not contain an action_id');
						}
						const receipt = await pollHitlReceipt(credentials, runId, actionId, {
							intervalMs: pollIntervalMs,
							timeoutMs: pollTimeoutMs,
						});
						response = { acceptance, receipt };
					} else {
						response = { acceptance };
					}
				}

				returnData.push({ json: response as IDataObject, pairedItem: { item: itemIndex } });
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